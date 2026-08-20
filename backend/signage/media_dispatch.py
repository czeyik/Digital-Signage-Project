import hashlib
import logging
import uuid
from datetime import timedelta

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Alert, ApiThrottle, MediaAsset
from .services import open_alert

logger = logging.getLogger(__name__)


class DispatchConfigurationError(ValueError):
    pass


DISPATCH_BUDGET_KEY = hashlib.sha256(
    b"duducar-media-dispatch-hourly-budget-v1"
).hexdigest()


def queue_media_processing(asset_id):
    """Dispatch only after the transaction which created the upload commits."""
    normalized_id = uuid.UUID(str(asset_id))
    transaction.on_commit(lambda: dispatch_media_processing(normalized_id))


def _ecs_configuration():
    required = {
        "cluster": settings.ECS_MEDIA_CLUSTER,
        "task_definition": settings.ECS_MEDIA_TASK_DEFINITION,
        "container_name": settings.ECS_MEDIA_CONTAINER_NAME,
    }
    missing = [name for name, value in required.items() if not value]
    if not settings.ECS_MEDIA_SUBNET_IDS:
        missing.append("subnet_ids")
    if not settings.ECS_MEDIA_SECURITY_GROUP_IDS:
        missing.append("security_group_ids")
    if missing:
        raise DispatchConfigurationError(
            "Missing ECS media dispatch settings: " + ", ".join(sorted(missing))
        )
    limits = {
        "max_concurrent_tasks": settings.MEDIA_DISPATCH_MAX_CONCURRENT_TASKS,
        "max_tasks_per_hour": settings.MEDIA_DISPATCH_MAX_TASKS_PER_HOUR,
        "ambiguity_reuse_seconds": (
            settings.MEDIA_DISPATCH_AMBIGUITY_REUSE_SECONDS
        ),
        "startup_grace_seconds": settings.MEDIA_DISPATCH_STARTUP_GRACE_SECONDS,
        "aws_connect_timeout_seconds": (
            settings.MEDIA_DISPATCH_AWS_CONNECT_TIMEOUT_SECONDS
        ),
        "aws_read_timeout_seconds": settings.MEDIA_DISPATCH_AWS_READ_TIMEOUT_SECONDS,
    }
    invalid = [name for name, value in limits.items() if value < 1]
    if settings.MEDIA_DISPATCH_AMBIGUITY_REUSE_SECONDS > 3600:
        invalid.append("ambiguity_reuse_seconds")
    if settings.MEDIA_DISPATCH_MAX_CONCURRENT_TASKS > 2:
        invalid.append("max_concurrent_tasks")
    if settings.MEDIA_DISPATCH_MAX_TASKS_PER_HOUR > 6:
        invalid.append("max_tasks_per_hour")
    if invalid:
        raise DispatchConfigurationError(
            "Invalid ECS media dispatch limits: " + ", ".join(sorted(invalid))
        )
    return required


def _ecs_client():
    return boto3.client(
        "ecs",
        region_name=settings.ECS_MEDIA_REGION or None,
        config=Config(
            connect_timeout=settings.MEDIA_DISPATCH_AWS_CONNECT_TIMEOUT_SECONDS,
            read_timeout=settings.MEDIA_DISPATCH_AWS_READ_TIMEOUT_SECONDS,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def _task_definition_family(task_definition):
    return task_definition.rsplit("/", 1)[-1].split(":", 1)[0]


def _active_ecs_task_count(client, ecs):
    task_arns = set()
    family = _task_definition_family(ecs["task_definition"])
    # ECS tasks whose last status is PENDING already have desiredStatus=RUNNING.
    for desired_status in ("RUNNING",):
        next_token = None
        while True:
            arguments = {
                "cluster": ecs["cluster"],
                "family": family,
                "desiredStatus": desired_status,
            }
            if next_token:
                arguments["nextToken"] = next_token
            response = client.list_tasks(**arguments)
            task_arns.update(response.get("taskArns", []))
            if len(task_arns) >= settings.MEDIA_DISPATCH_MAX_CONCURRENT_TASKS:
                return len(task_arns)
            next_token = response.get("nextToken")
            if not next_token:
                break
    return len(task_arns)


def _lock_hourly_dispatch_budget(now):
    budget, _ = ApiThrottle.objects.select_for_update().get_or_create(
        key_hash=DISPATCH_BUDGET_KEY
    )
    if budget.window_started_at <= now - timedelta(hours=1):
        budget.attempts = 0
        budget.window_started_at = now
        budget.blocked_until = None
        budget.save(
            update_fields=[
                "attempts",
                "window_started_at",
                "blocked_until",
                "updated_at",
            ]
        )
    return budget


def _asset_dispatch_state_key(asset_id):
    return hashlib.sha256(
        f"duducar-media-dispatch-state-v1|{asset_id}".encode()
    ).hexdigest()


def _record_dispatch_deferred(asset_id, reason):
    logger.warning(
        "Media processing dispatch deferred asset_id=%s reason=%s",
        asset_id,
        reason,
    )
    if reason == "hourly_budget_exhausted":
        open_alert(
            None,
            "media_processing_dispatch_budget_exhausted",
            Alert.Severity.WARNING,
            (
                "The hourly media-processing task budget was reached; "
                "quarantined uploads will retry automatically."
            ),
        )


@transaction.atomic
def _reserve_dispatch_attempt(asset_id, bypass_backoff, *, mark_issued=False):
    asset = MediaAsset.objects.select_for_update().filter(pk=asset_id).first()
    if not asset or asset.status != MediaAsset.Status.QUARANTINED:
        return None
    now = timezone.now()
    retry_after = asset.last_dispatch_attempt_at
    if retry_after:
        retry_after += timedelta(seconds=settings.MEDIA_DISPATCH_RETRY_SECONDS)
    if not bypass_backoff and retry_after and retry_after > now:
        return None

    dispatch_state = None
    reuse_ambiguous_attempt = False
    if mark_issued:
        dispatch_state = (
            ApiThrottle.objects.select_for_update()
            .filter(key_hash=_asset_dispatch_state_key(asset_id))
            .first()
        )
        ambiguity_cutoff = now - timedelta(
            seconds=settings.MEDIA_DISPATCH_AMBIGUITY_REUSE_SECONDS
        )
        reuse_ambiguous_attempt = bool(
            dispatch_state
            and dispatch_state.attempts > 0
            and dispatch_state.attempts == asset.dispatch_attempts
            and dispatch_state.window_started_at > ambiguity_cutoff
        )
    if not reuse_ambiguous_attempt:
        if asset.dispatch_attempts >= settings.MEDIA_MAX_DISPATCH_ATTEMPTS:
            if dispatch_state is not None:
                dispatch_state.delete()
            if mark_issued and asset.dispatched_at is not None:
                asset.dispatched_at = None
                asset.save(update_fields=["dispatched_at", "updated_at"])
            return None
        asset.dispatch_attempts += 1
    asset.last_dispatch_attempt_at = now
    if mark_issued:
        asset.dispatched_at = now
        if dispatch_state is None:
            dispatch_state = ApiThrottle.objects.create(
                key_hash=_asset_dispatch_state_key(asset_id)
            )
        dispatch_state.attempts = asset.dispatch_attempts
        # Preserve the first uncertain call time while reusing a client token.
        # Once this finite window expires, a later call advances the attempt and
        # receives a fresh token instead of retrying one token indefinitely.
        if not reuse_ambiguous_attempt:
            dispatch_state.window_started_at = now
        dispatch_state.blocked_until = now
        dispatch_state.save(
            update_fields=[
                "attempts",
                "window_started_at",
                "blocked_until",
                "updated_at",
            ]
        )
    update_fields = [
        "dispatch_attempts",
        "last_dispatch_attempt_at",
        "updated_at",
    ]
    if mark_issued:
        update_fields.append("dispatched_at")
    asset.save(update_fields=update_fields)
    return asset.dispatch_attempts


@transaction.atomic
def _clear_dispatch_state(asset_id, attempt, *, clear_dispatched_at):
    state = (
        ApiThrottle.objects.select_for_update()
        .filter(key_hash=_asset_dispatch_state_key(asset_id))
        .first()
    )
    if state and state.attempts == attempt:
        state.delete()
    if clear_dispatched_at:
        MediaAsset.objects.select_for_update().filter(
            pk=asset_id,
            dispatch_attempts=attempt,
        ).update(dispatched_at=None)


def _record_dispatch_failure(asset_id, reason):
    open_alert(
        None,
        "media_processing_dispatch_failed",
        Alert.Severity.WARNING,
        "A quarantined media upload could not be queued for validation.",
    )
    logger.error(
        "Media processing dispatch failed asset_id=%s reason=%s",
        asset_id,
        reason,
    )


def record_dispatch_exhaustion(asset_id):
    _, created = open_alert(
        None,
        "media_processing_dispatch_exhausted",
        Alert.Severity.CRITICAL,
        (
            "One or more media uploads exhausted automated validation retries "
            "and require operator investigation."
        ),
    )
    if created:
        logger.error(
            "Media processing dispatch attempts exhausted asset_id=%s",
            asset_id,
        )


def _handle_dispatch_failure(asset_id, attempt, reason):
    _record_dispatch_failure(asset_id, reason)
    if attempt >= settings.MEDIA_MAX_DISPATCH_ATTEMPTS:
        record_dispatch_exhaustion(asset_id)


def dispatch_media_processing(asset_id, *, bypass_backoff=False):
    """Run one isolated on-demand task for one UUID without shell interpolation."""
    normalized_id = uuid.UUID(str(asset_id))
    backend = settings.MEDIA_PROCESSING_DISPATCH_BACKEND
    if backend == "disabled" and settings.DEPLOYMENT_ENV != "production":
        logger.info(
            "Media processing dispatch is disabled asset_id=%s",
            normalized_id,
        )
        return False

    try:
        if backend != "ecs":
            raise DispatchConfigurationError(
                "Production media processing dispatch must use one-off ECS/Fargate "
                "tasks."
            )
        ecs = _ecs_configuration()
    except DispatchConfigurationError:
        attempt = _reserve_dispatch_attempt(normalized_id, bypass_backoff)
        if attempt is None:
            return False
        _handle_dispatch_failure(normalized_id, attempt, "invalid_configuration")
        return False

    try:
        client = _ecs_client()
    except BotoCoreError:
        attempt = _reserve_dispatch_attempt(normalized_id, bypass_backoff)
        if attempt is not None:
            _handle_dispatch_failure(normalized_id, attempt, "aws_transport_error")
        return False
    with transaction.atomic():
        now = timezone.now()
        budget = _lock_hourly_dispatch_budget(now)
        if budget.attempts >= settings.MEDIA_DISPATCH_MAX_TASKS_PER_HOUR:
            _record_dispatch_deferred(normalized_id, "hourly_budget_exhausted")
            return False

        try:
            active_task_count = _active_ecs_task_count(client, ecs)
        except ClientError as exc:
            attempt = _reserve_dispatch_attempt(normalized_id, bypass_backoff)
            if attempt is None:
                return False
            error_code = exc.response.get("Error", {}).get("Code", "client_error")
            _handle_dispatch_failure(
                normalized_id,
                attempt,
                f"aws_{error_code}"[:64],
            )
            return False
        except BotoCoreError:
            attempt = _reserve_dispatch_attempt(normalized_id, bypass_backoff)
            if attempt is None:
                return False
            _handle_dispatch_failure(normalized_id, attempt, "aws_transport_error")
            return False

        recently_dispatched_count = MediaAsset.objects.filter(
            dispatched_at__gte=now
            - timedelta(seconds=settings.MEDIA_DISPATCH_STARTUP_GRACE_SECONDS)
        ).exclude(pk=normalized_id).count()
        # Sum both signals conservatively. ECS is eventually consistent, so a
        # recent database reservation may be a different task not yet returned
        # by ListTasks. Temporary double-counting is safer than exceeding the
        # pilot's two-task budget cap.
        if (
            active_task_count + recently_dispatched_count
            >= settings.MEDIA_DISPATCH_MAX_CONCURRENT_TASKS
        ):
            _record_dispatch_deferred(normalized_id, "concurrency_limit")
            return False

        attempt = _reserve_dispatch_attempt(
            normalized_id,
            bypass_backoff,
            mark_issued=True,
        )
        if attempt is None:
            exhausted = MediaAsset.objects.filter(
                pk=normalized_id,
                status__in=[
                    MediaAsset.Status.QUARANTINED,
                    MediaAsset.Status.PROCESSING,
                ],
                dispatch_attempts__gte=settings.MEDIA_MAX_DISPATCH_ATTEMPTS,
            ).exists()
            if exhausted:
                record_dispatch_exhaustion(normalized_id)
            return False

        # Commit the cost reservation and idempotency marker before making the
        # mutating AWS request. A process crash or ambiguous transport failure
        # must not roll these controls back after ECS may have accepted it.
        budget.attempts += 1
        budget.save(update_fields=["attempts", "updated_at"])

    try:
        response = client.run_task(
            cluster=ecs["cluster"],
            taskDefinition=ecs["task_definition"],
            launchType="FARGATE",
            count=1,
            clientToken=f"media-{normalized_id.hex}-{attempt}",
            enableECSManagedTags=True,
            propagateTags="TASK_DEFINITION",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": settings.ECS_MEDIA_SUBNET_IDS,
                    "securityGroups": settings.ECS_MEDIA_SECURITY_GROUP_IDS,
                    "assignPublicIp": (
                        "ENABLED"
                        if settings.ECS_MEDIA_ASSIGN_PUBLIC_IP
                        else "DISABLED"
                    ),
                }
            },
            overrides={
                "containerOverrides": [
                    {
                        "name": ecs["container_name"],
                        "command": [
                            "sh",
                            "worker-entrypoint-root-init.sh",
                            "--asset-id",
                            str(normalized_id),
                        ],
                    }
                ]
            },
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "client_error")
        status_code = exc.response.get("ResponseMetadata", {}).get(
            "HTTPStatusCode", 0
        )
        if status_code >= 500:
            _record_dispatch_failure(normalized_id, "aws_outcome_unknown")
        else:
            _clear_dispatch_state(
                normalized_id,
                attempt,
                clear_dispatched_at=True,
            )
            _handle_dispatch_failure(
                normalized_id,
                attempt,
                f"aws_{error_code}"[:64],
            )
        return False
    except BotoCoreError:
        _record_dispatch_failure(normalized_id, "aws_outcome_unknown")
        return False

    if response.get("failures") or len(response.get("tasks", [])) != 1:
        _clear_dispatch_state(
            normalized_id,
            attempt,
            clear_dispatched_at=True,
        )
        _handle_dispatch_failure(
            normalized_id,
            attempt,
            "ecs_run_task_rejected",
        )
        return False

    _clear_dispatch_state(
        normalized_id,
        attempt,
        clear_dispatched_at=False,
    )
    logger.info(
        "Media processing task dispatched asset_id=%s attempt=%s",
        normalized_id,
        attempt,
    )
    return True
