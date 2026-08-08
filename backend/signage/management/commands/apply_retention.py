from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Exists, Max, OuterRef
from django.utils import timezone

from signage.models import (
    Alert,
    AuditEvent,
    DeviceAssignment,
    DeviceHeartbeat,
    DeviceOperationalEvent,
    Driver,
    PlaybackBatch,
    PlaybackCorrection,
    PlaybackEvent,
    Vehicle,
)

DELETE_BATCH_SIZE = 5_000


class Command(BaseCommand):
    help = "Apply one-year privacy and operational-record retention."

    @transaction.atomic
    def handle(self, *args, **options):
        # Keep anonymization, evidence deletion, and the aggregate audit event in
        # one transaction. A uniqueness collision or other failure must not
        # leave an unaudited, partially applied retention run.
        cutoff = timezone.now() - timedelta(days=365)
        active_assignments = DeviceAssignment.objects.filter(
            driver=OuterRef("pk"), unassigned_at__isnull=True
        )
        drivers = Driver.objects.annotate(
            active=Exists(active_assignments),
            last_unassigned=Max("deviceassignment__unassigned_at"),
        ).filter(active=False, anonymized_at__isnull=True, last_unassigned__lt=cutoff)
        driver_count = 0
        for driver in drivers:
            driver.name = "Anonymized driver"
            driver.anonymized_at = timezone.now()
            driver.save(update_fields=["name", "anonymized_at", "updated_at"])
            driver_count += 1
        vehicle_count = 0
        active_vehicle_assignments = DeviceAssignment.objects.filter(
            vehicle=OuterRef("pk"), unassigned_at__isnull=True
        )
        vehicles = Vehicle.objects.annotate(
            active=Exists(active_vehicle_assignments),
            last_unassigned=Max("deviceassignment__unassigned_at"),
        ).filter(active=False, anonymized_at__isnull=True, last_unassigned__lt=cutoff)
        for vehicle in vehicles:
            vehicle.registration = f"ANON-{vehicle.pk}"
            vehicle.anonymized_at = timezone.now()
            vehicle.save(update_fields=["registration", "anonymized_at", "updated_at"])
            vehicle_count += 1
        playback_counts = self._delete_expired_playback_evidence(cutoff)
        heartbeat_count, _ = DeviceHeartbeat.objects.filter(
            recorded_at__lt=cutoff
        ).delete()
        operational_count, _ = DeviceOperationalEvent.objects.filter(
            recorded_at__lt=cutoff
        ).delete()
        alert_count, _ = Alert.objects.filter(created_at__lt=cutoff).delete()
        audit_count, _ = AuditEvent.objects.filter(occurred_at__lt=cutoff).delete()
        AuditEvent.objects.create(
            action="retention.apply",
            target_type="retention_window",
            target_id=cutoff.date().isoformat(),
            metadata={
                "drivers_anonymized": driver_count,
                "vehicles_anonymized": vehicle_count,
                "heartbeats_deleted": heartbeat_count,
                "operational_events_deleted": operational_count,
                **playback_counts,
                "alerts_deleted": alert_count,
                "audit_events_deleted": audit_count,
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Anonymized {driver_count} drivers and {vehicle_count} vehicles; "
                f"removed {heartbeat_count} heartbeats, {operational_count} "
                f"operational events, {alert_count} alerts, and {audit_count} audits."
            )
        )

    def _delete_expired_playback_evidence(self, cutoff):
        """Delete only complete, relationally safe evidence graphs after one year.

        A correction's lifetime follows its parent event: when the event's own
        timestamp is older than one year, corrections and the event are deleted
        together. Events are processed by status to use the existing
        status/timestamp index.
        """
        counts = {
            "playback_corrections_deleted": 0,
            "playback_events_deleted": 0,
            "playback_batches_deleted": 0,
        }
        for status in PlaybackEvent.Status.values:
            while True:
                candidate_ids = list(
                    PlaybackEvent.objects.filter(
                        status=status,
                        started_at__lt=cutoff,
                    )
                    .order_by("started_at")
                    .values_list("pk", flat=True)[:DELETE_BATCH_SIZE]
                )
                if not candidate_ids:
                    break
                with transaction.atomic():
                    locked_ids = list(
                        PlaybackEvent.objects.select_for_update()
                        .filter(pk__in=candidate_ids)
                        .values_list("pk", flat=True)
                    )
                    if not locked_ids:
                        continue
                    correction_count = PlaybackCorrection.objects.filter(
                        event_id__in=locked_ids
                    ).count()
                    event_count = PlaybackEvent.objects.filter(
                        pk__in=locked_ids
                    ).count()
                    PlaybackCorrection.objects.filter(
                        event_id__in=locked_ids
                    ).delete()
                    PlaybackEvent.objects.filter(pk__in=locked_ids).delete()
                    counts["playback_corrections_deleted"] += correction_count
                    counts["playback_events_deleted"] += event_count

        remaining_events = PlaybackEvent.objects.filter(batch_id=OuterRef("pk"))
        while True:
            candidate_ids = list(
                PlaybackBatch.objects.filter(
                    loop_started_at__lt=cutoff,
                )
                .annotate(has_events=Exists(remaining_events))
                .filter(has_events=False)
                .order_by("loop_started_at")
                .values_list("pk", flat=True)[:DELETE_BATCH_SIZE]
            )
            if not candidate_ids:
                break
            with transaction.atomic():
                locked_ids = list(
                    PlaybackBatch.objects.select_for_update()
                    .filter(pk__in=candidate_ids)
                    .annotate(has_events=Exists(remaining_events))
                    .filter(has_events=False)
                    .values_list("pk", flat=True)
                )
                PlaybackBatch.objects.filter(pk__in=locked_ids).delete()
                counts["playback_batches_deleted"] += len(locked_ids)
        return counts
