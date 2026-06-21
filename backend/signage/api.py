import uuid

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import exceptions, serializers, status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .models import (
    Alert,
    Device,
    DeviceAccessToken,
    DeviceCredential,
    DeviceHeartbeat,
    EnrollmentCode,
    PlatformSettings,
    PlaybackBatch,
    PlaybackEvent,
    Playlist,
    token_hash,
)


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is not None:
        response.data = {
            "error": {
                "code": getattr(exc, "default_code", "request_error"),
                "detail": response.data.get("detail", response.data),
            }
        }
    return response


def parse_required_datetime(value, field):
    parsed = parse_datetime(value) if isinstance(value, str) else None
    if not parsed:
        raise serializers.ValidationError({field: "Use an ISO-8601 timestamp."})
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def device_for(request):
    return request.user.device


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def enroll(request):
    code = str(request.data.get("code", "")).strip()
    android_id = str(request.data.get("android_id", "")).strip()
    android_version = str(request.data.get("android_version", "")).strip()
    app_version = str(request.data.get("app_version", "")).strip()
    compromised = bool(request.data.get("integrity_compromised", False))
    if not code or not android_id:
        raise serializers.ValidationError(
            "Enrollment code and Android ID are required."
        )
    try:
        major_android = int(android_version.split(".")[0])
    except (TypeError, ValueError) as exc:
        raise serializers.ValidationError(
            {"android_version": "Invalid Android version."}
        ) from exc
    if compromised or major_android < 12:
        raise exceptions.PermissionDenied("Device integrity requirements were not met.")

    with transaction.atomic():
        enrollment = (
            EnrollmentCode.objects.select_for_update()
            .select_related("device")
            .filter(code_hash=token_hash(code))
            .first()
        )
        if not enrollment or not enrollment.is_usable:
            raise exceptions.AuthenticationFailed("Invalid or expired enrollment code.")
        device = enrollment.device
        if not device.assignments.filter(unassigned_at__isnull=True).exists():
            raise serializers.ValidationError("Device must have an active assignment.")
        android_hash = token_hash(android_id)
        if (
            Device.objects.exclude(pk=device.pk)
            .filter(android_id_hash=android_hash)
            .exists()
        ):
            raise exceptions.PermissionDenied(
                "This Android device is already enrolled."
            )
        device.android_id_hash = android_hash
        device.android_version = android_version
        device.app_version = app_version
        device.status = Device.Status.ACTIVE
        device.save(
            update_fields=[
                "android_id_hash",
                "android_version",
                "app_version",
                "status",
                "updated_at",
            ]
        )
        enrollment.used_at = timezone.now()
        enrollment.save(update_fields=["used_at"])
        DeviceCredential.objects.filter(device=device, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
        credential, refresh_token = DeviceCredential.issue(device)
        access, access_token = DeviceAccessToken.issue(credential)

    return Response(
        {
            "device_id": str(device.id),
            "refresh_token": refresh_token,
            "access_token": access_token,
            "access_token_expires_at": access.expires_at,
            "server_time": timezone.now(),
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def token_refresh(request):
    refresh_token = str(request.data.get("refresh_token", ""))
    credential = (
        DeviceCredential.objects.select_related("device")
        .filter(refresh_hash=token_hash(refresh_token), revoked_at__isnull=True)
        .first()
    )
    if not credential:
        raise exceptions.AuthenticationFailed("Invalid device credential.")
    access, raw = DeviceAccessToken.issue(credential)
    return Response(
        {
            "access_token": raw,
            "access_token_expires_at": access.expires_at,
            "server_time": timezone.now(),
        }
    )


def active_playlist():
    now = timezone.now()
    urgent = (
        Playlist.objects.filter(
            status=Playlist.Status.PUBLISHED,
            is_urgent=True,
            published_at__lte=now,
            starts_at__lte=now,
            ends_at__gt=now,
        )
        .order_by("-published_at")
        .first()
    )
    if urgent:
        return urgent
    scheduled = (
        Playlist.objects.filter(
            status=Playlist.Status.PUBLISHED,
            starts_at__lte=now,
            ends_at__gt=now,
        )
        .order_by("-starts_at", "-version")
        .first()
    )
    if scheduled:
        return scheduled
    return (
        Playlist.objects.filter(
            status=Playlist.Status.PUBLISHED,
            published_at__lte=now,
            starts_at__lte=now,
        )
        .order_by("-published_at")
        .first()
    )


@api_view(["GET"])
def sync_manifest(request):
    device = device_for(request)

    def mark_successful_sync():
        device.last_sync_at = timezone.now()
        device.save(update_fields=["last_sync_at", "updated_at"])

    if device.status == Device.Status.DISABLED:
        mark_successful_sync()
        return Response(
            {
                "mode": "maintenance",
                "server_time": timezone.now(),
                "message": "This display is temporarily unavailable.",
            }
        )
    playlist = active_playlist()
    if not playlist:
        mark_successful_sync()
        return Response(
            {"mode": "fallback", "server_time": timezone.now(), "playlist": None}
        )
    items = playlist.items.select_related("media").all()
    manifest = []
    for item in items:
        media = item.media
        if media.status != media.Status.READY or not media.normalized_file:
            raise exceptions.APIException(
                "Published playlist contains unavailable media."
            )
        manifest.append(
            {
                "entry_id": str(item.id),
                "position": item.position,
                "media_id": str(media.id),
                "kind": media.kind,
                "sha256": media.sha256,
                "size_bytes": media.file_size,
                "duration_ms": media.duration_ms,
                "download_url": media.normalized_file.url,
            }
        )
    mark_successful_sync()
    return Response(
        {
            "mode": "play",
            "server_time": timezone.now(),
            "playlist": {
                "id": str(playlist.id),
                "name": playlist.name,
                "version": playlist.version,
                "urgent": playlist.is_urgent,
                "starts_at": playlist.starts_at,
                "ends_at": playlist.ends_at,
                "items": manifest,
            },
        }
    )


@api_view(["POST"])
def heartbeat(request):
    device = device_for(request)
    recorded_at = parse_required_datetime(
        request.data.get("recorded_at"), "recorded_at"
    )
    free_storage = int(request.data.get("free_storage_bytes", 0))
    app_version = str(request.data.get("app_version", ""))[:32]
    battery_percent = request.data.get("battery_percent")
    if battery_percent is not None:
        battery_percent = min(100, max(0, int(battery_percent)))
    temperature = request.data.get("temperature_celsius")
    hb = DeviceHeartbeat.objects.create(
        device=device,
        recorded_at=recorded_at,
        screen_on=bool(request.data.get("screen_on")),
        external_power=bool(request.data.get("external_power")),
        charging=bool(request.data.get("charging")),
        battery_percent=battery_percent,
        free_storage_bytes=free_storage,
        temperature_celsius=temperature,
        app_version=app_version,
        android_version=str(request.data.get("android_version", ""))[:32],
    )
    device.last_seen_at = timezone.now()
    device.app_version = hb.app_version
    device.android_version = hb.android_version
    device.save(
        update_fields=["last_seen_at", "app_version", "android_version", "updated_at"]
    )
    if free_storage < 2 * 1024 * 1024 * 1024:
        Alert.objects.get_or_create(
            device=device,
            code="low_storage",
            acknowledged_at__isnull=True,
            defaults={
                "severity": Alert.Severity.WARNING,
                "message": "Device has less than 2 GB of free storage.",
            },
        )
    if app_version and app_version != settings.REQUIRED_APP_VERSION:
        Alert.objects.get_or_create(
            device=device,
            code="outdated_app",
            acknowledged_at__isnull=True,
            defaults={
                "severity": Alert.Severity.WARNING,
                "message": (
                    "Device application version does not match the required version."
                ),
            },
        )
    if (
        temperature is not None
        and float(temperature) >= settings.DEVICE_OVERHEAT_CELSIUS
    ):
        Alert.objects.get_or_create(
            device=device,
            code="overheating",
            acknowledged_at__isnull=True,
            defaults={
                "severity": Alert.Severity.CRITICAL,
                "message": "Device reported a temperature above the safe threshold.",
            },
        )
    return Response({"accepted": True, "server_time": timezone.now()})


def validate_event(event, playlist_items):
    event_id = uuid.UUID(str(event["id"]))
    item_id = uuid.UUID(str(event["playlist_item_id"]))
    if item_id not in playlist_items:
        raise serializers.ValidationError("Playback item is not in the playlist.")
    status_value = event.get("status")
    if status_value not in PlaybackEvent.Status.values:
        raise serializers.ValidationError("Invalid playback status.")
    item = playlist_items[item_id]
    duration_ms = max(0, int(event.get("duration_ms", 0)))
    if status_value == PlaybackEvent.Status.COMPLETED:
        tolerance_ms = 500 if item.media.kind == item.media.Kind.VIDEO else 0
        if duration_ms < max(0, item.media.duration_ms - tolerance_ms):
            raise serializers.ValidationError(
                "Completed playback duration is shorter than the media duration."
            )
    return {
        "id": event_id,
        "playlist_item_id": item_id,
        "started_at": parse_required_datetime(event.get("started_at"), "started_at"),
        "ended_at": (
            parse_required_datetime(event.get("ended_at"), "ended_at")
            if event.get("ended_at")
            else None
        ),
        "duration_ms": duration_ms,
        "status": status_value,
        "failure_reason": str(event.get("failure_reason", ""))[:64],
    }


@api_view(["POST"])
def playback_batch(request):
    device = device_for(request)
    if device.status == Device.Status.DISABLED:
        raise exceptions.PermissionDenied("Disabled devices cannot submit playback.")
    try:
        batch_id = uuid.UUID(str(request.data.get("id")))
        playlist_id = uuid.UUID(str(request.data.get("playlist_id")))
    except (TypeError, ValueError) as exc:
        raise serializers.ValidationError(
            "Valid batch and playlist IDs are required."
        ) from exc
    existing = PlaybackBatch.objects.filter(pk=batch_id, device=device).first()
    if existing:
        return Response({"accepted": True, "duplicate": True})
    playlist = Playlist.objects.filter(pk=playlist_id).first()
    if not playlist:
        raise serializers.ValidationError("Unknown playlist.")
    playlist_items = {
        item.id: item for item in playlist.items.select_related("media").all()
    }
    raw_events = request.data.get("events", [])
    if not raw_events or len(raw_events) > PlatformSettings.load().playlist_max_entries:
        raise serializers.ValidationError("A batch must contain valid playlist events.")
    normalized = [validate_event(event, playlist_items) for event in raw_events]
    loop_started_at = parse_required_datetime(
        request.data.get("loop_started_at"), "loop_started_at"
    )
    assignment = (
        device.assignments.filter(assigned_at__lte=loop_started_at)
        .filter(
            models.Q(unassigned_at__isnull=True)
            | models.Q(unassigned_at__gt=loop_started_at)
        )
        .order_by("-assigned_at")
        .first()
    )

    try:
        with transaction.atomic():
            batch = PlaybackBatch.objects.create(
                id=batch_id,
                device=device,
                playlist=playlist,
                assignment=assignment,
                loop_started_at=loop_started_at,
                loop_ended_at=(
                    parse_required_datetime(
                        request.data.get("loop_ended_at"), "loop_ended_at"
                    )
                    if request.data.get("loop_ended_at")
                    else None
                ),
                captured_offline=bool(request.data.get("captured_offline")),
            )
            PlaybackEvent.objects.bulk_create(
                [PlaybackEvent(batch=batch, **event) for event in normalized]
            )
            device.last_playback_at = timezone.now()
            device.save(update_fields=["last_playback_at", "updated_at"])
    except IntegrityError:
        return Response({"accepted": True, "duplicate": True})
    return Response(
        {"accepted": True, "duplicate": False}, status=status.HTTP_201_CREATED
    )
