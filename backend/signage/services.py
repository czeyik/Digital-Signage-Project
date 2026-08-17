import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import uuid
import warnings
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    Alert,
    ApiThrottle,
    AuditEvent,
    Device,
    DeviceAccessToken,
    DeviceCredential,
    EnrollmentCode,
    MediaAsset,
    MediaDeletion,
    PlatformSettings,
    Playlist,
)

ALLOWED_IMAGE_MIME = {"image/jpeg", "image/png"}
ALLOWED_VIDEO_MIME = {"video/mp4"}
IMAGE_EXTENSION_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
VIDEO_EXTENSION_MIME = {".mp4": "video/mp4"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"


def audit(actor, action, target, metadata=None):
    return AuditEvent.objects.create(
        actor=actor,
        action=action,
        target_type=target._meta.label_lower,
        target_id=str(target.pk),
        metadata=metadata or {},
    )


def client_ip(request):
    """Return the trusted proxy-set address, not a spoofable left-most XFF value."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if getattr(settings, "SECURE_PROXY_SSL_HEADER", None) and forwarded:
        return forwarded.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR", "")


@transaction.atomic
def throttle_wait(request, action, limit=10, window_seconds=900):
    identity = f"{action}|{client_ip(request)}|{settings.SECRET_KEY}"
    key = hashlib.sha256(identity.encode()).hexdigest()
    now = timezone.now()
    throttle, _ = ApiThrottle.objects.select_for_update().get_or_create(key_hash=key)
    if throttle.is_blocked:
        return max(1, int((throttle.blocked_until - now).total_seconds()))
    if throttle.window_started_at <= now - timedelta(seconds=window_seconds):
        throttle.attempts = 0
        throttle.window_started_at = now
        throttle.blocked_until = None
    throttle.attempts += 1
    if throttle.attempts > limit:
        throttle.blocked_until = now + timedelta(seconds=window_seconds)
    throttle.save()
    if throttle.blocked_until:
        return window_seconds
    return 0


def enforce_api_throttle(request, action, limit=10, window_seconds=900):
    from rest_framework import exceptions

    wait = throttle_wait(request, action, limit, window_seconds)
    if wait:
        raise exceptions.Throttled(wait=wait)


def open_alert(device, code, severity, message):
    return open_or_escalate_alert(device, code, severity, message)


@transaction.atomic
def open_or_escalate_alert(device, code, severity, message):
    """Open one device alert or raise its severity without auto-resolving it.

    The device-row lock serializes concurrent heartbeats and fleet-health runs
    for the same device, including the case where no unresolved alert exists
    yet.  A later warning must never lower an already critical alert.
    """

    if device is not None:
        Device.objects.select_for_update().get(pk=device.pk)
    alert = (
        Alert.objects.select_for_update()
        .filter(device=device, code=code, acknowledged_at__isnull=True)
        .order_by("created_at")
        .first()
    )
    if alert is None:
        try:
            # Keep the insert in a savepoint: the partial unique constraints
            # also serialize global (device=NULL) alerts, where no device row
            # exists to lock.
            with transaction.atomic():
                return Alert.objects.create(
                    device=device,
                    code=code,
                    severity=severity,
                    message=message,
                ), True
        except IntegrityError:
            pass
        alert = (
            Alert.objects.select_for_update()
            .filter(device=device, code=code, acknowledged_at__isnull=True)
            .order_by("created_at")
            .first()
        )
        if alert is None:
            raise IntegrityError("Could not create or load the unresolved alert.")
    if (
        severity == Alert.Severity.CRITICAL
        and alert.severity != Alert.Severity.CRITICAL
    ):
        alert.severity = severity
        alert.message = message
        alert.save(update_fields=["severity", "message", "updated_at"])
    return alert, False


@transaction.atomic
def issue_kiosk_pin(device, actor):
    raw_pin = f"{secrets.randbelow(1_000_000):06d}"
    locked = Device.objects.select_for_update().get(pk=device.pk)
    salt = secrets.token_bytes(16)
    iterations = 210_000
    derived = hashlib.pbkdf2_hmac("sha256", raw_pin.encode("utf-8"), salt, iterations)
    locked.kiosk_pin_hash = f"pbkdf2_sha256${iterations}${salt.hex()}${derived.hex()}"
    locked.kiosk_pin_reset_at = timezone.now()
    locked.save(update_fields=["kiosk_pin_hash", "kiosk_pin_reset_at", "updated_at"])
    audit(actor, "device.kiosk_pin.reset", locked)
    return raw_pin


def media_has_current_or_future_references(asset):
    return asset.playlist_items.filter(
        playlist__status__in=[Playlist.Status.DRAFT, Playlist.Status.PUBLISHED],
        playlist__ends_at__gte=timezone.now(),
    ).exists()


@transaction.atomic
def delete_media_binary(asset, actor):
    locked = MediaAsset.objects.select_for_update().get(pk=asset.pk)
    was_processing = locked.status == MediaAsset.Status.PROCESSING
    if media_has_current_or_future_references(locked):
        raise ValidationError(
            "Media is referenced by a draft, current, or future playlist."
        )
    MediaDeletion.objects.get_or_create(
        asset=locked,
        defaults={
            "source_name": locked.source_file.name,
            "normalized_name": locked.normalized_file.name,
        },
    )
    locked.status = MediaAsset.Status.ARCHIVED
    locked.archived_at = timezone.now()
    locked.processing_token = None
    locked.processing_lease_expires_at = None
    if was_processing:
        locked.processing_finished_at = timezone.now()
    locked.save(
        update_fields=[
            "status",
            "archived_at",
            "processing_token",
            "processing_lease_expires_at",
            "processing_finished_at",
            "updated_at",
        ]
    )
    audit(actor, "media.delete_binary", locked)
    return locked


def process_media_deletion(deletion_id):
    """Delete a queued binary after its archival transaction has committed."""

    with transaction.atomic():
        deletion = (
            MediaDeletion.objects.select_for_update()
            .select_related("asset")
            .filter(pk=deletion_id, completed_at__isnull=True)
            .first()
        )
        if not deletion:
            return False
        asset = deletion.asset
        source_name = deletion.source_name
        normalized_name = deletion.normalized_name
        source_storage = asset.source_file.storage
        normalized_storage = asset.normalized_file.storage
        deletion.attempts += 1
        deletion.last_attempt_at = timezone.now()
        deletion.save(update_fields=["attempts", "last_attempt_at", "updated_at"])

    try:
        if source_name:
            source_storage.delete(source_name)
        if normalized_name:
            normalized_storage.delete(normalized_name)
    except Exception as exc:  # Storage backends surface provider-specific errors.
        with transaction.atomic():
            MediaDeletion.objects.select_for_update().filter(
                pk=deletion_id,
                completed_at__isnull=True,
            ).update(last_error=str(exc)[:255])
        return False

    with transaction.atomic():
        deletion = (
            MediaDeletion.objects.select_for_update()
            .select_related("asset")
            .filter(pk=deletion_id, completed_at__isnull=True)
            .first()
        )
        if not deletion:
            return False
        asset = deletion.asset
        # Do not clear a field if a later operation replaced its object name.
        update_fields = []
        if asset.source_file.name == deletion.source_name:
            asset.source_file = ""
            update_fields.append("source_file")
        if asset.normalized_file.name == deletion.normalized_name:
            asset.normalized_file = ""
            update_fields.append("normalized_file")
        if update_fields:
            asset.save(update_fields=[*update_fields, "updated_at"])
        deletion.completed_at = timezone.now()
        deletion.last_error = ""
        deletion.save(update_fields=["completed_at", "last_error", "updated_at"])
    return True


def extension_mime(path):
    return {**IMAGE_EXTENSION_MIME, **VIDEO_EXTENSION_MIME}.get(path.suffix.lower())


def sniff_image_mime(path):
    expected = extension_mime(path)
    if expected not in ALLOWED_IMAGE_MIME:
        raise ValidationError("Only JPEG and PNG image filenames are accepted.")
    with path.open("rb") as handle:
        header = handle.read(16)
    if header.startswith(JPEG_SIGNATURE):
        detected = "image/jpeg"
    elif header.startswith(PNG_SIGNATURE):
        detected = "image/png"
    else:
        raise ValidationError("Image content is not a valid JPEG or PNG file.")
    if detected != expected:
        raise ValidationError("Image filename extension does not match its content.")
    from PIL import Image

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format not in {"JPEG", "PNG"}:
                    raise ValidationError(
                        "Image decoder did not confirm JPEG or PNG content."
                    )
                width, height = image.size
                if (
                    width < 1
                    or height < 1
                    or width > settings.MEDIA_MAX_IMAGE_DIMENSION
                    or height > settings.MEDIA_MAX_IMAGE_DIMENSION
                    or width * height > settings.MEDIA_MAX_IMAGE_PIXELS
                ):
                    raise ValidationError("Image dimensions exceed the safe limit.")
                image.verify()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError("Image dimensions exceed the safe limit.") from exc
    return detected


def sniff_video_mime(path):
    expected = extension_mime(path)
    if expected not in ALLOWED_VIDEO_MIME:
        raise ValidationError("Only MP4 video filenames are accepted.")
    with path.open("rb") as handle:
        header = handle.read(12)
    if len(header) < 12 or header[4:8] != b"ftyp":
        raise ValidationError("Video content is not an MP4 container.")
    return "video/mp4"


def run_ffprobe(path):
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration,format_name:stream=codec_type,codec_name,width,height",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.MEDIA_FFPROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValidationError("Video inspection timed out.") from exc
    return json.loads(probe.stdout)


def validate_normalized_video(path):
    details = run_ffprobe(path)
    streams = details.get("streams", [])
    video_streams = [
        stream for stream in streams if stream.get("codec_type") == "video"
    ]
    audio_streams = [
        stream for stream in streams if stream.get("codec_type") == "audio"
    ]
    if len(video_streams) != 1:
        raise ValidationError(
            "Normalized output must contain exactly one video stream."
        )
    if audio_streams:
        raise ValidationError("Normalized output must not contain audio.")
    video = video_streams[0]
    if video.get("codec_name") != "h264":
        raise ValidationError("Normalized output must use H.264 video.")
    if int(video.get("width", 0)) > 1920 or int(video.get("height", 0)) > 1080:
        raise ValidationError("Normalized output exceeds 1920x1080.")
    duration_ms = round(float(details["format"]["duration"]) * 1000)
    if duration_ms > 15_000:
        raise ValidationError("Normalized output exceeds the 15-second limit.")
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-f",
                "null",
                "-",
            ],
            check=True,
            capture_output=True,
            timeout=settings.MEDIA_FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValidationError("Normalized video validation timed out.") from exc
    return duration_ms


def scan_quarantined_source(path, require_malware_scanner=True):
    """Scan the source before any image or video decoder sees untrusted bytes."""
    scanner = shutil.which("clamscan")
    if require_malware_scanner and not scanner:
        raise ValidationError("Malware scanner is unavailable.")
    if not scanner:
        return
    scan_command = [scanner, "--no-summary"]
    database = os.getenv("CLAMAV_DATABASE_DIR", "")
    if database:
        scan_command.extend(["--database", database])
    try:
        result = subprocess.run(
            [*scan_command, str(path)],
            check=False,
            capture_output=True,
            timeout=settings.MEDIA_CLAMAV_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValidationError("Malware scan timed out.") from exc
    if result.returncode == 1:
        raise ValidationError("Malware was detected in the upload.")
    if result.returncode != 0:
        raise ValidationError("Malware scan failed.")


def normalize_image(source, detected_mime):
    """Fully decode and re-encode a bounded 1080p image on a black canvas."""
    from PIL import Image

    output = source.with_name(f"{source.stem}-normalized{source.suffix.lower()}")
    decoded = None
    canvas = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as image:
                width, height = image.size
                if (
                    width < 1
                    or height < 1
                    or width > settings.MEDIA_MAX_IMAGE_DIMENSION
                    or height > settings.MEDIA_MAX_IMAGE_DIMENSION
                    or width * height > settings.MEDIA_MAX_IMAGE_PIXELS
                ):
                    raise ValidationError("Image dimensions exceed the safe limit.")
                image.load()
                decoded = image.convert("RGBA")

        normalized_size = (
            settings.MEDIA_NORMALIZED_IMAGE_WIDTH,
            settings.MEDIA_NORMALIZED_IMAGE_HEIGHT,
        )
        decoded.thumbnail(normalized_size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", normalized_size, "black")
        position = (
            (normalized_size[0] - decoded.width) // 2,
            (normalized_size[1] - decoded.height) // 2,
        )
        canvas.paste(decoded, position, decoded)
        if detected_mime == "image/jpeg":
            canvas.save(output, format="JPEG", quality=90, optimize=True)
        else:
            canvas.save(output, format="PNG", optimize=True)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError("Image dimensions exceed the safe limit.") from exc
    finally:
        if decoded is not None:
            decoded.close()
        if canvas is not None:
            canvas.close()

    if output.stat().st_size > settings.MEDIA_MAX_IMAGE_BYTES:
        raise ValidationError("Normalized image exceeds the 10 MB limit.")
    validate_normalized_image(output, detected_mime)
    return output


def validate_normalized_image(path, expected_mime):
    """Test a normalized image with a second complete decode before publication."""
    from PIL import Image

    detected_mime = sniff_image_mime(path)
    if detected_mime != expected_mime:
        raise ValidationError("Normalized image type changed unexpectedly.")
    with Image.open(path) as image:
        image.load()
        if image.size != (
            settings.MEDIA_NORMALIZED_IMAGE_WIDTH,
            settings.MEDIA_NORMALIZED_IMAGE_HEIGHT,
        ):
            raise ValidationError("Normalized image dimensions are invalid.")
        if image.mode != "RGB":
            raise ValidationError("Normalized image must use RGB pixels.")


def copy_source_to_temporary_file(asset, directory):
    source_name = Path(asset.source_file.name).name
    suffix = Path(source_name).suffix.lower()
    source_path = Path(directory) / f"source{suffix}"
    asset.source_file.open("rb")
    try:
        with source_path.open("wb") as output:
            for chunk in asset.source_file.chunks():
                output.write(chunk)
    finally:
        asset.source_file.close()
    return source_path


def normalized_media_name(asset, source_path, processing_token):
    suffix = (
        ".mp4"
        if asset.kind == MediaAsset.Kind.VIDEO
        else source_path.suffix.lower()
    )
    # FileField defaults to max_length=100. UUID hex still preserves the complete
    # asset and processing-attempt identities while keeping the S3 key below it.
    return f"{asset.id.hex}/{processing_token.hex}/media{suffix}"


@transaction.atomic
def _start_media_processing_attempt(asset_id):
    asset = MediaAsset.objects.select_for_update().get(pk=asset_id)
    if asset.status != MediaAsset.Status.QUARANTINED:
        return None
    now = timezone.now()
    asset.status = MediaAsset.Status.PROCESSING
    asset.processing_attempts += 1
    asset.processing_token = uuid.uuid4()
    asset.processing_started_at = now
    asset.processing_lease_expires_at = now + timedelta(
        seconds=settings.MEDIA_PROCESSING_LEASE_SECONDS
    )
    asset.processing_finished_at = None
    asset.save(
        update_fields=[
            "status",
            "processing_attempts",
            "processing_token",
            "processing_started_at",
            "processing_lease_expires_at",
            "processing_finished_at",
            "updated_at",
        ]
    )
    return asset.processing_token


@transaction.atomic
def _finalize_media_processing(asset, processing_token, staged_name):
    locked = MediaAsset.objects.select_for_update().get(pk=asset.pk)
    if (
        locked.status != MediaAsset.Status.PROCESSING
        or locked.processing_token != processing_token
    ):
        return False

    old_normalized_name = (
        locked.normalized_file.name if locked.normalized_file else ""
    )
    for field_name in (
        "sha256",
        "file_size",
        "mime_type",
        "duration_ms",
        "width",
        "height",
        "rejection_reason",
    ):
        setattr(locked, field_name, getattr(asset, field_name))
    locked.status = asset.status
    if asset.status == MediaAsset.Status.READY:
        locked.normalized_file = staged_name
    locked.processing_token = None
    locked.processing_lease_expires_at = None
    locked.processing_finished_at = timezone.now()
    locked.save(
        update_fields=[
            "normalized_file",
            "sha256",
            "file_size",
            "mime_type",
            "duration_ms",
            "width",
            "height",
            "rejection_reason",
            "status",
            "processing_token",
            "processing_lease_expires_at",
            "processing_finished_at",
            "updated_at",
        ]
    )
    if (
        old_normalized_name
        and staged_name
        and old_normalized_name != staged_name
    ):
        storage = asset.normalized_file.storage
        transaction.on_commit(
            lambda name=old_normalized_name: storage.delete(name)
        )
    return True


def inspect_media(asset, require_malware_scanner=True):
    processing_token = asset.processing_token
    if asset.status != MediaAsset.Status.PROCESSING or not processing_token:
        processing_token = _start_media_processing_attempt(asset.pk)
        if not processing_token:
            asset.refresh_from_db()
            return asset
        asset.refresh_from_db()
    staged_name = ""
    storage = asset.normalized_file.storage
    try:
        with tempfile.TemporaryDirectory() as temporary:
            source = copy_source_to_temporary_file(asset, temporary)
            source_size = source.stat().st_size
            if asset.kind == MediaAsset.Kind.IMAGE:
                if source_size > settings.MEDIA_MAX_IMAGE_BYTES:
                    raise ValidationError("Image exceeds the 10 MB limit.")
            elif source_size > settings.MEDIA_MAX_VIDEO_BYTES:
                raise ValidationError("Video exceeds the 50 MB limit.")

            scan_quarantined_source(source, require_malware_scanner)

            if asset.kind == MediaAsset.Kind.IMAGE:
                detected = sniff_image_mime(source)
                output = normalize_image(source, detected)
                asset.width = settings.MEDIA_NORMALIZED_IMAGE_WIDTH
                asset.height = settings.MEDIA_NORMALIZED_IMAGE_HEIGHT
                asset.duration_ms = 10_000
            else:
                detected = sniff_video_mime(source)
                details = run_ffprobe(source)
                duration_ms = round(float(details["format"]["duration"]) * 1000)
                if duration_ms > 15_000:
                    raise ValidationError("Video exceeds the 15-second limit.")
                video_stream = next(
                    stream
                    for stream in details.get("streams", [])
                    if stream.get("codec_type") == "video"
                    and "width" in stream
                    and "height" in stream
                )
                asset.width = video_stream["width"]
                asset.height = video_stream["height"]
                asset.duration_ms = duration_ms
                output = source.with_name(f"{source.stem}-normalized.mp4")
                try:
                    subprocess.run(
                        [
                            "ffmpeg",
                            "-y",
                            "-i",
                            str(source),
                            "-an",
                            "-vf",
                            (
                                "scale=1920:1080:force_original_aspect_ratio=decrease,"
                                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black"
                            ),
                            "-c:v",
                            "libx264",
                            "-pix_fmt",
                            "yuv420p",
                            "-movflags",
                            "+faststart",
                            str(output),
                        ],
                        check=True,
                        capture_output=True,
                        timeout=settings.MEDIA_FFMPEG_TIMEOUT_SECONDS,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise ValidationError("Video normalization timed out.") from exc
                asset.duration_ms = validate_normalized_video(output)
            digest = hashlib.sha256()
            with output.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            with output.open("rb") as handle:
                generated_name = asset.normalized_file.field.generate_filename(
                    asset,
                    normalized_media_name(asset, source, processing_token),
                )
                staged_name = storage.save(generated_name, File(handle))
            asset.sha256 = digest.hexdigest()
            asset.file_size = output.stat().st_size
            asset.mime_type = (
                "video/mp4" if asset.kind == MediaAsset.Kind.VIDEO else detected
            )
            asset.status = MediaAsset.Status.READY
            asset.rejection_reason = ""
    except (
        ValidationError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
        StopIteration,
    ) as exc:
        asset.status = MediaAsset.Status.REJECTED
        asset.rejection_reason = str(exc)[:255]
    finalized = _finalize_media_processing(asset, processing_token, staged_name)
    if not finalized and staged_name:
        storage.delete(staged_name)
    asset.refresh_from_db()
    return asset


@transaction.atomic
def publish_playlist(playlist, actor, urgent=False):
    locked = Playlist.objects.select_for_update().get(pk=playlist.pk)
    if locked.status != Playlist.Status.DRAFT:
        raise ValidationError("Only a draft playlist can be published.")
    locked.full_clean()
    items = list(locked.items.select_related("media"))
    # Serialize every publication through the singleton so two transactions
    # cannot both observe an empty schedule window and publish overlapping rows.
    limits, _ = PlatformSettings.objects.select_for_update().get_or_create(
        singleton_id=1
    )
    if not items:
        raise ValidationError("A playlist cannot be empty.")
    if len(items) > limits.playlist_max_entries:
        raise ValidationError("Playlist exceeds the configured entry limit.")
    if any(item.media.status != MediaAsset.Status.READY for item in items):
        raise ValidationError("Every media item must be validated before publishing.")
    duration = sum(item.media.duration_ms for item in items) / 1000
    if duration > limits.playlist_max_duration_seconds:
        raise ValidationError("Playlist exceeds the configured duration limit.")

    now = timezone.now()
    if urgent and not (locked.starts_at <= now < locked.ends_at):
        raise ValidationError(
            "An urgent replacement must cover the current weekly window."
        )

    overlapping = (
        Playlist.objects.select_for_update()
        .filter(
            status=Playlist.Status.PUBLISHED,
            starts_at__lt=locked.ends_at,
            ends_at__gt=locked.starts_at,
        )
        .exclude(pk=locked.pk)
    )
    superseded = list(
        overlapping.filter(
            name=locked.name,
            version__lt=locked.version,
            starts_at=locked.starts_at,
            ends_at=locked.ends_at,
            is_urgent=urgent,
        )
    )
    conflicts = overlapping.filter(is_urgent=urgent).exclude(
        pk__in=[previous.pk for previous in superseded]
    )
    if conflicts.exists():
        if urgent:
            raise ValidationError(
                "An urgent replacement is already published for this weekly window; "
                "create its next version to correct it."
            )
        raise ValidationError(
            "A published scheduled playlist already overlaps this weekly window; "
            "create its next version to correct it."
        )

    locked.status = Playlist.Status.PUBLISHED
    locked.published_at = now
    locked.is_urgent = urgent
    locked.save(update_fields=["status", "published_at", "is_urgent", "updated_at"])
    for previous in superseded:
        previous.status = Playlist.Status.CANCELLED
        previous.superseded_by = locked
        previous.save(update_fields=["status", "superseded_by", "updated_at"])
        audit(
            actor,
            "playlist.cancelled_by_correction",
            previous,
            {"replacement": str(locked.id)},
        )
    audit(actor, "playlist.publish", locked, {"urgent": urgent})
    return locked


def active_playlist():
    """Select the same effective playlist for the player and dashboard."""

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


def _revoke_active_device_credentials(device):
    now = timezone.now()
    credentials = DeviceCredential.objects.select_for_update().filter(
        device=device,
        revoked_at__isnull=True,
    )
    credential_ids = list(credentials.values_list("pk", flat=True))
    if not credential_ids:
        return 0
    DeviceCredential.objects.filter(pk__in=credential_ids).update(revoked_at=now)
    DeviceAccessToken.objects.filter(credential_id__in=credential_ids).delete()
    return len(credential_ids)


def _expire_unused_enrollment_codes(device):
    now = timezone.now()
    return EnrollmentCode.objects.filter(
        device=device,
        used_at__isnull=True,
        expires_at__gt=now,
    ).update(expires_at=now)


@transaction.atomic
def revoke_device_credentials(device, actor):
    """Invalidate all active refresh and access tokens for a lost device."""

    locked = Device.objects.select_for_update().get(pk=device.pk)
    credential_count = _revoke_active_device_credentials(locked)
    enrollment_count = _expire_unused_enrollment_codes(locked)
    audit(
        actor,
        "device.credentials.revoke",
        locked,
        {
            "credentials_revoked": credential_count,
            "enrollment_codes_expired": enrollment_count,
        },
    )
    return locked, credential_count


@transaction.atomic
def disable_device(device, actor):
    locked = Device.objects.select_for_update().get(pk=device.pk)
    locked.status = Device.Status.DISABLED
    locked.disabled_at = timezone.now()
    locked.save(update_fields=["status", "disabled_at", "updated_at"])
    credential_count = _revoke_active_device_credentials(locked)
    enrollment_count = _expire_unused_enrollment_codes(locked)
    audit(
        actor,
        "device.disable",
        locked,
        {
            "credentials_revoked": credential_count,
            "enrollment_codes_expired": enrollment_count,
        },
    )
    return locked


@transaction.atomic
def reactivate_device(device, actor):
    locked = Device.objects.select_for_update().get(pk=device.pk)
    locked.status = Device.Status.ACTIVE
    locked.disabled_at = None
    locked.save(update_fields=["status", "disabled_at", "updated_at"])
    audit(actor, "device.reactivate", locked)
    return locked
