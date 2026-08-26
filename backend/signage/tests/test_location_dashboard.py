import uuid
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from signage.models import (
    Device,
    DeviceAssignment,
    DeviceLocationPoint,
    Driver,
    User,
    Vehicle,
)


@pytest.mark.django_db
def test_location_dashboard_latest_and_history_expose_only_driver_internal_id(client):
    user = User.objects.create_user(
        "marketing-locations@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    device = Device.objects.create(
        label="MAP-01",
        status=Device.Status.ACTIVE,
        location_state="fresh",
        last_location_reported_at=timezone.now(),
    )
    assignment = DeviceAssignment.objects.create(
        device=device,
        driver=Driver.objects.create(internal_id="D-MAP", name="Private Driver Name"),
        vehicle=Vehicle.objects.create(registration="MAP1234"),
        assigned_at=timezone.now() - timedelta(hours=1),
    )
    point = DeviceLocationPoint.objects.create(
        id=uuid.uuid4(),
        device=device,
        assignment=assignment,
        recorded_at=timezone.now() - timedelta(minutes=1),
        device_recorded_at=timezone.now() - timedelta(minutes=1),
        latitude="3.139000",
        longitude="101.686900",
        accuracy_m="12.50",
        provider="gps",
        source="location_manager",
    )
    client.force_login(user)

    latest = client.get(reverse("location-latest"))
    assert latest.status_code == 200
    body = latest.json()
    assert body["devices"][0]["point"]["driver_internal_id"] == "D-MAP"
    assert "Private Driver Name" not in latest.content.decode()

    history = client.get(
        reverse("location-history"),
        {"device_id": str(device.id)},
    )
    assert history.status_code == 200
    assert history.json()["points"][0]["id"] == str(point.id)
    assert "Private Driver Name" not in history.content.decode()


@pytest.mark.django_db
def test_location_history_rejects_ranges_over_24_hours(client):
    user = User.objects.create_user(
        "marketing-locations-range@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    device = Device.objects.create(label="MAP-RANGE", status=Device.Status.ACTIVE)
    client.force_login(user)
    now = timezone.now()
    response = client.get(
        reverse("location-history"),
        {
            "device_id": str(device.id),
            "start": (now - timedelta(hours=25)).isoformat(),
            "end": now.isoformat(),
        },
    )
    assert response.status_code == 400
