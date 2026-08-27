import logging
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Max
from django.utils import timezone

from signage.models import Alert, Device, DeviceOperationalEvent, PlaybackEvent
from signage.services import open_alert, open_or_escalate_alert

ABNORMAL_APP_EXIT_KIND = DeviceOperationalEvent.Kind.ABNORMAL_APP_EXIT
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Evaluate fleet status and create operational alerts."

    def handle(self, *args, **options):
        started = time.monotonic()
        now = timezone.now()
        active_devices = Device.objects.filter(status=Device.Status.ACTIVE).annotate(
            latest_credential_issued_at=Max("credentials__created_at")
        )
        evaluated_devices = 0
        for device in active_devices:
            evaluated_devices += 1
            # ``last_seen_at`` is set from DeviceHeartbeat.received_at, never
            # from device-provided timestamps. A newly activated device with no
            # heartbeat gets a server-side grace period from credential issue;
            # that is not counted as a heartbeat and only avoids a stale
            # provisioning record raising an immediate alert.
            last_heartbeat_at = (
                device.last_seen_at
                or device.latest_credential_issued_at
                or device.created_at
            )
            unavailable_for = now - last_heartbeat_at
            if unavailable_for >= timedelta(hours=24):
                critical = unavailable_for >= timedelta(hours=48)
                open_or_escalate_alert(
                    device,
                    "device_unavailable",
                    Alert.Severity.CRITICAL
                    if critical
                    else Alert.Severity.WARNING,
                    "Device has not sent a heartbeat for 48 hours."
                    if critical
                    else "Device has not sent a heartbeat for 24 hours.",
                )
            self._evaluate_location_health(device, now)
            if not device.last_sync_at or device.last_sync_at < now - timedelta(days=1):
                severity = (
                    Alert.Severity.CRITICAL
                    if not device.last_sync_at
                    or device.last_sync_at < now - timedelta(days=3)
                    else Alert.Severity.WARNING
                )
                code = (
                    "sync_missing_3d"
                    if severity == Alert.Severity.CRITICAL
                    else "sync_missing_1d"
                )
                open_alert(
                    device,
                    code,
                    severity,
                    "Retrieve device: no sync for three days."
                    if severity == Alert.Severity.CRITICAL
                    else "Device has not synchronized for one day.",
                )
            last_failure_alert = (
                Alert.objects.filter(
                    device=device,
                    code="three_ad_failures",
                    acknowledged_at__isnull=False,
                )
                .order_by("-acknowledged_at")
                .first()
            )
            failures = PlaybackEvent.objects.filter(
                batch__device=device,
                status=PlaybackEvent.Status.FAILED,
            )
            if last_failure_alert:
                failures = failures.filter(
                    started_at__gt=last_failure_alert.acknowledged_at
                )
            failures = failures.count()
            if failures >= 3:
                open_alert(
                    device,
                    "three_ad_failures",
                    Alert.Severity.WARNING,
                    "Device reported at least three advertisement failures.",
                )
            abnormal_app_exits = DeviceOperationalEvent.objects.filter(
                device=device,
                kind=ABNORMAL_APP_EXIT_KIND,
                received_at__gte=now - timedelta(hours=24),
            )
            if abnormal_app_exits.values("id").distinct().count() >= 3:
                open_alert(
                    device,
                    "repeated_abnormal_app_exit",
                    Alert.Severity.WARNING,
                    "Device reported at least three abnormal application exits "
                    "within 24 hours.",
                )
        logger.info(
            "device_health_evaluation_complete devices=%d duration_seconds=%.3f",
            evaluated_devices,
            time.monotonic() - started,
        )
        self.stdout.write(self.style.SUCCESS("Fleet health evaluated."))

    def _evaluate_location_health(self, device, now):
        if device.location_state == "shutdown":
            return
        if device.location_state == "planned_gap" and (
            device.location_planned_gap_until is None
            or device.location_planned_gap_until > now
        ):
            return
        last_report = (
            device.last_location_reported_at
            or device.latest_credential_issued_at
            or device.created_at
        )
        age = now - last_report
        if device.location_state in {
            "permission_disabled",
            "location_disabled",
            "mock",
        }:
            code = {
                "permission_disabled": "location_permission_disabled",
                "location_disabled": "location_disabled",
                "mock": "location_mock",
            }[device.location_state]
            message = {
                "permission_disabled": (
                    "Device precise-location permission is disabled."
                ),
                "location_disabled": "Device location services are disabled.",
                "mock": "Device reported a mock location source.",
            }[device.location_state]
            open_or_escalate_alert(device, code, Alert.Severity.CRITICAL, message)
            return
        if age >= timedelta(minutes=10):
            open_or_escalate_alert(
                device,
                "location_unavailable",
                Alert.Severity.CRITICAL,
                "Device has not reported a valid location for ten minutes.",
            )
        elif age >= timedelta(minutes=3):
            open_or_escalate_alert(
                device,
                "location_stale",
                Alert.Severity.WARNING,
                "Device location report is stale.",
            )
