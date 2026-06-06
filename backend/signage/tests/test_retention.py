from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from signage.models import Device, DeviceAssignment, Driver, Vehicle


@pytest.mark.django_db
def test_retention_uses_final_unassignment_date():
    driver = Driver.objects.create(internal_id="D001", name="Private Name")
    vehicle = Vehicle.objects.create(registration="WXY1234")
    old_device = Device.objects.create(label="OLD")
    recent_device = Device.objects.create(label="RECENT")
    DeviceAssignment.objects.create(
        device=old_device,
        driver=driver,
        vehicle=vehicle,
        assigned_at=timezone.now() - timedelta(days=800),
        unassigned_at=timezone.now() - timedelta(days=700),
    )
    recent = DeviceAssignment.objects.create(
        device=recent_device,
        driver=driver,
        vehicle=vehicle,
        assigned_at=timezone.now() - timedelta(days=100),
        unassigned_at=timezone.now() - timedelta(days=10),
    )

    call_command("apply_retention")
    driver.refresh_from_db()
    assert driver.anonymized_at is None

    recent.unassigned_at = timezone.now() - timedelta(days=400)
    recent.save(update_fields=["unassigned_at"])
    call_command("apply_retention")
    driver.refresh_from_db()
    vehicle.refresh_from_db()
    assert driver.name == "Anonymized driver"
    assert driver.anonymized_at is not None
    assert vehicle.registration.startswith("ANON-")
