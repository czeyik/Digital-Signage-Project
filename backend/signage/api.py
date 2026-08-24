import io
import ipaddress
import secrets
import uuid
import zlib
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import exceptions, serializers, status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    parser_classes,
    permission_classes,
)
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .authentication import DeviceAccessTokenAuthentication
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
    PlaybackBatch,
    PlaybackEvent,
    Playlist,
    token_hash,
)
from .services import (
    active_playlist,
    enforce_api_throttle,
    open_alert,
    open_or_escalate_alert,
    throttle_wait,
)

MAX_DEVICE_TIMESTAMP_FUTURE_SKEW = timedelta(minutes=5)
MAX_PLAYBACK_EVENT_DURATION_MS = 2_147_483_647
PLAYBACK_TIMESTAMP_TOLERANCE = timedelta(seconds=5)
PLAYBACK_FAILURE_REASONS_BY_STATUS = {
    PlaybackEvent.Status.FAILED: frozenset(
        {"decode_failure", "missing_file", "playback_timeout", "start_timeout"}
    ),
    PlaybackEvent.Status.INTERRUPTED: frozenset(
        {
            "administrator_session",
            "app_restart_or_power_loss",
            "app_restart_or_unexpected_exit",
            "credential_rejected",
            "device_disabled",
            "device_owner_removed",
            "external_power_lost",
            "external_power_unavailable",
            "fallback_mode",
            "loop_interrupted_before_entry",
            "planned_shutdown",
            "server_forbidden",
            "urgent_playlist_replacement",
        }
    ),
}
ABNORMAL_APP_EXIT_REASONS = frozenset(
    {
        "crash",
        "native_crash",
        "anr",
        "initialization_failure",
        "low_memory",
        "excessive_resource_usage",
        "freezer_termination",
    }
)
REPLACEMENT_FAILURE_STAGES = frozenset({"preparation", "activation"})


class PlaybackBatchPayloadTooLarge(exceptions.APIException):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    default_detail = "Playback batch exceeds the allowed size."
    default_code = "playback_batch_too_large"


class UnsupportedPlaybackBatchEncoding(exceptions.APIException):
    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    default_detail = "Playback batches require Content-Encoding: gzip."
    default_code = "unsupported_content_encoding"


def _decompress_gzip_limited(payload, maximum_bytes):
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        decoded = decompressor.decompress(payload, maximum_bytes + 1)
        if len(decoded) > maximum_bytes or decompressor.unconsumed_tail:
            raise PlaybackBatchPayloadTooLarge
        decoded += decompressor.flush(maximum_bytes + 1 - len(decoded))
    except zlib.error as exc:
        raise exceptions.ParseError("Playback batch is not valid gzip data.") from exc
    if len(decoded) > maximum_bytes:
        raise PlaybackBatchPayloadTooLarge
    if not decompressor.eof or decompressor.unused_data:
        raise exceptions.ParseError(
            "Playback batch is truncated or malformed gzip data."
        )
    return decoded


def _read_limited(stream, maximum_bytes):
    chunks = []
    remaining = maximum_bytes + 1
    while remaining:
        chunk = stream.read(min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > maximum_bytes:
        raise PlaybackBatchPayloadTooLarge
    return payload


class GzipPlaybackBatchJSONParser(JSONParser):
    """Parse one bounded gzip member containing a JSON playback batch."""

    def parse(self, stream, media_type=None, parser_context=None):
        parser_context = parser_context or {}
        request = parser_context.get("request")
        content_encoding = (
            request.headers.get("Content-Encoding", "").strip().lower()
            if request is not None
            else ""
        )
        if content_encoding != "gzip":
            raise UnsupportedPlaybackBatchEncoding

        compressed_limit = settings.PLAYBACK_BATCH_MAX_COMPRESSED_BYTES
        content_length = request.META.get("CONTENT_LENGTH", "") if request else ""
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError):
            declared_length = None
        if declared_length is not None and declared_length > compressed_limit:
            raise PlaybackBatchPayloadTooLarge

        compressed = _read_limited(stream, compressed_limit)
        decoded = _decompress_gzip_limited(
            compressed, settings.PLAYBACK_BATCH_MAX_DECOMPRESSED_BYTES
        )
        parsed = super().parse(
            io.BytesIO(decoded), media_type=media_type, parser_context=parser_context
        )
        if not isinstance(parsed, dict):
            raise exceptions.ParseError("Playback batch JSON must be an object.")
        return parsed


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
    try:
        parsed = parse_datetime(value) if isinstance(value, str) else None
        if parsed and timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    except (OverflowError, TypeError, ValueError):
        parsed = None
    if not parsed:
        raise serializers.ValidationError({field: "Use an ISO-8601 timestamp."})
    if parsed > timezone.now() + MAX_DEVICE_TIMESTAMP_FUTURE_SKEW:
        raise serializers.ValidationError(
            {field: "Timestamp is too far ahead of server time."}
        )
    return parsed


def optional_json_boolean(data, field):
    """Return an optional JSON boolean without truthiness coercion.

    Legacy players send power telemetry, while the battery-backed player omits
    it.  Treating arbitrary values such as the string ``"false"`` as truthy
    would corrupt the historic telemetry retained during that transition.
    """

    value = data.get(field)
    if value is None:
        return None
    if type(value) is not bool:
        raise serializers.ValidationError({field: "Use a JSON boolean or null."})
    return value


def required_json_boolean(data, field):
    if field not in data or type(data[field]) is not bool:
        raise serializers.ValidationError({field: "Use a JSON boolean."})
    return data[field]


def required_short_string(data, field, maximum_length):
    value = data.get(field)
    if not isinstance(value, str):
        raise serializers.ValidationError({field: "Use a non-empty string."})
    value = value.strip()
    if not value or len(value) > maximum_length:
        raise serializers.ValidationError(
            {field: f"Use a non-empty string of at most {maximum_length} characters."}
        )
    return value


def device_for(request):
    return request.user.device


def configured_media_origin():
    domain = str(getattr(settings, "AWS_S3_CUSTOM_DOMAIN", "")).strip().lower()
    parsed = None
    origin = None
    port = None
    try:
        parsed = urlsplit(f"https://{domain}")
        origin = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        pass
    has_ip_origin = False
    if origin:
        try:
            ipaddress.ip_address(origin)
        except ValueError:
            pass
        else:
            has_ip_origin = True
    if (
        parsed is None
        or not origin
        or parsed.netloc != domain
        or origin != domain
        or port is not None
        or origin == "localhost"
        or origin.endswith(".")
        or "." not in origin
        or has_ip_origin
    ):
        raise exceptions.APIException("Media delivery is unavailable.")
    return origin


def media_url_uses_origin(download_url, origin):
    if not isinstance(download_url, str):
        return False
    try:
        parsed = urlsplit(download_url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == origin
            and parsed.port is None
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and parsed.path.startswith("/")
        )
    except ValueError:
        return False


def enrollment_device_details(data, *, require_hardware=False):
    android_id = required_short_string(data, "android_id", 255)
    android_version = required_short_string(data, "android_version", 32)
    app_version = required_short_string(data, "app_version", 32)
    try:
        major_android = int(android_version.split(".")[0])
    except (TypeError, ValueError) as exc:
        raise serializers.ValidationError(
            {"android_version": "Invalid Android version."}
        ) from exc
    if major_android < 12:
        raise exceptions.PermissionDenied("Device integrity requirements were not met.")
    hardware_model = firmware_version = security_patch_level = ""
    if require_hardware:
        hardware_model = required_short_string(data, "hardware_model", 160)
        firmware_version = required_short_string(data, "firmware_version", 100)
        security_patch_level = required_short_string(data, "security_patch_level", 32)
    return (
        android_id,
        android_version,
        app_version,
        hardware_model,
        firmware_version,
        security_patch_level,
    )


def device_matches_qualification(
    device,
    *,
    hardware_model,
    firmware_version,
    security_patch_level,
):
    qualification = device.hardware_qualification
    return bool(
        qualification
        and qualification.is_enrollment_eligible
        and qualification.model_name == hardware_model
        and qualification.firmware_version == firmware_version
        and qualification.security_patch_level == security_patch_level
    )


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def enrollment_challenge(request):
    enforce_api_throttle(request, "enrollment_challenge", limit=10)
    code = str(request.data.get("code", "")).strip()
    if not code:
        raise serializers.ValidationError("Enrollment code is required.")
    (
        android_id,
        android_version,
        app_version,
        hardware_model,
        firmware_version,
        security_patch_level,
    ) = enrollment_device_details(
        request.data,
        require_hardware=settings.DEPLOYMENT_ENV == "production",
    )
    enrollment = (
        EnrollmentCode.objects.select_related("device__hardware_qualification")
        .filter(code_hash=token_hash(code))
        .first()
    )
    if not enrollment or not enrollment.is_usable:
        raise exceptions.AuthenticationFailed("Invalid or expired enrollment code.")
    if settings.DEPLOYMENT_ENV == "production" and not device_matches_qualification(
        enrollment.device,
        hardware_model=hardware_model,
        firmware_version=firmware_version,
        security_patch_level=security_patch_level,
    ):
        raise exceptions.PermissionDenied(
            "This device does not match its enrollment-eligible hardware record."
        )
    if not enrollment.device.assignments.filter(unassigned_at__isnull=True).exists():
        raise serializers.ValidationError("Device must have an active assignment.")
    android_hash = token_hash(android_id)
    challenge_id = uuid.uuid4()
    request_hash = token_hash(
        f"{challenge_id}:{secrets.token_urlsafe(32)}:{android_hash}:"
        f"{app_version}:{hardware_model}:{firmware_version}:{security_patch_level}:"
        f"{enrollment.code_hash}"
    )
    EnrollmentChallenge.objects.create(
        id=challenge_id,
        enrollment=enrollment,
        request_hash=request_hash,
        android_id_hash=android_hash,
        android_version=android_version,
        app_version=app_version,
        hardware_model=hardware_model,
        firmware_version=firmware_version,
        security_patch_level=security_patch_level,
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


def _issue_device_credentials(
    enrollment,
    android_hash,
    android_version,
    app_version,
    hardware_model="",
    firmware_version="",
    security_patch_level="",
):
    device = enrollment.device
    if device.status == Device.Status.DISABLED or not device.kiosk_pin_hash:
        raise exceptions.AuthenticationFailed("Invalid or expired enrollment code.")
    if settings.DEPLOYMENT_ENV == "production" and not device_matches_qualification(
        device,
        hardware_model=hardware_model,
        firmware_version=firmware_version,
        security_patch_level=security_patch_level,
    ):
        raise exceptions.PermissionDenied(
            "This device does not match its enrollment-eligible hardware record."
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
    device.hardware_model = hardware_model
    device.hardware_firmware_version = firmware_version
    device.hardware_security_patch = security_patch_level
    device.status = Device.Status.ACTIVE
    device.save(
        update_fields=[
            "android_id_hash",
            "android_version",
            "app_version",
            "hardware_model",
            "hardware_firmware_version",
            "hardware_security_patch",
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
            device = Device.objects.select_for_update().get(
                pk=challenge.enrollment.device_id
            )
            challenge = (
                EnrollmentChallenge.objects.select_for_update()
                .get(pk=challenge.pk)
            )
            enrollment = EnrollmentCode.objects.select_for_update().get(
                pk=challenge.enrollment_id
            )
            enrollment.device = device
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
                challenge.hardware_model,
                challenge.firmware_version,
                challenge.security_patch_level,
            )
    elif settings.DEPLOYMENT_ENV != "production":
        code = str(request.data.get("code", "")).strip()
        (
            android_id,
            android_version,
            app_version,
            hardware_model,
            firmware_version,
            security_patch_level,
        ) = enrollment_device_details(request.data)
        if bool(request.data.get("integrity_compromised", False)):
            raise exceptions.PermissionDenied(
                "Device integrity requirements were not met."
            )
        enrollment_candidate = EnrollmentCode.objects.filter(
            code_hash=token_hash(code)
        ).values("pk", "device_id").first()
        if not enrollment_candidate:
            raise exceptions.AuthenticationFailed(
                "Invalid or expired enrollment code."
            )
        with transaction.atomic():
            device = Device.objects.select_for_update().get(
                pk=enrollment_candidate["device_id"]
            )
            enrollment = EnrollmentCode.objects.select_for_update().get(
                pk=enrollment_candidate["pk"]
            )
            enrollment.device = device
            if not enrollment.is_usable:
                raise exceptions.AuthenticationFailed(
                    "Invalid or expired enrollment code."
                )
            device, refresh_token, access, access_token = _issue_device_credentials(
                enrollment,
                token_hash(android_id),
                android_version,
                app_version,
                hardware_model,
                firmware_version,
                security_patch_level,
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
@authentication_classes([DeviceAccessTokenAuthentication])
@permission_classes([AllowAny])
def token_refresh(request):
    enforce_api_throttle(request, "token_refresh", limit=20)
    refresh_token = str(request.data.get("refresh_token", ""))
    credential = (
        DeviceCredential.objects.select_related("device")
        .filter(refresh_hash=token_hash(refresh_token))
        .first()
    )
    # Refresh credentials are no longer usable after either explicit revocation
    # or device disable.  Return the same 401 outcome for legacy disabled rows
    # as for revoked rows: the player must clear local credentials and wait for
    # owner-issued re-enrollment, rather than treating refresh as maintenance.
    if (
        not credential
        or credential.revoked_at
        or credential.device.status == Device.Status.DISABLED
    ):
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


@api_view(["GET"])
def sync_manifest(request):
    device = device_for(request)

    def mark_successful_sync():
        device.last_sync_at = timezone.now()
        device.save(update_fields=["last_sync_at", "updated_at"])

    missing_enrollment_eligible_hardware = (
        settings.DEPLOYMENT_ENV == "production"
        and device.status == Device.Status.ACTIVE
        and not device_matches_qualification(
            device,
            hardware_model=device.hardware_model,
            firmware_version=device.hardware_firmware_version,
            security_patch_level=device.hardware_security_patch,
        )
    )
    if device.status == Device.Status.DISABLED:
        # Authentication normally rejects this before the view; keep the
        # defense-in-depth path aligned with credential revocation.
        raise exceptions.AuthenticationFailed("Invalid or expired device token.")
    if missing_enrollment_eligible_hardware:
        # Keep an active device in maintenance when its exact, attested hardware
        # identity becomes ineligible. Physical checklist completion remains a
        # pilot-approval gate but does not control this enrollment/sync path.
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
    media_origin = configured_media_origin()
    manifest = []
    for item in items:
        media = item.media
        if media.status != media.Status.READY or not media.normalized_file:
            raise exceptions.APIException(
                "Published playlist contains unavailable media."
            )
        download_url = media.normalized_file.url
        if not media_url_uses_origin(download_url, media_origin):
            raise exceptions.APIException("Media delivery is unavailable.")
        manifest.append(
            {
                "entry_id": str(item.id),
                "position": item.position,
                "media_id": str(media.id),
                "kind": media.kind,
                "sha256": media.sha256,
                "size_bytes": media.file_size,
                "duration_ms": media.duration_ms,
                "download_url": download_url,
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
                "media_origin": media_origin,
                "sync_timezone": settings.TIME_ZONE,
                "daily_sync_local_time": "00:00:00",
                "items": manifest,
            },
        }
    )


@api_view(["POST"])
def heartbeat(request):
    authenticated_device = device_for(request)
    recorded_at = parse_required_datetime(
        request.data.get("recorded_at"), "recorded_at"
    )
    try:
        free_storage = int(request.data.get("free_storage_bytes", 0))
    except (TypeError, ValueError) as exc:
        raise serializers.ValidationError("Invalid free storage value.") from exc
    if free_storage < 0:
        raise serializers.ValidationError("Free storage cannot be negative.")
    app_version = required_short_string(request.data, "app_version", 32)
    android_version = required_short_string(request.data, "android_version", 32)
    screen_on = required_json_boolean(request.data, "screen_on")
    playback_active = required_json_boolean(request.data, "playback_active")
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
    reported_playlist = None
    active_playlist_id = request.data.get("active_playlist_id")
    if active_playlist_id:
        try:
            active_playlist_id = uuid.UUID(str(active_playlist_id))
        except (TypeError, ValueError) as exc:
            raise serializers.ValidationError("Invalid active playlist ID.") from exc
        reported_playlist = Playlist.objects.filter(pk=active_playlist_id).first()
        if reported_playlist is None:
            raise serializers.ValidationError("Unknown active playlist ID.")
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
    with transaction.atomic():
        device = (
            Device.objects.select_for_update(of=("self",))
            .select_related("hardware_qualification")
            .get(pk=authenticated_device.pk)
        )
        hb = DeviceHeartbeat.objects.create(
            device=device,
            recorded_at=recorded_at,
            screen_on=screen_on,
            external_power=optional_json_boolean(request.data, "external_power"),
            charging=optional_json_boolean(request.data, "charging"),
            battery_percent=battery_percent,
            free_storage_bytes=free_storage,
            temperature_celsius=temperature,
            app_version=app_version,
            android_version=android_version,
            active_playlist=reported_playlist,
            playback_active=playback_active,
            last_successful_sync_at=last_sync,
            last_playback_at=last_playback,
        )
        # Receipt time proves liveness, while device-derived aggregate state may
        # only move forward with the newest reported heartbeat.
        device.last_seen_at = hb.received_at
        update_fields = ["last_seen_at", "updated_at"]
        if (
            device.last_heartbeat_recorded_at is None
            or recorded_at >= device.last_heartbeat_recorded_at
        ):
            device.last_heartbeat_recorded_at = recorded_at
            device.app_version = hb.app_version
            device.android_version = hb.android_version
            device.current_playlist = reported_playlist
            update_fields.extend(
                [
                    "last_heartbeat_recorded_at",
                    "app_version",
                    "android_version",
                    "current_playlist",
                ]
            )
        if last_sync and (
            device.last_sync_at is None or last_sync > device.last_sync_at
        ):
            device.last_sync_at = last_sync
            update_fields.append("last_sync_at")
        if last_playback and (
            device.last_playback_at is None or last_playback > device.last_playback_at
        ):
            device.last_playback_at = last_playback
            update_fields.append("last_playback_at")
        device.save(update_fields=update_fields)
        if free_storage < 2 * 1024 * 1024 * 1024:
            open_or_escalate_alert(
                device,
                "low_storage",
                Alert.Severity.WARNING,
                "Device has less than 2 GB of free storage.",
            )
        if battery_percent is not None and battery_percent <= 20:
            critical = battery_percent <= 10
            open_or_escalate_alert(
                device,
                "low_battery",
                Alert.Severity.CRITICAL if critical else Alert.Severity.WARNING,
                "Device battery is at or below 10%."
                if critical
                else "Device battery is at or below 20%.",
            )
        if app_version != settings.REQUIRED_APP_VERSION:
            open_or_escalate_alert(
                device,
                "outdated_app",
                Alert.Severity.WARNING,
                "Device application version does not match the required version.",
            )
        if (
            temperature is not None
            and device.hardware_qualification_id
            and device.hardware_qualification.approved_for_pilot
            and device.hardware_qualification.thermal_passed
            and float(temperature) >= settings.DEVICE_OVERHEAT_CELSIUS
        ):
            open_or_escalate_alert(
                device,
                "overheating",
                Alert.Severity.CRITICAL,
                "Device reported a temperature above the safe threshold.",
            )
    return Response({"accepted": True, "server_time": timezone.now()})


def validate_operational_event_details(kind, data):
    details_provided = "details" in data
    details = data.get("details", {})
    if not isinstance(details, dict):
        raise serializers.ValidationError({"details": "Details must be a JSON object."})
    if kind == DeviceOperationalEvent.Kind.FORCED_QUEUE_LOSS:
        expected = {
            "removed_batches",
            "estimated_removed_bytes",
            "target_removed_bytes",
        }
        if set(details) != expected or any(
            type(details[field]) is not int or details[field] < 0
            for field in expected
        ):
            raise serializers.ValidationError(
                {
                    "details": (
                        "Forced queue loss details must contain non-negative integer "
                        "removed_batches, estimated_removed_bytes, and "
                        "target_removed_bytes values."
                    )
                }
            )
    elif kind == DeviceOperationalEvent.Kind.REPLACEMENT_FAILED:
        if set(details) != {"playlist_id", "stage"}:
            raise serializers.ValidationError(
                {
                    "details": (
                        "Replacement failure details must contain only playlist_id "
                        "and stage."
                    )
                }
            )
        try:
            uuid.UUID(details["playlist_id"])
        except (TypeError, ValueError, AttributeError) as exc:
            raise serializers.ValidationError(
                {"details.playlist_id": "Use a playlist UUID."}
            ) from exc
        if (
            not isinstance(details["stage"], str)
            or details["stage"] not in REPLACEMENT_FAILURE_STAGES
        ):
            raise serializers.ValidationError(
                {"details.stage": "Use a recognized replacement stage."}
            )
    elif kind == DeviceOperationalEvent.Kind.PLANNED_SHUTDOWN:
        if not details_provided or details != {}:
            raise serializers.ValidationError(
                {"details": "Planned shutdown details must be exactly an empty object."}
            )
    elif kind == DeviceOperationalEvent.Kind.ABNORMAL_APP_EXIT:
        if set(details) != {"reason"}:
            raise serializers.ValidationError(
                {
                    "details": (
                        "Abnormal application exit details must contain only a reason."
                    )
                }
            )
        reason = details["reason"]
        if not isinstance(reason, str) or reason not in ABNORMAL_APP_EXIT_REASONS:
            raise serializers.ValidationError(
                {"details.reason": "Use a recognized abnormal exit reason."}
            )
    return details


def operational_event_matches(existing, *, kind, recorded_at, details):
    return (
        existing.kind == kind
        and existing.recorded_at == recorded_at
        and existing.details == details
    )


@api_view(["POST"])
def operational_event(request):
    device = device_for(request)
    if device.status == Device.Status.DISABLED:
        raise exceptions.PermissionDenied("Disabled devices cannot submit events.")
    kind = request.data.get("kind")
    unexpected_fields = set(request.data).difference(
        {"id", "kind", "recorded_at", "details"}
    )
    if unexpected_fields:
        raise serializers.ValidationError("Operational event contains unknown fields.")
    if kind not in DeviceOperationalEvent.Kind.values:
        raise serializers.ValidationError("Invalid operational event kind.")
    try:
        event_id = uuid.UUID(str(request.data.get("id")))
    except (TypeError, ValueError) as exc:
        raise serializers.ValidationError(
            "A valid operational event ID is required."
        ) from exc
    recorded_at = parse_required_datetime(
        request.data.get("recorded_at"), "recorded_at"
    )
    details = validate_operational_event_details(kind, request.data)
    existing = DeviceOperationalEvent.objects.filter(pk=event_id).first()
    if existing:
        if existing.device_id != device.id:
            raise exceptions.PermissionDenied("Operational event identifier collision.")
        if operational_event_matches(
            existing,
            kind=kind,
            recorded_at=recorded_at,
            details=details,
        ):
            return Response({"accepted": True, "duplicate": True, "id": str(event_id)})
        raise serializers.ValidationError(
            "Operational event identifier was already used for different evidence."
        )
    try:
        event = DeviceOperationalEvent.objects.create(
            id=event_id,
            device=device,
            kind=kind,
            recorded_at=recorded_at,
            details=details,
        )
    except IntegrityError:
        raced_event = DeviceOperationalEvent.objects.filter(pk=event_id).first()
        if raced_event and raced_event.device_id != device.id:
            raise exceptions.PermissionDenied(
                "Operational event identifier collision."
            ) from None
        if raced_event and operational_event_matches(
            raced_event,
            kind=kind,
            recorded_at=recorded_at,
            details=details,
        ):
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


def parse_required_uuid(value, field):
    if not isinstance(value, str):
        raise serializers.ValidationError({field: "Use a UUID string."})
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise serializers.ValidationError({field: "Use a valid UUID string."}) from exc


def required_value(payload, field, location=""):
    error_field = f"{location}.{field}" if location else field
    if field not in payload:
        raise serializers.ValidationError({error_field: "This field is required."})
    return payload[field]


def validate_event(event, playlist_items, index, loop_started_at, loop_ended_at):
    location = f"events[{index}]"
    if not isinstance(event, dict):
        raise serializers.ValidationError({location: "Each event must be an object."})

    event_id = parse_required_uuid(
        required_value(event, "id", location), f"{location}.id"
    )
    item_id = parse_required_uuid(
        required_value(event, "playlist_item_id", location),
        f"{location}.playlist_item_id",
    )
    if item_id not in playlist_items:
        raise serializers.ValidationError(
            {f"{location}.playlist_item_id": "Playback item is not in the playlist."}
        )
    status_value = required_value(event, "status", location)
    if not isinstance(status_value, str):
        raise serializers.ValidationError(
            {f"{location}.status": "Playback status must be a string."}
        )
    if status_value not in PlaybackEvent.Status.values:
        raise serializers.ValidationError(
            {f"{location}.status": "Invalid playback status."}
        )

    item = playlist_items[item_id]
    duration_ms = required_value(event, "duration_ms", location)
    if type(duration_ms) is not int or not (
        0 <= duration_ms <= MAX_PLAYBACK_EVENT_DURATION_MS
    ):
        raise serializers.ValidationError(
            {
                f"{location}.duration_ms": (
                    "Use an integer between 0 and "
                    f"{MAX_PLAYBACK_EVENT_DURATION_MS}."
                )
            }
        )

    started_at = parse_required_datetime(
        required_value(event, "started_at", location), f"{location}.started_at"
    )
    ended_value = event.get("ended_at")
    ended_at = (
        parse_required_datetime(ended_value, f"{location}.ended_at")
        if ended_value is not None
        else None
    )
    if ended_at is not None and ended_at < started_at:
        raise serializers.ValidationError(
            {f"{location}.ended_at": "Event end cannot precede event start."}
        )
    if (
        started_at < loop_started_at
        and loop_started_at - started_at > PLAYBACK_TIMESTAMP_TOLERANCE
    ):
        raise serializers.ValidationError(
            {
                f"{location}.started_at": (
                    "Event start falls outside the batch loop window."
                )
            }
        )
    if (
        started_at > loop_ended_at
        and started_at - loop_ended_at > PLAYBACK_TIMESTAMP_TOLERANCE
    ):
        raise serializers.ValidationError(
            {
                f"{location}.started_at": (
                    "Event start falls outside the batch loop window."
                )
            }
        )
    if ended_at is not None and (
        (
            ended_at < loop_started_at
            and loop_started_at - ended_at > PLAYBACK_TIMESTAMP_TOLERANCE
        )
        or (
            ended_at > loop_ended_at
            and ended_at - loop_ended_at > PLAYBACK_TIMESTAMP_TOLERANCE
        )
    ):
        raise serializers.ValidationError(
            {
                f"{location}.ended_at": (
                    "Event end falls outside the batch loop window."
                )
            }
        )

    failure_reason = event.get("failure_reason", "")
    if not isinstance(failure_reason, str) or len(failure_reason) > 64:
        raise serializers.ValidationError(
            {
                f"{location}.failure_reason": (
                    "Failure reason must be a string of at most 64 characters."
                )
            }
        )
    if status_value == PlaybackEvent.Status.COMPLETED:
        tolerance_ms = 500 if item.media.kind == item.media.Kind.VIDEO else 0
        minimum_duration_ms = max(0, item.media.duration_ms - tolerance_ms)
        maximum_duration_ms = item.media.duration_ms + tolerance_ms
        if not minimum_duration_ms <= duration_ms <= maximum_duration_ms:
            raise serializers.ValidationError(
                {
                    f"{location}.duration_ms": (
                        "Completed playback duration does not match the media duration."
                    )
                }
            )
        if ended_at is None:
            raise serializers.ValidationError(
                {
                    f"{location}.ended_at": (
                        "Completed playback requires an end timestamp."
                    )
                }
            )
        if failure_reason:
            raise serializers.ValidationError(
                {
                    f"{location}.failure_reason": (
                        "Completed playback cannot include a failure reason."
                    )
                }
            )
    elif failure_reason not in PLAYBACK_FAILURE_REASONS_BY_STATUS[status_value]:
        raise serializers.ValidationError(
            {
                f"{location}.failure_reason": (
                    "Interrupted and failed playback require a recognized failure "
                    "category."
                )
            }
        )

    if ended_at is not None:
        timestamp_duration_ms = int((ended_at - started_at).total_seconds() * 1000)
        timestamp_tolerance_ms = int(
            PLAYBACK_TIMESTAMP_TOLERANCE.total_seconds() * 1000
        )
        if duration_ms > timestamp_duration_ms + timestamp_tolerance_ms:
            raise serializers.ValidationError(
                {
                    f"{location}.duration_ms": (
                        "Playback duration exceeds the event timestamp interval."
                    )
                }
            )
    return {
        "id": event_id,
        "playlist_item_id": item_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": duration_ms,
        "status": status_value,
        "failure_reason": failure_reason,
    }


def validate_playback_batch_payload(payload, playlist, batch_id, playlist_id):
    loop_started_at = parse_required_datetime(
        required_value(payload, "loop_started_at"), "loop_started_at"
    )
    loop_ended_at = parse_required_datetime(
        required_value(payload, "loop_ended_at"), "loop_ended_at"
    )
    if loop_ended_at < loop_started_at:
        raise serializers.ValidationError(
            {"loop_ended_at": "Loop end cannot precede loop start."}
        )
    if (
        loop_started_at < playlist.starts_at
        and playlist.starts_at - loop_started_at > PLAYBACK_TIMESTAMP_TOLERANCE
    ):
        raise serializers.ValidationError(
            {"loop_started_at": "Playback cannot precede the playlist start time."}
        )

    captured_offline = required_value(payload, "captured_offline")
    if type(captured_offline) is not bool:
        raise serializers.ValidationError(
            {"captured_offline": "Use a JSON boolean."}
        )

    raw_events = required_value(payload, "events")
    if not isinstance(raw_events, list):
        raise serializers.ValidationError({"events": "Events must be a JSON array."})
    playlist_items = {
        item.id: item for item in playlist.items.select_related("media").all()
    }
    if not playlist_items:
        raise serializers.ValidationError(
            {"events": "Playback evidence requires a non-empty playlist."}
        )
    if len(raw_events) != len(playlist_items):
        raise serializers.ValidationError(
            {
                "events": (
                    "A batch must contain exactly one result for every playlist entry."
                )
            }
        )
    normalized_events = [
        validate_event(
            event,
            playlist_items,
            index,
            loop_started_at,
            loop_ended_at,
        )
        for index, event in enumerate(raw_events)
    ]
    submitted_items = [event["playlist_item_id"] for event in normalized_events]
    submitted_event_ids = [event["id"] for event in normalized_events]
    if len(submitted_event_ids) != len(set(submitted_event_ids)):
        raise serializers.ValidationError(
            {"events": "A batch cannot contain duplicate event identifiers."}
        )
    if len(submitted_items) != len(set(submitted_items)):
        raise serializers.ValidationError(
            {"events": "A batch cannot contain duplicate playlist entries."}
        )
    if set(submitted_items) != set(playlist_items):
        raise serializers.ValidationError(
            {
                "events": (
                    "A batch must contain exactly one result for every playlist entry."
                )
            }
        )
    return {
        "id": batch_id,
        "playlist_id": playlist_id,
        "loop_started_at": loop_started_at,
        "loop_ended_at": loop_ended_at,
        "captured_offline": captured_offline,
        "events": normalized_events,
    }


def playback_batch_matches(existing, normalized):
    if any(
        (
            existing.playlist_id != normalized["playlist_id"],
            existing.loop_started_at != normalized["loop_started_at"],
            existing.loop_ended_at != normalized["loop_ended_at"],
            existing.captured_offline != normalized["captured_offline"],
        )
    ):
        return False

    submitted_events = {event["id"]: event for event in normalized["events"]}
    stored_events = {event.id: event for event in existing.events.all()}
    if set(submitted_events) != set(stored_events):
        return False
    for event_id, submitted in submitted_events.items():
        stored = stored_events[event_id]
        if any(
            (
                stored.playlist_item_id != submitted["playlist_item_id"],
                stored.started_at != submitted["started_at"],
                stored.ended_at != submitted["ended_at"],
                stored.duration_ms != submitted["duration_ms"],
                stored.status != submitted["status"],
                stored.failure_reason != submitted["failure_reason"],
            )
        ):
            return False
    return True


@api_view(["POST"])
@parser_classes([GzipPlaybackBatchJSONParser])
def playback_batch(request):
    device = device_for(request)
    if device.status == Device.Status.DISABLED:
        raise exceptions.PermissionDenied("Disabled devices cannot submit playback.")
    batch_id = parse_required_uuid(required_value(request.data, "id"), "id")
    playlist_id = parse_required_uuid(
        required_value(request.data, "playlist_id"), "playlist_id"
    )
    existing = PlaybackBatch.objects.filter(pk=batch_id).first()
    if existing and existing.device_id != device.id:
        raise exceptions.PermissionDenied("Playback batch identifier collision.")
    playlist = Playlist.objects.filter(pk=playlist_id).first()
    if not playlist:
        raise serializers.ValidationError("Unknown playlist.")
    if not existing and playlist.status not in {
        Playlist.Status.PUBLISHED,
        Playlist.Status.CANCELLED,
    }:
        raise serializers.ValidationError(
            "Playback evidence is accepted only for a published playlist version."
        )
    normalized = validate_playback_batch_payload(
        request.data, playlist, batch_id, playlist_id
    )
    if existing:
        if playback_batch_matches(existing, normalized):
            return Response({"accepted": True, "duplicate": True})
        raise serializers.ValidationError(
            "Playback batch identifier was already used for different evidence."
        )

    loop_started_at = normalized["loop_started_at"]
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
                loop_ended_at=normalized["loop_ended_at"],
                captured_offline=normalized["captured_offline"],
            )
            PlaybackEvent.objects.bulk_create(
                [
                    PlaybackEvent(batch=batch, **event)
                    for event in normalized["events"]
                ]
            )
            device.last_playback_at = timezone.now()
            device.save(update_fields=["last_playback_at", "updated_at"])
    except IntegrityError:
        raced_batch = PlaybackBatch.objects.filter(pk=batch_id).first()
        if raced_batch:
            if raced_batch.device_id != device.id:
                raise exceptions.PermissionDenied(
                    "Playback batch identifier collision."
                ) from None
            if playback_batch_matches(raced_batch, normalized):
                return Response({"accepted": True, "duplicate": True})
        raise serializers.ValidationError(
            "Playback evidence identifier collision; the batch was not accepted."
        ) from None
    return Response(
        {"accepted": True, "duplicate": False}, status=status.HTTP_201_CREATED
    )
