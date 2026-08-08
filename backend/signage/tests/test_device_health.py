import uuid
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from signage.management.commands import evaluate_device_health as health_command
from signage.models import (
    Alert,
    Device,
    DeviceCredential,
    DeviceHeartbeat,
    DeviceOperationalEvent,
)


def active_device(label, now, *, last_seen_at=None):
    return Device.objects.create(
        label=label,
        status=Device.Status.ACTIVE,
        last_seen_at=last_seen_at,
        last_sync_at=now,
    )


def run_health(monkeypatch, now):
    monkeypatch.setattr(health_command.timezone, "now", lambda: now)
    call_command("evaluate_device_health", verbosity=0)


def abnormal_exit(device, now, *, received_at, reason="crash"):
    event = DeviceOperationalEvent.objects.create(
        id=uuid.uuid4(),
        device=device,
        kind=DeviceOperationalEvent.Kind.ABNORMAL_APP_EXIT,
        recorded_at=now - timedelta(days=30),
        details={"reason": reason},
    )
    DeviceOperationalEvent.objects.filter(pk=event.pk).update(received_at=received_at)
    return event


@pytest.mark.django_db
def test_device_unavailable_alert_uses_24_and_48_hour_boundaries(monkeypatch):
    now = timezone.now().replace(microsecond=0)
    device = active_device(
        "HEALTH-24H",
        now,
        last_seen_at=now - timedelta(hours=23, minutes=59, seconds=59),
    )

    run_health(monkeypatch, now)
    assert not Alert.objects.filter(device=device, code="device_unavailable").exists()

    Device.objects.filter(pk=device.pk).update(last_seen_at=now - timedelta(hours=24))
    run_health(monkeypatch, now)
    alert = Alert.objects.get(device=device, code="device_unavailable")
    assert alert.severity == Alert.Severity.WARNING

    Device.objects.filter(pk=device.pk).update(last_seen_at=now - timedelta(hours=48))
    run_health(monkeypatch, now)
    alert.refresh_from_db()
    assert alert.severity == Alert.Severity.CRITICAL
    assert Alert.objects.filter(
        device=device,
        code="device_unavailable",
        acknowledged_at__isnull=True,
    ).count() == 1


@pytest.mark.django_db
def test_device_health_uses_server_heartbeat_time_and_active_devices_only(monkeypatch):
    now = timezone.now().replace(microsecond=0)
    recent = active_device(
        "HEALTH-RECENT",
        now,
        last_seen_at=now - timedelta(hours=1),
    )
    heartbeat = DeviceHeartbeat.objects.create(
        device=recent,
        recorded_at=now - timedelta(days=30),
        screen_on=True,
        free_storage_bytes=3 * 1024 * 1024 * 1024,
        app_version="1.0.0",
        android_version="13",
    )
    DeviceHeartbeat.objects.filter(pk=heartbeat.pk).update(
        received_at=now - timedelta(hours=1)
    )
    pending = Device.objects.create(
        label="HEALTH-PENDING",
        last_seen_at=now - timedelta(days=3),
        last_sync_at=now,
    )
    disabled = Device.objects.create(
        label="HEALTH-DISABLED",
        status=Device.Status.DISABLED,
        last_seen_at=now - timedelta(days=3),
        last_sync_at=now,
    )

    run_health(monkeypatch, now)

    assert not Alert.objects.filter(device=recent, code="device_unavailable").exists()
    assert not Alert.objects.filter(device=pending, code="device_unavailable").exists()
    assert not Alert.objects.filter(device=disabled, code="device_unavailable").exists()


@pytest.mark.django_db
def test_newly_activated_device_uses_latest_credential_for_heartbeat_grace(
    monkeypatch,
):
    now = timezone.now().replace(microsecond=0)
    device = active_device("HEALTH-CREDENTIAL-GRACE", now)
    Device.objects.filter(pk=device.pk).update(created_at=now - timedelta(days=3))
    credential, _ = DeviceCredential.issue(device)
    DeviceCredential.objects.filter(pk=credential.pk).update(
        created_at=now - timedelta(hours=23, minutes=59, seconds=59)
    )

    run_health(monkeypatch, now)
    assert not Alert.objects.filter(device=device, code="device_unavailable").exists()

    DeviceCredential.objects.filter(pk=credential.pk).update(
        created_at=now - timedelta(hours=24)
    )
    run_health(monkeypatch, now)
    assert Alert.objects.filter(
        device=device,
        code="device_unavailable",
        severity=Alert.Severity.WARNING,
    ).exists()


@pytest.mark.django_db
def test_repeated_abnormal_exits_use_received_time_and_exclude_planned_shutdown(
    monkeypatch,
):
    now = timezone.now().replace(microsecond=0)
    device = active_device("HEALTH-EXITS", now, last_seen_at=now)
    for reason in ("crash", "native_crash"):
        abnormal_exit(device, now, received_at=now - timedelta(hours=1), reason=reason)
    DeviceOperationalEvent.objects.create(
        id=uuid.uuid4(),
        device=device,
        kind=DeviceOperationalEvent.Kind.PLANNED_SHUTDOWN,
        recorded_at=now - timedelta(hours=1),
        details={},
    )

    run_health(monkeypatch, now)
    assert not Alert.objects.filter(
        device=device,
        code="repeated_abnormal_app_exit",
    ).exists()

    abnormal_exit(
        device,
        now,
        received_at=now - timedelta(hours=1),
        reason="anr",
    )
    run_health(monkeypatch, now)
    assert Alert.objects.filter(
        device=device,
        code="repeated_abnormal_app_exit",
        severity=Alert.Severity.WARNING,
    ).count() == 1


@pytest.mark.django_db
def test_oldly_received_abnormal_exits_do_not_open_current_alert(monkeypatch):
    now = timezone.now().replace(microsecond=0)
    device = active_device("HEALTH-OLD-EXITS", now, last_seen_at=now)
    for reason in ("crash", "native_crash", "anr"):
        abnormal_exit(
            device,
            now,
            received_at=now - timedelta(hours=24, seconds=1),
            reason=reason,
        )

    run_health(monkeypatch, now)

    assert not Alert.objects.filter(
        device=device,
        code="repeated_abnormal_app_exit",
    ).exists()
