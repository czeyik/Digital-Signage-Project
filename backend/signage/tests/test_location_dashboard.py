import gzip
import sqlite3
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


@pytest.mark.django_db
@pytest.mark.parametrize(
    "invalid_timestamp",
    ["2026-01-01T00:00:00+24:00", "0000-01-01T00:00:00Z"],
)
def test_location_history_rejects_malformed_timestamps_without_server_error(
    client, invalid_timestamp
):
    user = User.objects.create_user(
        "marketing-locations-invalid-time@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    device = Device.objects.create(label=f"MAP-INVALID-{invalid_timestamp[:4]}")
    client.force_login(user)

    response = client.get(
        reverse("location-history"),
        {"device_id": str(device.id), "start": invalid_timestamp},
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_openmaptiles_style_and_tile_endpoints_use_authenticated_mbtiles(
    client, tmp_path, settings
):
    user = User.objects.create_user(
        "marketing-openmaptiles@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    mbtiles_path = tmp_path / "malaysia.mbtiles"
    with sqlite3.connect(mbtiles_path) as database:
        database.execute(
            "CREATE TABLE tiles ("
            "zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB"
            ")"
        )
        database.execute(
            "INSERT INTO tiles VALUES (?, ?, ?, ?)",
            (1, 0, 1, gzip.compress(b"test-vector-tile")),
        )
    settings.OPENMAPTILES_MBTILES_PATH = str(mbtiles_path)

    assert client.get(reverse("location-style")).status_code == 302
    client.force_login(user)

    page = client.get(reverse("location-map"))
    assert page.status_code == 200
    assert "data-api-key" not in page.content.decode()

    style = client.get(reverse("location-style"))
    assert style.status_code == 200
    assert style.json()["sources"]["openmaptiles"]["url"].endswith(
        "/locations/tiles.json"
    )
    major_roads = next(
        layer
        for layer in style.json()["layers"]
        if layer["id"] == "transportation-major"
    )
    assert major_roads["filter"] == [
        "match",
        ["get", "class"],
        ["motorway", "trunk", "primary", "secondary"],
        True,
        False,
    ]

    tilejson = client.get(reverse("location-tilejson"))
    assert tilejson.status_code == 200
    assert tilejson.json()["scheme"] == "xyz"
    assert tilejson.json()["tiles"][0].endswith(
        "/locations/tiles/{z}/{x}/{y}.pbf"
    )

    tile = client.get(reverse("location-tile", args=[1, 0, 0]))
    assert tile.status_code == 200
    assert tile["Content-Type"] == "application/vnd.mapbox-vector-tile"
    assert tile["Content-Encoding"] == "gzip"
    assert gzip.decompress(tile.content) == b"test-vector-tile"

    assert client.get(reverse("location-tile", args=[1, 2, 0])).status_code == 404
    assert client.get(reverse("location-tile", args=[1, 0, 2])).status_code == 404
