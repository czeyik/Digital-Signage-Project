import secrets
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

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

from .integrity import verify_integrity_token
from .models import (
    Alert,
    Device,
    DeviceAccessToken,
    DeviceCredential,
    DeviceHeartbeat,
    DeviceOperationalEvent,
    EnrollmentChallenge,
    EnrollmentCode,
    PlatformSettings,
    PlaybackBatch,
    PlaybackEvent,
    Playlist,
    token_hash,
)
from .services import enforce_api_throttle, open_alert, throttle_wait


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is not None:
        detail = (
            response.data.get("detail", response.data)
            if isinstance(response.data, dict)
            else response.data
        )
        response.data = {
            "error": {
                "code": getattr(exc, "default_code", "request_error"),
                "detail": detail,
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


def enrollment_device_details(data):
    android_id = str(data.get("android_id", "")).strip()
    android_version = str(data.get("android_version", "")).strip()
    app_version = str(data.get("app_version", "")).strip()
    if not android_id:
        raise serializers.ValidationError("Android ID is required.")
    try:
        major_android = int(android_version.split(".")[0])
    except (TypeError, ValueError) as exc:
        raise serializers.ValidationError(
            {"android_version": "Invalid Android version."}
        ) from exc
    if major_android < 12:
        raise exceptions.PermissionDenied("Device integrity requirements were not met.")
    return android_id, android_version[:32], app_version[:32]


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def enrollment_challenge(request):
    enforce_api_throttle(request, "enrollment_challenge", limit=10)
    code = str(request.data.get("code", "")).strip()
    if not code:
        raise serializers.ValidationError("Enrollment code is required.")
    android_id, android_version, app_version = enrollment_device_details(request.data)
    enrollment = (
        EnrollmentCode.objects.select_related("device")
        .filter(code_hash=token_hash(code))
        .first()
    )
    if not enrollment or not enrollment.is_usable:
        raise exceptions.AuthenticationFailed("Invalid or expired enrollment code.")
    if settings.DEPLOYMENT_ENV == "production" and not (
        enrollment.device.hardware_qualification_id
        and enrollment.device.hardware_qualification.approved_for_pilot
    ):
        raise exceptions.PermissionDenied(
            "This device does not have an approved hardware qualification."
        )
    if not enrollment.device.assignments.filter(unassigned_at__isnull=True).exists():
        raise serializers.ValidationError("Device must have an active assignment.")
    android_hash = token_hash(android_id)
    challenge_id = uuid.uuid4()
    request_hash = token_hash(
        f"{challenge_id}:{secrets.token_urlsafe(32)}:{android_hash}:"
        f"{app_version}:{enrollment.code_hash}"
    )
    EnrollmentChallenge.objects.create(
        id=challenge_id,
        enrollment=enrollment,
        request_hash=request_hash,
        android_id_hash=android_hash,
        android_version=android_version,
        app_version=app_version,
        expires_at=timezone.now()
        + timedelta(seconds=settings.ENROLLMENT_CHALLENGE_TTL_SECONDS),
    )
    return Response(
        {
            "challenge_id": str(challenge_id),
            "request_hash": request_hash,
            "cloud_project_number": settings.PLAY_INTEGRITY_PROJECT_NUMBER,
            "expires_at": timezone.now()
            + timedelta(seconds=settings.ENROLLMENT_CHALLENGE_TTL_SECONDS),
        },
        status=status.HTTP_201_CREATED,
    )


def _issue_device_credentials(enrollment, android_hash, android_version, app_version):
    device = enrollment.device
    if settings.DEPLOYMENT_ENV == "production" and not (
        device.hardware_qualification_id
        and device.hardware_qualification.approved_for_pilot
    ):
        raise exceptions.PermissionDenied(
            "This device does not have an approved hardware qualification."
        )
    if not device.assignments.filter(unassigned_at__isnull=True).exists():
        raise serializers.ValidationError("Device must have an active assignment.")
    if (
        Device.objects.exclude(pk=device.pk)
        .filter(android_id_hash=android_hash)
        .exists()
    ):
        raise exceptions.PermissionDenied("This Android device is already enrolled.")
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
    return device, refresh_token, access, access_token


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def enroll(request):
    enforce_api_throttle(request, "enroll", limit=10)
    challenge_id = request.data.get("challenge_id")
    integrity_token = str(request.data.get("integrity_token", ""))
    if challenge_id and integrity_token:
        challenge = (
            EnrollmentChallenge.objects.select_related("enrollment__device")
            .filter(pk=challenge_id)
            .first()
        )
        if (
            not challenge
            or not challenge.is_usable
            or not challenge.enrollment.is_usable
        ):
            raise exceptions.AuthenticationFailed(
                "Invalid or expired enrollment challenge."
            )
        verify_integrity_token(integrity_token, challenge.request_hash)
        with transaction.atomic():
            challenge = (
                EnrollmentChallenge.objects.select_for_update()
                .select_related("enrollment__device")
                .get(pk=challenge.pk)
            )
            enrollment = EnrollmentCode.objects.select_for_update().get(
                pk=challenge.enrollment_id
            )
            if not challenge.is_usable or not enrollment.is_usable:
                raise exceptions.AuthenticationFailed(
                    "Invalid or expired enrollment challenge."
                )
            challenge.used_at = timezone.now()
            challenge.save(update_fields=["used_at"])
            device, refresh_token, access, access_token = _issue_device_credentials(
                enrollment,
                challenge.android_id_hash,
                challenge.android_version,
                challenge.app_version,
            )
    elif settings.DEPLOYMENT_ENV != "production":
        code = str(request.data.get("code", "")).strip()
        android_id, android_version, app_version = enrollment_device_details(
            request.data
        )
        if bool(request.data.get("integrity_compromised", False)):
            raise exceptions.PermissionDenied(
                "Device integrity requirements were not met."
            )
        with transaction.atomic():
            enrollment = (
                EnrollmentCode.objects.select_for_update()
                .select_related("device")
                .filter(code_hash=token_hash(code))
                .first()
            )
            if not enrollment or not enrollment.is_usable:
                raise exceptions.AuthenticationFailed(
                    "Invalid or expired enrollment code."
                )
            device, refresh_token, access, access_token = _issue_device_credentials(
                enrollment, token_hash(android_id), android_version, app_version
            )
    else:
        raise serializers.ValidationError(
            "A verified enrollment challenge and integrity token are required."
        )

    return Response(
        {
            "device_id": str(device.id),
            "refresh_token": refresh_token,
            "access_token": access_token,
            "access_token_expires_at": access.expires_at,
            "server_time": timezone.now(),
            "kiosk_pin_verifier": device.kiosk_pin_hash,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def token_refresh(request):
    enforce_api_throttle(request, "token_refresh", limit=20)
    refresh_token = str(request.data.get("refresh_token", ""))
    credential = (
        DeviceCredential.objects.select_related("device")
        .filter(refresh_hash=token_hash(refresh_token), revoked_at__isnull=True)
        .first()
    )
    if not credential:
        if throttle_wait(
            request, "invalid_device_refresh", limit=5, window_seconds=900
        ):
            open_alert(
                None,
                "repeated_device_authentication",
                Alert.Severity.WARNING,
                "Repeated device token refresh requests used invalid credentials.",
            )
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
                "kiosk_pin_verifier": device.kiosk_pin_hash,
            }
        )
    playlist = active_playlist()
    if not playlist:
        mark_successful_sync()
        return Response(
            {
                "mode": "fallback",
                "server_time": timezone.now(),
                "playlist": None,
                "kiosk_pin_verifier": device.kiosk_pin_hash,
            }
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
            "kiosk_pin_verifier": device.kiosk_pin_hash,
            "playlist": {
                "id": str(playlist.id),
                "name": playlist.name,
                "version": playlist.version,
                "urgent": playlist.is_urgent,
                "starts_at": playlist.starts_at,
                "ends_at": playlist.ends_at,
                "required_app_version": settings.REQUIRED_APP_VERSION,
                "media_cache_bytes": settings.DEVICE_MEDIA_CACHE_BYTES,
                "event_queue_bytes": settings.DEVICE_EVENT_QUEUE_BYTES,
                "minimum_free_bytes": settings.DEVICE_MIN_FREE_BYTES,
                "sync_timezone": settings.TIME_ZONE,
                "daily_sync_local_time": "00:00:00",
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
    try:
        free_storage = int(request.data.get("free_storage_bytes", 0))
    except (TypeError, ValueError) as exc:
        raise serializers.ValidationError("Invalid free storage value.") from exc
    if free_storage < 0:
        raise serializers.ValidationError("Free storage cannot be negative.")
    app_version = str(request.data.get("app_version", ""))[:32]
    battery_percent = request.data.get("battery_percent")
    if battery_percent is not None:
        try:
            battery_percent = int(battery_percent)
        except (TypeError, ValueError) as exc:
            raise serializers.ValidationError("Invalid battery percentage.") from exc
        if not 0 <= battery_percent <= 100:
            raise serializers.ValidationError(
                "Battery percentage must be between 0 and 100."
            )
    temperature = request.data.get("temperature_celsius")
    if temperature is not None:
        try:
            temperature = Decimal(str(temperature))
        except InvalidOperation as exc:
            raise serializers.ValidationError("Invalid temperature value.") from exc
        if not temperature.is_finite() or not Decimal("-50") <= temperature <= 150:
            raise serializers.ValidationError("Temperature is outside safe bounds.")
    active_playlist = None
    active_playlist_id = request.data.get("active_playlist_id")
    if active_playlist_id:
        try:
            active_playlist_id = uuid.UUID(str(active_playlist_id))
        except (TypeError, ValueError) as exc:
            raise serializers.ValidationError("Invalid active playlist ID.") from exc
        active_playlist = Playlist.objects.filter(pk=active_playlist_id).first()
    last_sync = (
        parse_required_datetime(
            request.data.get("last_successful_sync_at"), "last_successful_sync_at"
        )
        if request.data.get("last_successful_sync_at")
        else None
    )
    last_playback = (
        parse_required_datetime(
            request.data.get("last_playback_at"), "last_playback_at"
        )
        if request.data.get("last_playback_at")
        else None
    )
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
        active_playlist=active_playlist,
        playback_active=bool(request.data.get("playback_active")),
        last_successful_sync_at=last_sync,
        last_playback_at=last_playback,
    )
    device.last_seen_at = timezone.now()
    device.app_version = hb.app_version
    device.android_version = hb.android_version
    device.current_playlist = active_playlist
    if last_sync:
        device.last_sync_at = last_sync
    if last_playback:
        device.last_playback_at = last_playback
    device.save(
        update_fields=[
            "last_seen_at",
            "app_version",
            "android_version",
            "current_playlist",
            "last_sync_at",
            "last_playback_at",
            "updated_at",
        ]
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
        and device.hardware_qualification_id
        and device.hardware_qualification.approved_for_pilot
        and device.hardware_qualification.thermal_passed
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


@api_view(["POST"])
def operational_event(request):
    device = device_for(request)
    if device.status == Device.Status.DISABLED:
        raise exceptions.PermissionDenied("Disabled devices cannot submit events.")
    kind = request.data.get("kind")
    if kind not in DeviceOperationalEvent.Kind.values:
        raise serializers.ValidationError("Invalid operational event kind.")
    try:
        event_id = uuid.UUID(str(request.data.get("id")))
    except (TypeError, ValueError) as exc:
        raise serializers.ValidationError(
            "A valid operational event ID is required."
        ) from exc
    existing = DeviceOperationalEvent.objects.filter(pk=event_id).first()
    if existing:
        if existing.device_id != device.id:
            raise exceptions.PermissionDenied("Operational event identifier collision.")
        return Response({"accepted": True, "duplicate": True, "id": str(event_id)})
    try:
        event = DeviceOperationalEvent.objects.create(
            id=event_id,
            device=device,
            kind=kind,
            recorded_at=parse_required_datetime(
                request.data.get("recorded_at"), "recorded_at"
            ),
            details=request.data.get("details", {}),
        )
    except IntegrityError:
        if DeviceOperationalEvent.objects.filter(pk=event_id, device=device).exists():
            return Response(
                {"accepted": True, "duplicate": True, "id": str(event_id)}
            )
        raise serializers.ValidationError(
            "Operational event identifier collision."
        ) from None
    return Response(
        {"accepted": True, "duplicate": False, "id": str(event.id)},
        status=status.HTTP_201_CREATED,
    )


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
    existing = PlaybackBatch.objects.filter(pk=batch_id).first()
    if existing:
        if existing.device_id != device.id:
            raise exceptions.PermissionDenied("Playback batch identifier collision.")
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
    submitted_items = [event["playlist_item_id"] for event in normalized]
    if len(submitted_items) != len(set(submitted_items)):
        raise serializers.ValidationError("A batch cannot contain duplicate entries.")
    if set(submitted_items) != set(playlist_items):
        raise serializers.ValidationError(
            "A batch must contain exactly one result for every playlist entry."
        )
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
        if PlaybackBatch.objects.filter(pk=batch_id, device=device).exists():
            return Response({"accepted": True, "duplicate": True})
        raise serializers.ValidationError(
            "Playback evidence identifier collision; the batch was not accepted."
        ) from None
    return Response(
        {"accepted": True, "duplicate": False}, status=status.HTTP_201_CREATED
    )
