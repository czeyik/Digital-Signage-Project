import logging
import uuid
from datetime import timedelta

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Alert, MediaAsset
from .services import open_alert

logger = logging.getLogger(__name__)


class DispatchConfigurationError(ValueError):
    pass


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
    return required


@transaction.atomic
def _reserve_dispatch_attempt(asset_id, bypass_backoff):
    asset = MediaAsset.objects.select_for_update().filter(pk=asset_id).first()
    if not asset or asset.status != MediaAsset.Status.QUARANTINED:
        return None
    if asset.dispatch_attempts >= settings.MEDIA_MAX_DISPATCH_ATTEMPTS:
        return None
    now = timezone.now()
    retry_after = asset.last_dispatch_attempt_at
    if retry_after:
        retry_after += timedelta(seconds=settings.MEDIA_DISPATCH_RETRY_SECONDS)
    if not bypass_backoff and retry_after and retry_after > now:
        return None
    asset.dispatch_attempts += 1
    asset.last_dispatch_attempt_at = now
    asset.save(
        update_fields=[
            "dispatch_attempts",
            "last_dispatch_attempt_at",
            "updated_at",
        ]
    )
    return asset.dispatch_attempts


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

    attempt = _reserve_dispatch_attempt(normalized_id, bypass_backoff)
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

    try:
        if backend != "ecs":
            raise DispatchConfigurationError(
                "Production media processing dispatch must use one-off ECS/Fargate "
                "tasks."
            )
        ecs = _ecs_configuration()
        response = boto3.client(
            "ecs",
            region_name=settings.ECS_MEDIA_REGION or None,
        ).run_task(
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
                            "worker-entrypoint.sh",
                            "--asset-id",
                            str(normalized_id),
                        ],
                    }
                ]
            },
        )
        if response.get("failures") or len(response.get("tasks", [])) != 1:
            _handle_dispatch_failure(
                normalized_id,
                attempt,
                "ecs_run_task_rejected",
            )
            return False
    except DispatchConfigurationError:
        _handle_dispatch_failure(normalized_id, attempt, "invalid_configuration")
        return False
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "client_error")
        _handle_dispatch_failure(
            normalized_id,
            attempt,
            f"aws_{error_code}"[:64],
        )
        return False
    except BotoCoreError:
        _handle_dispatch_failure(normalized_id, attempt, "aws_transport_error")
        return False

    MediaAsset.objects.filter(pk=normalized_id).update(dispatched_at=timezone.now())
    logger.info(
        "Media processing task dispatched asset_id=%s attempt=%s",
        normalized_id,
        attempt,
    )
    return True
