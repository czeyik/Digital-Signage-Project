from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Exists, Max, OuterRef
from django.utils import timezone

from signage.models import (
    Alert,
    AuditEvent,
    DeviceAssignment,
    DeviceHeartbeat,
    DeviceOperationalEvent,
    Driver,
    Vehicle,
)


class Command(BaseCommand):
    help = "Anonymize former driver and vehicle details after one year."

    def handle(self, *args, **options):
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
        heartbeat_count, _ = DeviceHeartbeat.objects.filter(
            received_at__lt=cutoff
        ).delete()
        operational_count, _ = DeviceOperationalEvent.objects.filter(
            received_at__lt=cutoff
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
