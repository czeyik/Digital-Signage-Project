import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction
from django.db.models import Exists, Max, OuterRef
from django.utils import timezone

from signage.models import (
    Alert,
    ApiThrottle,
    AuditEvent,
    DeviceAccessToken,
    DeviceAssignment,
    DeviceCredential,
    DeviceHeartbeat,
    DeviceLocationPoint,
    DeviceOperationalEvent,
    Driver,
    EnrollmentChallenge,
    EnrollmentCode,
    LoginThrottle,
    PlaybackBatch,
    PlaybackCorrection,
    PlaybackEvent,
    Vehicle,
)

DELETE_BATCH_SIZE = 5_000


class Command(BaseCommand):
    help = "Apply one-year privacy and operational-record retention."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=365)
        location_cutoff = timezone.now() - timedelta(days=30)
        driver_count = self._anonymize_drivers(cutoff)
        vehicle_count = self._anonymize_vehicles(cutoff)
        playback_counts = self._delete_expired_playback_evidence(cutoff)
        heartbeat_count = self._delete_in_batches(
            DeviceHeartbeat.objects.filter(recorded_at__lt=cutoff)
        )
        location_count = self._delete_in_batches(
            DeviceLocationPoint.objects.filter(recorded_at__lt=location_cutoff)
        )
        operational_count = self._delete_in_batches(
            DeviceOperationalEvent.objects.filter(recorded_at__lt=cutoff)
        )
        alert_count = self._delete_in_batches(
            Alert.objects.filter(created_at__lt=cutoff)
        )
        audit_count = self._delete_in_batches(
            AuditEvent.objects.filter(occurred_at__lt=cutoff)
        )
        auth_counts = self._delete_expired_auth_artifacts()
        AuditEvent.objects.create(
            action="retention.apply",
            target_type="retention_window",
            target_id=cutoff.date().isoformat(),
            metadata={
                "drivers_anonymized": driver_count,
                "vehicles_anonymized": vehicle_count,
                "heartbeats_deleted": heartbeat_count,
                "location_points_deleted": location_count,
                "operational_events_deleted": operational_count,
                **playback_counts,
                "alerts_deleted": alert_count,
                "audit_events_deleted": audit_count,
                **auth_counts,
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Anonymized {driver_count} drivers and {vehicle_count} vehicles; "
                f"removed {heartbeat_count} heartbeats, "
                f"{location_count} location points, "
                f"{operational_count} "
                f"operational events, {alert_count} alerts, and {audit_count} audits."
            )
        )

    def _anonymize_drivers(self, cutoff):
        active_assignments = DeviceAssignment.objects.filter(
            driver=OuterRef("pk"), unassigned_at__isnull=True
        )
        candidates = Driver.objects.annotate(
            active=Exists(active_assignments),
            last_unassigned=Max("deviceassignment__unassigned_at"),
        ).filter(active=False, anonymized_at__isnull=True, last_unassigned__lt=cutoff)
        count = 0
        for driver_id in candidates.values_list("pk", flat=True).iterator():
            with transaction.atomic():
                driver = Driver.objects.select_for_update().get(pk=driver_id)
                assignments = driver.deviceassignment_set
                latest_unassignment = assignments.aggregate(
                    latest=Max("unassigned_at")
                )["latest"]
                if (
                    driver.anonymized_at
                    or assignments.filter(unassigned_at__isnull=True).exists()
                    or not latest_unassignment
                    or latest_unassignment >= cutoff
                ):
                    continue
                driver.name = "Anonymized driver"
                driver.anonymized_at = timezone.now()
                driver.save(update_fields=["name", "anonymized_at", "updated_at"])
                count += 1
        return count

    def _anonymize_vehicles(self, cutoff):
        active_assignments = DeviceAssignment.objects.filter(
            vehicle=OuterRef("pk"), unassigned_at__isnull=True
        )
        candidates = Vehicle.objects.annotate(
            active=Exists(active_assignments),
            last_unassigned=Max("deviceassignment__unassigned_at"),
        ).filter(active=False, anonymized_at__isnull=True, last_unassigned__lt=cutoff)
        count = 0
        for vehicle_id in candidates.values_list("pk", flat=True).iterator():
            if self._anonymize_vehicle(vehicle_id, cutoff):
                count += 1
        return count

    def _anonymize_vehicle(self, vehicle_id, cutoff):
        # A deterministic ANON-<pk> can already exist in imported historic
        # data. The short random suffix fits the 32-character registration
        # column even for a 64-bit primary key and avoids a whole-run rollback.
        for _ in range(10):
            try:
                with transaction.atomic():
                    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle_id)
                    assignments = vehicle.deviceassignment_set
                    latest_unassignment = assignments.aggregate(
                        latest=Max("unassigned_at")
                    )["latest"]
                    if (
                        vehicle.anonymized_at
                        or assignments.filter(unassigned_at__isnull=True).exists()
                        or not latest_unassignment
                        or latest_unassignment >= cutoff
                    ):
                        return False
                    registration = f"ANON-{vehicle.pk}-{secrets.token_hex(3)}"
                    if Vehicle.objects.filter(registration=registration).exists():
                        continue
                    vehicle.registration = registration
                    vehicle.anonymized_at = timezone.now()
                    vehicle.save(
                        update_fields=["registration", "anonymized_at", "updated_at"]
                    )
                    return True
            except IntegrityError:
                continue
        raise RuntimeError("Could not allocate a unique anonymized registration.")

    def _delete_in_batches(self, queryset):
        count = 0
        while True:
            ids = list(
                queryset.order_by("pk")
                .values_list("pk", flat=True)[:DELETE_BATCH_SIZE]
            )
            if not ids:
                return count
            with transaction.atomic():
                deleted, _ = queryset.filter(pk__in=ids).delete()
                count += deleted

    def _delete_expired_auth_artifacts(self):
        now = timezone.now()
        stale_before = now - timedelta(days=settings.AUTH_ARTIFACT_RETENTION_DAYS)
        return {
            "access_tokens_deleted": self._delete_in_batches(
                DeviceAccessToken.objects.filter(expires_at__lt=now)
            ),
            "revoked_credentials_deleted": self._delete_in_batches(
                DeviceCredential.objects.filter(revoked_at__lt=stale_before)
            ),
            "enrollment_challenges_deleted": self._delete_in_batches(
                EnrollmentChallenge.objects.filter(expires_at__lt=stale_before)
            ),
            "enrollment_codes_deleted": self._delete_in_batches(
                EnrollmentCode.objects.filter(expires_at__lt=stale_before)
            ),
            "login_throttles_deleted": self._delete_in_batches(
                LoginThrottle.objects.filter(updated_at__lt=stale_before)
            ),
            "api_throttles_deleted": self._delete_in_batches(
                ApiThrottle.objects.filter(updated_at__lt=stale_before)
            ),
            "sessions_deleted": self._delete_in_batches(
                Session.objects.filter(expire_date__lt=now)
            ),
        }
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
