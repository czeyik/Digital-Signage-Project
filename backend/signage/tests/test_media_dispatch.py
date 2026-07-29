import uuid
from datetime import timedelta
from io import BytesIO, StringIO
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from PIL import Image

from signage.management.commands.process_media import Command as ProcessMediaCommand
from signage.management.commands.reconcile_media_processing import (
    Command as ReconcileMediaCommand,
)
from signage.media_dispatch import dispatch_media_processing, queue_media_processing
from signage.models import Alert, MediaAsset, User
from signage.services import delete_media_binary, inspect_media

ECS_DISPATCH_SETTINGS = {
    "DEPLOYMENT_ENV": "production",
    "MEDIA_PROCESSING_DISPATCH_BACKEND": "ecs",
    "MEDIA_MAX_DISPATCH_ATTEMPTS": 5,
    "MEDIA_DISPATCH_RETRY_SECONDS": 600,
    "ECS_MEDIA_REGION": "ap-southeast-5",
    "ECS_MEDIA_CLUSTER": "production-cluster",
    "ECS_MEDIA_TASK_DEFINITION": "production-worker:12",
    "ECS_MEDIA_CONTAINER_NAME": "application",
    "ECS_MEDIA_SUBNET_IDS": ["subnet-a", "subnet-b"],
    "ECS_MEDIA_SECURITY_GROUP_IDS": ["sg-worker"],
    "ECS_MEDIA_ASSIGN_PUBLIC_IP": False,
}


def create_asset(title="Queued media", **fields):
    user = User.objects.create_user(
        f"{uuid.uuid4().hex}@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    source_file = fields.pop(
        "source_file",
        f"quarantine/{uuid.uuid4()}/poster.png",
    )
    return MediaAsset.objects.create(
        business_name="DUDU",
        title=title,
        kind=MediaAsset.Kind.IMAGE,
        source_file=source_file,
        uploaded_by=user,
        **fields,
    )


def png_bytes():
    image = Image.new("RGB", (4, 3), color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.django_db
@override_settings(**ECS_DISPATCH_SETTINGS)
def test_dispatch_runs_one_task_for_exact_asset_uuid(monkeypatch):
    asset = create_asset()
    calls = []

    class EcsClient:
        def run_task(self, **kwargs):
            calls.append(kwargs)
            return {"tasks": [{"taskArn": "task-arn"}], "failures": []}

    monkeypatch.setattr(
        "signage.media_dispatch.boto3.client",
        lambda service, region_name=None: EcsClient(),
    )

    assert dispatch_media_processing(asset.id) is True

    asset.refresh_from_db()
    assert asset.status == MediaAsset.Status.QUARANTINED
    assert asset.dispatch_attempts == 1
    assert asset.dispatched_at is not None
    assert calls[0]["overrides"]["containerOverrides"][0]["command"] == [
        "sh",
        "worker-entrypoint.sh",
        "--asset-id",
        str(asset.id),
    ]
    assert calls[0]["propagateTags"] == "TASK_DEFINITION"
    assert (
        calls[0]["networkConfiguration"]["awsvpcConfiguration"]["assignPublicIp"]
        == "DISABLED"
    )


@pytest.mark.django_db
@override_settings(**ECS_DISPATCH_SETTINGS)
def test_dispatch_failure_keeps_quarantine_and_opens_generic_alert(monkeypatch):
    asset = create_asset()

    class EcsClient:
        def run_task(self, **kwargs):
            return {"tasks": [], "failures": [{"arn": "not-logged"}]}

    monkeypatch.setattr(
        "signage.media_dispatch.boto3.client",
        lambda service, region_name=None: EcsClient(),
    )

    assert dispatch_media_processing(asset.id) is False

    asset.refresh_from_db()
    alert = Alert.objects.get(code="media_processing_dispatch_failed")
    assert asset.status == MediaAsset.Status.QUARANTINED
    assert asset.dispatch_attempts == 1
    assert asset.dispatched_at is None
    assert str(asset.id) not in alert.message
    assert "not-logged" not in alert.message


@pytest.mark.django_db
@override_settings(**ECS_DISPATCH_SETTINGS)
def test_final_dispatch_failure_opens_terminal_alert(monkeypatch):
    asset = create_asset(
        dispatch_attempts=ECS_DISPATCH_SETTINGS["MEDIA_MAX_DISPATCH_ATTEMPTS"] - 1
    )

    class EcsClient:
        def run_task(self, **kwargs):
            return {"tasks": [], "failures": [{"arn": "not-logged"}]}

    monkeypatch.setattr(
        "signage.media_dispatch.boto3.client",
        lambda service, region_name=None: EcsClient(),
    )

    assert dispatch_media_processing(asset.id) is False

    assert Alert.objects.filter(
        code="media_processing_dispatch_exhausted",
        severity=Alert.Severity.CRITICAL,
    ).exists()


@pytest.mark.django_db
def test_queue_dispatches_only_after_transaction_commit(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    asset_id = uuid.uuid4()
    dispatched = []
    monkeypatch.setattr(
        "signage.media_dispatch.dispatch_media_processing",
        lambda value: dispatched.append(value),
    )

    with django_capture_on_commit_callbacks(execute=True):
        with transaction.atomic():
            queue_media_processing(asset_id)
            assert dispatched == []

    assert dispatched == [asset_id]


@pytest.mark.django_db
def test_targeted_processor_is_idempotent_while_lease_is_active(monkeypatch):
    started_at = timezone.now()
    asset = create_asset(
        status=MediaAsset.Status.PROCESSING,
        processing_attempts=1,
        processing_started_at=started_at,
        processing_lease_expires_at=started_at + timedelta(minutes=20),
    )
    monkeypatch.setattr(
        "signage.management.commands.process_media.inspect_media",
        lambda *args, **kwargs: pytest.fail("active lease must not be processed twice"),
    )
    out = StringIO()

    call_command("process_media", asset_id=str(asset.id), stdout=out)

    asset.refresh_from_db()
    assert asset.processing_attempts == 1
    assert "already processing" in out.getvalue()


@pytest.mark.django_db
@override_settings(DEPLOYMENT_ENV="production")
def test_production_processor_forbids_skipping_clamav():
    asset = create_asset()

    with pytest.raises(CommandError, match="forbidden in production"):
        call_command(
            "process_media",
            asset_id=str(asset.id),
            allow_missing_clamav=True,
        )


@pytest.mark.django_db
@override_settings(
    MEDIA_RECONCILE_MAX_ASSETS=2,
    MEDIA_MAX_DISPATCH_ATTEMPTS=5,
    MEDIA_DISPATCH_RETRY_SECONDS=600,
)
def test_reconciliation_caps_dispatches_and_recovers_expired_lease(monkeypatch):
    expired = create_asset(
        title="Expired",
        status=MediaAsset.Status.PROCESSING,
        processing_started_at=timezone.now() - timedelta(hours=1),
        processing_lease_expires_at=timezone.now() - timedelta(minutes=30),
    )
    create_asset(
        title="Waiting one",
        dispatch_attempts=1,
        last_dispatch_attempt_at=timezone.now() - timedelta(minutes=30),
        dispatched_at=timezone.now() - timedelta(minutes=30),
    )
    create_asset(title="Waiting two")
    dispatched = []

    def capture_dispatch(asset_id, *, bypass_backoff=False):
        asset = MediaAsset.objects.get(pk=asset_id)
        dispatched.append((asset_id, asset.status, bypass_backoff))
        return True

    monkeypatch.setattr(
        "signage.management.commands.reconcile_media_processing."
        "dispatch_media_processing",
        capture_dispatch,
    )

    call_command("reconcile_media_processing", limit=2, verbosity=0)

    expired.refresh_from_db()
    assert len(dispatched) == 2
    assert dispatched[0] == (expired.id, MediaAsset.Status.QUARANTINED, True)
    assert expired.status == MediaAsset.Status.QUARANTINED
    assert Alert.objects.filter(code="media_processing_lease_expired").exists()
    assert Alert.objects.filter(code="media_processing_task_stalled").exists()

    with pytest.raises(CommandError, match="between 1 and 2"):
        call_command("reconcile_media_processing", limit=3)


@pytest.mark.django_db
@override_settings(
    MEDIA_RECONCILE_MAX_ASSETS=2,
    MEDIA_MAX_DISPATCH_ATTEMPTS=5,
    MEDIA_DISPATCH_RETRY_SECONDS=600,
)
def test_reconciliation_alerts_when_crashed_worker_exhausted_attempts(monkeypatch):
    asset = create_asset(
        status=MediaAsset.Status.PROCESSING,
        dispatch_attempts=5,
        last_dispatch_attempt_at=timezone.now() - timedelta(minutes=30),
        processing_token=uuid.uuid4(),
        processing_started_at=timezone.now() - timedelta(minutes=30),
        processing_lease_expires_at=timezone.now() - timedelta(minutes=15),
    )
    monkeypatch.setattr(
        "signage.management.commands.reconcile_media_processing."
        "dispatch_media_processing",
        lambda *args, **kwargs: pytest.fail("exhausted assets must not dispatch"),
    )

    call_command("reconcile_media_processing", limit=2, verbosity=0)

    asset.refresh_from_db()
    assert asset.status == MediaAsset.Status.PROCESSING
    assert Alert.objects.filter(
        code="media_processing_dispatch_exhausted",
        severity=Alert.Severity.CRITICAL,
    ).exists()


@pytest.mark.django_db
def test_expired_worker_cannot_overwrite_newer_success(
    tmp_path,
    settings,
    monkeypatch,
):
    settings.MEDIA_ROOT = tmp_path
    monkeypatch.setattr("signage.services.shutil.which", lambda executable: None)
    asset = create_asset(
        source_file=SimpleUploadedFile("poster.png", png_bytes()),
    )
    processor = ProcessMediaCommand()
    first_attempt, _ = processor._claim_asset(asset.id)
    first_token = first_attempt.processing_token
    MediaAsset.objects.filter(pk=asset.id).update(
        processing_lease_expires_at=timezone.now() - timedelta(minutes=1)
    )
    assert ReconcileMediaCommand()._recover_expired_lease(
        asset.id,
        timezone.now(),
    )
    second_attempt, _ = processor._claim_asset(asset.id)
    second_token = second_attempt.processing_token

    inspect_media(second_attempt, require_malware_scanner=False)
    second_attempt.refresh_from_db()
    winning_name = second_attempt.normalized_file.name

    inspect_media(first_attempt, require_malware_scanner=False)

    asset.refresh_from_db()
    assert first_token != second_token
    assert asset.status == MediaAsset.Status.READY
    assert asset.processing_token is None
    assert asset.normalized_file.name == winning_name
    assert str(second_token) in winning_name
    stale_name = (
        Path("validated")
        / str(asset.id)
        / str(first_token)
        / f"{asset.id}.png"
    )
    assert not (tmp_path / stale_name).exists()


@pytest.mark.django_db
def test_deletion_invalidates_worker_and_discards_its_staged_output(
    tmp_path,
    settings,
    monkeypatch,
):
    settings.MEDIA_ROOT = tmp_path
    monkeypatch.setattr("signage.services.shutil.which", lambda executable: None)
    asset = create_asset(
        source_file=SimpleUploadedFile("poster.png", png_bytes()),
    )
    worker_asset, _ = ProcessMediaCommand()._claim_asset(asset.id)
    worker_token = worker_asset.processing_token

    delete_media_binary(worker_asset, worker_asset.uploaded_by)

    def use_worker_local_copy(asset, directory):
        source = Path(directory) / "source.png"
        source.write_bytes(png_bytes())
        return source

    monkeypatch.setattr(
        "signage.services.copy_source_to_temporary_file",
        use_worker_local_copy,
    )
    inspect_media(worker_asset, require_malware_scanner=False)

    asset.refresh_from_db()
    assert asset.status == MediaAsset.Status.ARCHIVED
    assert asset.processing_token is None
    assert asset.processing_lease_expires_at is None
    assert not asset.source_file
    assert not asset.normalized_file
    stale_name = (
        Path("validated")
        / str(asset.id)
        / str(worker_token)
        / f"{asset.id}.png"
    )
    assert not (tmp_path / stale_name).exists()
