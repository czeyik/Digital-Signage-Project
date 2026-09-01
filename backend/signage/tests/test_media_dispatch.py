import uuid
from datetime import timedelta
from io import BytesIO, StringIO
from pathlib import Path

import pytest
from botocore.exceptions import ReadTimeoutError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.db import transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from signage.management.commands.process_media import Command as ProcessMediaCommand
from signage.management.commands.reconcile_media_processing import (
    Command as ReconcileMediaCommand,
)
from signage.media_dispatch import (
    DISPATCH_BUDGET_KEY,
    dispatch_media_processing,
    queue_media_processing,
)
from signage.models import Alert, ApiThrottle, MediaAsset, MediaDeletion, User
from signage.services import delete_media_binary, inspect_media, process_media_deletion

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
        def list_tasks(self, **kwargs):
            return {"taskArns": []}

        def run_task(self, **kwargs):
            calls.append(kwargs)
            return {"tasks": [{"taskArn": "task-arn"}], "failures": []}

    monkeypatch.setattr(
        "signage.media_dispatch.boto3.client",
        lambda service, region_name=None, config=None: EcsClient(),
    )

    assert dispatch_media_processing(asset.id) is True

    asset.refresh_from_db()
    assert asset.status == MediaAsset.Status.QUARANTINED
    assert asset.dispatch_attempts == 1
    assert asset.dispatched_at is not None
    assert ApiThrottle.objects.get(key_hash=DISPATCH_BUDGET_KEY).attempts == 1
    assert calls[0]["overrides"]["containerOverrides"][0]["command"] == [
        "sh",
        "worker-entrypoint-root-init.sh",
        "--asset-id",
        str(asset.id),
    ]
    assert calls[0]["startedBy"] == str(asset.id)
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
        def list_tasks(self, **kwargs):
            return {"taskArns": []}

        def run_task(self, **kwargs):
            return {"tasks": [], "failures": [{"arn": "not-logged"}]}

    monkeypatch.setattr(
        "signage.media_dispatch.boto3.client",
        lambda service, region_name=None, config=None: EcsClient(),
    )

    assert dispatch_media_processing(asset.id) is False

    asset.refresh_from_db()
    alert = Alert.objects.get(code="media_processing_dispatch_failed")
    assert asset.status == MediaAsset.Status.QUARANTINED
    assert asset.dispatch_attempts == 1
    assert asset.dispatched_at is None
    assert ApiThrottle.objects.get(key_hash=DISPATCH_BUDGET_KEY).attempts == 1
    assert str(asset.id) not in alert.message
    assert "not-logged" not in alert.message


@pytest.mark.django_db
@override_settings(**ECS_DISPATCH_SETTINGS)
def test_final_dispatch_failure_opens_terminal_alert(monkeypatch):
    asset = create_asset(
        dispatch_attempts=ECS_DISPATCH_SETTINGS["MEDIA_MAX_DISPATCH_ATTEMPTS"] - 1
    )

    class EcsClient:
        def list_tasks(self, **kwargs):
            return {"taskArns": []}

        def run_task(self, **kwargs):
            return {"tasks": [], "failures": [{"arn": "not-logged"}]}

    monkeypatch.setattr(
        "signage.media_dispatch.boto3.client",
        lambda service, region_name=None, config=None: EcsClient(),
    )

    assert dispatch_media_processing(asset.id) is False

    assert Alert.objects.filter(
        code="media_processing_dispatch_exhausted",
        severity=Alert.Severity.CRITICAL,
    ).exists()


@pytest.mark.django_db
@override_settings(
    **ECS_DISPATCH_SETTINGS,
    MEDIA_DISPATCH_MAX_CONCURRENT_TASKS=1,
)
def test_dispatch_concurrency_limit_defers_without_consuming_attempt(monkeypatch):
    asset = create_asset()

    class EcsClient:
        def list_tasks(self, **kwargs):
            if kwargs["desiredStatus"] == "RUNNING":
                return {"taskArns": ["already-running"]}
            return {"taskArns": []}

        def run_task(self, **kwargs):
            pytest.fail("capacity-limited upload must not start another task")

    monkeypatch.setattr(
        "signage.media_dispatch.boto3.client",
        lambda service, region_name=None, config=None: EcsClient(),
    )

    assert dispatch_media_processing(asset.id) is False

    asset.refresh_from_db()
    assert asset.dispatch_attempts == 0
    assert asset.dispatched_at is None


@pytest.mark.django_db
@override_settings(
    **ECS_DISPATCH_SETTINGS,
    MEDIA_DISPATCH_MAX_CONCURRENT_TASKS=2,
)
def test_dispatch_combines_visible_and_recent_capacity_signals(monkeypatch):
    asset = create_asset()
    create_asset(title="Recent dispatch", dispatched_at=timezone.now())

    class EcsClient:
        def list_tasks(self, **kwargs):
            return {"taskArns": ["older-active-task"]}

        def describe_tasks(self, **kwargs):
            return {"tasks": [{"taskArn": "older-active-task"}]}

        def run_task(self, **kwargs):
            pytest.fail("combined capacity must defer the third task")

    monkeypatch.setattr(
        "signage.media_dispatch.boto3.client",
        lambda service, region_name=None, config=None: EcsClient(),
    )

    assert dispatch_media_processing(asset.id) is False

    asset.refresh_from_db()
    assert asset.dispatch_attempts == 0
    assert asset.dispatched_at is None


@pytest.mark.django_db
@override_settings(
    **ECS_DISPATCH_SETTINGS,
    MEDIA_DISPATCH_MAX_CONCURRENT_TASKS=2,
)
def test_dispatch_deduplicates_visible_task_and_recent_reservation(monkeypatch):
    asset = create_asset()
    recent = create_asset(
        title="Visible recent dispatch",
        dispatched_at=timezone.now(),
    )
    calls = []

    class EcsClient:
        def list_tasks(self, **kwargs):
            return {"taskArns": ["visible-recent-task"]}

        def describe_tasks(self, **kwargs):
            assert kwargs == {
                "cluster": "production-cluster",
                "tasks": ["visible-recent-task"],
            }
            return {
                "tasks": [
                    {
                        "taskArn": "visible-recent-task",
                        "startedBy": str(recent.id),
                    }
                ]
            }

        def run_task(self, **kwargs):
            calls.append(kwargs)
            return {"tasks": [{"taskArn": "second-task"}], "failures": []}

    monkeypatch.setattr(
        "signage.media_dispatch.boto3.client",
        lambda service, region_name=None, config=None: EcsClient(),
    )

    assert dispatch_media_processing(asset.id) is True
    assert calls[0]["startedBy"] == str(asset.id)


@pytest.mark.django_db
def test_media_list_labels_quarantined_assets_as_queued(client):
    asset = create_asset()
    client.force_login(asset.uploaded_by)

    response = client.get(reverse("media-list"))

    assert response.status_code == 200
    assert b"Queued for validation" in response.content
    assert b">Quarantined<" not in response.content


@pytest.mark.django_db
@override_settings(
    **ECS_DISPATCH_SETTINGS,
    MEDIA_DISPATCH_MAX_TASKS_PER_HOUR=2,
)
def test_hourly_dispatch_budget_defers_without_consuming_attempt(monkeypatch):
    asset = create_asset()
    ApiThrottle.objects.create(
        key_hash=DISPATCH_BUDGET_KEY,
        attempts=2,
        window_started_at=timezone.now(),
    )

    class EcsClient:
        def list_tasks(self, **kwargs):
            pytest.fail("hourly budget is checked before ECS capacity")

        def run_task(self, **kwargs):
            pytest.fail("budget-limited upload must not start another task")

    monkeypatch.setattr(
        "signage.media_dispatch.boto3.client",
        lambda service, region_name=None, config=None: EcsClient(),
    )

    assert dispatch_media_processing(asset.id) is False

    asset.refresh_from_db()
    assert asset.dispatch_attempts == 0
    assert Alert.objects.filter(
        code="media_processing_dispatch_budget_exhausted"
    ).exists()


@pytest.mark.django_db
@override_settings(**ECS_DISPATCH_SETTINGS)
def test_dispatch_rejects_response_with_task_and_failure(monkeypatch):
    asset = create_asset()

    class EcsClient:
        def list_tasks(self, **kwargs):
            return {"taskArns": []}

        def run_task(self, **kwargs):
            return {
                "tasks": [{"taskArn": "ambiguous-partial-task"}],
                "failures": [{"arn": "must-not-be-logged"}],
            }

    monkeypatch.setattr(
        "signage.media_dispatch.boto3.client",
        lambda service, region_name=None, config=None: EcsClient(),
    )

    assert dispatch_media_processing(asset.id) is False

    asset.refresh_from_db()
    assert asset.dispatch_attempts == 1
    assert asset.dispatched_at is None
    assert ApiThrottle.objects.get(key_hash=DISPATCH_BUDGET_KEY).attempts == 1
    assert Alert.objects.filter(code="media_processing_dispatch_failed").exists()


@pytest.mark.django_db
@override_settings(**ECS_DISPATCH_SETTINGS)
def test_ambiguous_run_task_retry_reuses_token_and_consumes_call_budget(monkeypatch):
    asset = create_asset()
    client_tokens = []

    class EcsClient:
        def list_tasks(self, **kwargs):
            return {"taskArns": []}

        def run_task(self, **kwargs):
            client_tokens.append(kwargs["clientToken"])
            if len(client_tokens) == 1:
                raise ReadTimeoutError(endpoint_url="https://ecs.example.test")
            return {"tasks": [{"taskArn": "same-idempotent-task"}], "failures": []}

    client = EcsClient()
    monkeypatch.setattr(
        "signage.media_dispatch.boto3.client",
        lambda service, region_name=None, config=None: client,
    )

    assert dispatch_media_processing(asset.id) is False
    asset.refresh_from_db()
    assert asset.dispatch_attempts == 1
    assert asset.dispatched_at is not None
    assert ApiThrottle.objects.get(key_hash=DISPATCH_BUDGET_KEY).attempts == 1

    assert dispatch_media_processing(asset.id, bypass_backoff=True) is True

    asset.refresh_from_db()
    assert asset.dispatch_attempts == 1
    assert client_tokens[0] == client_tokens[1]
    assert ApiThrottle.objects.get(key_hash=DISPATCH_BUDGET_KEY).attempts == 2
    assert not ApiThrottle.objects.exclude(key_hash=DISPATCH_BUDGET_KEY).exists()


@pytest.mark.django_db
@override_settings(
    **ECS_DISPATCH_SETTINGS,
    MEDIA_DISPATCH_AMBIGUITY_REUSE_SECONDS=900,
)
def test_expired_ambiguous_dispatch_advances_attempt_and_token(monkeypatch):
    asset = create_asset()
    client_tokens = []

    class EcsClient:
        def list_tasks(self, **kwargs):
            return {"taskArns": []}

        def run_task(self, **kwargs):
            client_tokens.append(kwargs["clientToken"])
            if len(client_tokens) == 1:
                raise ReadTimeoutError(endpoint_url="https://ecs.example.test")
            return {"tasks": [{"taskArn": "new-attempt-task"}], "failures": []}

    client = EcsClient()
    monkeypatch.setattr(
        "signage.media_dispatch.boto3.client",
        lambda service, region_name=None, config=None: client,
    )

    assert dispatch_media_processing(asset.id) is False
    dispatch_state = ApiThrottle.objects.exclude(
        key_hash=DISPATCH_BUDGET_KEY
    ).get()
    dispatch_state.window_started_at = timezone.now() - timedelta(minutes=16)
    dispatch_state.save(update_fields=["window_started_at"])

    assert dispatch_media_processing(asset.id, bypass_backoff=True) is True

    asset.refresh_from_db()
    assert asset.dispatch_attempts == 2
    assert client_tokens[0] != client_tokens[1]
    assert client_tokens[0].endswith("-1")
    assert client_tokens[1].endswith("-2")
    assert ApiThrottle.objects.get(key_hash=DISPATCH_BUDGET_KEY).attempts == 2
    assert not ApiThrottle.objects.exclude(key_hash=DISPATCH_BUDGET_KEY).exists()


@pytest.mark.django_db
@override_settings(
    **ECS_DISPATCH_SETTINGS,
    MEDIA_DISPATCH_AMBIGUITY_REUSE_SECONDS=900,
)
def test_expired_final_ambiguous_dispatch_moves_to_manual_review(monkeypatch):
    asset = create_asset(
        dispatch_attempts=ECS_DISPATCH_SETTINGS["MEDIA_MAX_DISPATCH_ATTEMPTS"] - 1
    )
    run_task_calls = 0

    class EcsClient:
        def list_tasks(self, **kwargs):
            return {"taskArns": []}

        def run_task(self, **kwargs):
            nonlocal run_task_calls
            run_task_calls += 1
            raise ReadTimeoutError(endpoint_url="https://ecs.example.test")

    monkeypatch.setattr(
        "signage.media_dispatch.boto3.client",
        lambda service, region_name=None, config=None: EcsClient(),
    )

    assert dispatch_media_processing(asset.id) is False
    dispatch_state = ApiThrottle.objects.exclude(
        key_hash=DISPATCH_BUDGET_KEY
    ).get()
    dispatch_state.window_started_at = timezone.now() - timedelta(minutes=16)
    dispatch_state.save(update_fields=["window_started_at"])

    assert dispatch_media_processing(asset.id, bypass_backoff=True) is False

    asset.refresh_from_db()
    assert run_task_calls == 1
    assert (
        asset.dispatch_attempts
        == ECS_DISPATCH_SETTINGS["MEDIA_MAX_DISPATCH_ATTEMPTS"]
    )
    assert asset.dispatched_at is None
    assert Alert.objects.filter(
        code="media_processing_dispatch_exhausted",
        severity=Alert.Severity.CRITICAL,
    ).exists()
    assert ApiThrottle.objects.get(key_hash=DISPATCH_BUDGET_KEY).attempts == 1
    assert not ApiThrottle.objects.exclude(key_hash=DISPATCH_BUDGET_KEY).exists()


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
    assert second_token.hex in winning_name
    stale_name = (
        Path("validated")
        / asset.id.hex
        / first_token.hex
        / "media.png"
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
    assert process_media_deletion(MediaDeletion.objects.get(asset=asset).pk)
    asset.refresh_from_db()
    assert not asset.source_file
    assert not asset.normalized_file
    stale_name = (
        Path("validated")
        / asset.id.hex
        / worker_token.hex
        / "media.png"
    )
    assert not (tmp_path / stale_name).exists()


@pytest.mark.django_db
def test_media_deletion_retries_before_clearing_database_file_names(
    tmp_path,
    settings,
    monkeypatch,
):
    settings.MEDIA_ROOT = tmp_path
    asset = create_asset(
        source_file=SimpleUploadedFile("source.png", png_bytes()),
        normalized_file=SimpleUploadedFile("normalized.png", png_bytes()),
    )
    delete_media_binary(asset, asset.uploaded_by)
    deletion = MediaDeletion.objects.get(asset=asset)
    storage = asset.source_file.storage
    original_delete = storage.delete

    def fail_delete(name):
        raise OSError("temporary object-store failure")

    monkeypatch.setattr(storage, "delete", fail_delete)
    assert process_media_deletion(deletion.pk) is False
    asset.refresh_from_db()
    deletion.refresh_from_db()
    assert asset.source_file
    assert asset.normalized_file
    assert deletion.attempts == 1
    assert deletion.completed_at is None
    assert "temporary object-store failure" in deletion.last_error

    monkeypatch.setattr(storage, "delete", original_delete)
    assert process_media_deletion(deletion.pk) is True
    asset.refresh_from_db()
    deletion.refresh_from_db()
    assert not asset.source_file
    assert not asset.normalized_file
    assert deletion.attempts == 2
    assert deletion.completed_at is not None
