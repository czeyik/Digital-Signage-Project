import logging
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from signage.media_dispatch import (
    dispatch_media_processing,
    record_dispatch_exhaustion,
)
from signage.models import Alert, MediaAsset
from signage.services import open_alert

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Retry a capped batch of stalled or undispatched quarantined media."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **options):
        maximum = settings.MEDIA_RECONCILE_MAX_ASSETS
        limit = options["limit"] if options["limit"] is not None else min(10, maximum)
        if limit < 1 or limit > maximum:
            raise CommandError(
                f"--limit must be between 1 and {maximum} to cap one-off ECS task "
                "launches."
            )

        now = timezone.now()
        retry_cutoff = now - timedelta(seconds=settings.MEDIA_DISPATCH_RETRY_SECONDS)
        self._alert_exhausted_attempts(now, retry_cutoff, limit)
        eligible = (
            Q(
                status=MediaAsset.Status.QUARANTINED,
                dispatch_attempts__lt=settings.MEDIA_MAX_DISPATCH_ATTEMPTS,
            )
            & (
                Q(last_dispatch_attempt_at__isnull=True)
                | Q(last_dispatch_attempt_at__lte=retry_cutoff)
            )
        ) | (
            Q(
                status=MediaAsset.Status.PROCESSING,
                dispatch_attempts__lt=settings.MEDIA_MAX_DISPATCH_ATTEMPTS,
            )
            & (
                Q(processing_lease_expires_at__isnull=True)
                | Q(processing_lease_expires_at__lte=now)
            )
        )
        asset_ids = list(
            MediaAsset.objects.filter(eligible)
            .order_by("created_at")
            .values_list("id", flat=True)[:limit]
        )

        dispatched = 0
        for asset_id in asset_ids:
            recovered = self._recover_expired_lease(asset_id, now)
            if not recovered:
                self._note_stalled_dispatch(asset_id, retry_cutoff)
            if dispatch_media_processing(asset_id, bypass_backoff=True):
                dispatched += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciled {len(asset_ids)} media assets; dispatched {dispatched}."
            )
        )

    def _alert_exhausted_attempts(self, now, retry_cutoff, limit):
        exhausted = MediaAsset.objects.filter(
            dispatch_attempts__gte=settings.MEDIA_MAX_DISPATCH_ATTEMPTS,
        ).filter(
            Q(
                status=MediaAsset.Status.QUARANTINED,
                last_dispatch_attempt_at__lte=retry_cutoff,
            )
            | Q(
                status=MediaAsset.Status.PROCESSING,
                processing_lease_expires_at__isnull=True,
            )
            | Q(
                status=MediaAsset.Status.PROCESSING,
                processing_lease_expires_at__lte=now,
            )
        )
        for asset_id in exhausted.order_by("created_at").values_list(
            "id", flat=True
        )[:limit]:
            record_dispatch_exhaustion(asset_id)

    @transaction.atomic
    def _recover_expired_lease(self, asset_id, now):
        asset = MediaAsset.objects.select_for_update().filter(pk=asset_id).first()
        if not asset or asset.status != MediaAsset.Status.PROCESSING:
            return False
        lease = asset.processing_lease_expires_at
        if lease and lease > now:
            return False
        asset.status = MediaAsset.Status.QUARANTINED
        asset.processing_token = None
        asset.processing_lease_expires_at = None
        asset.save(
            update_fields=[
                "status",
                "processing_token",
                "processing_lease_expires_at",
                "updated_at",
            ]
        )
        open_alert(
            None,
            "media_processing_lease_expired",
            Alert.Severity.WARNING,
            (
                "A media validation task stopped before reporting a result "
                "and was retried."
            ),
        )
        logger.warning("Recovered expired media processing lease asset_id=%s", asset_id)
        return True

    def _note_stalled_dispatch(self, asset_id, retry_cutoff):
        stalled = MediaAsset.objects.filter(
            pk=asset_id,
            status=MediaAsset.Status.QUARANTINED,
            dispatched_at__isnull=False,
            last_dispatch_attempt_at__lte=retry_cutoff,
        ).exists()
        if not stalled:
            return
        open_alert(
            None,
            "media_processing_task_stalled",
            Alert.Severity.WARNING,
            "A queued media validation task did not start and was retried.",
        )
        logger.warning("Retrying stalled media processing task asset_id=%s", asset_id)
