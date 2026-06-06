from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from signage.models import Alert, Device, PlaybackEvent


def open_alert(device, code, severity, message):
    Alert.objects.get_or_create(
        device=device,
        code=code,
        acknowledged_at__isnull=True,
        defaults={"severity": severity, "message": message},
    )


class Command(BaseCommand):
    help = "Evaluate fleet status and create operational alerts."

    def handle(self, *args, **options):
        now = timezone.now()
        for device in Device.objects.all():
            if not device.last_seen_at or device.last_seen_at < now - timedelta(
                hours=48
            ):
                open_alert(
                    device,
                    "offline_48h",
                    Alert.Severity.CRITICAL,
                    "Device has been offline for more than 48 hours.",
                )
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
            power_losses = PlaybackEvent.objects.filter(
                batch__device=device,
                status=PlaybackEvent.Status.INTERRUPTED,
                failure_reason__in=[
                    "external_power_lost",
                    "app_restart_or_power_loss",
                ],
                started_at__gte=now - timedelta(hours=24),
            )
            if power_losses.count() >= 10:
                open_alert(
                    device,
                    "repeated_power_loss",
                    Alert.Severity.WARNING,
                    "Device reported at least 10 power losses within 24 hours.",
                )
            if power_losses.filter(duration_ms__gte=24 * 60 * 60 * 1000).exists():
                open_alert(
                    device,
                    "long_power_interruption",
                    Alert.Severity.WARNING,
                    "Device reported a power interruption longer than 24 hours.",
                )
        self.stdout.write(self.style.SUCCESS("Fleet health evaluated."))
