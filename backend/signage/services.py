import hashlib
import json
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import transaction
from django.utils import timezone

from .models import (
    Alert,
    AuditEvent,
    Device,
    MediaAsset,
    PlatformSettings,
    Playlist,
    token_hash,
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


def open_alert(device, code, severity, message):
    return Alert.objects.get_or_create(
        device=device,
        code=code,
        acknowledged_at__isnull=True,
        defaults={"severity": severity, "message": message},
    )


@transaction.atomic
def issue_kiosk_pin(device, actor):
    raw_pin = f"{secrets.randbelow(1_000_000):06d}"
    locked = Device.objects.select_for_update().get(pk=device.pk)
    locked.kiosk_pin_hash = token_hash(raw_pin)
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
    if media_has_current_or_future_references(locked):
        raise ValidationError(
            "Media is referenced by a draft, current, or future playlist."
        )
    for field_name in ("source_file", "normalized_file"):
        file_field = getattr(locked, field_name)
        if file_field:
            file_field.delete(save=False)
            setattr(locked, field_name, "")
    locked.status = MediaAsset.Status.ARCHIVED
    locked.archived_at = timezone.now()
    locked.save(
        update_fields=[
            "source_file",
            "normalized_file",
            "status",
            "archived_at",
            "updated_at",
        ]
    )
    audit(actor, "media.delete_binary", locked)
    return locked


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

    with Image.open(path) as image:
        if image.format not in {"JPEG", "PNG"}:
            raise ValidationError("Image decoder did not confirm JPEG or PNG content.")
        image.verify()
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
    )
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


def normalized_media_name(asset, source_path):
    if asset.kind == MediaAsset.Kind.VIDEO:
        return f"{asset.id}-normalized.mp4"
    return f"{asset.id}{source_path.suffix.lower()}"


def inspect_media(asset, require_malware_scanner=True):
    asset.status = MediaAsset.Status.PROCESSING
    asset.save(update_fields=["status", "updated_at"])
    try:
        with tempfile.TemporaryDirectory() as temporary:
            source = copy_source_to_temporary_file(asset, temporary)
            if asset.kind == MediaAsset.Kind.IMAGE:
                detected = sniff_image_mime(source)
                if source.stat().st_size > settings.MEDIA_MAX_IMAGE_BYTES:
                    raise ValidationError("Image exceeds the 10 MB limit.")
                from PIL import Image

                with Image.open(source) as image:
                    asset.width, asset.height = image.size
                output = source
                asset.duration_ms = 10_000
            else:
                detected = sniff_video_mime(source)
                if source.stat().st_size > settings.MEDIA_MAX_VIDEO_BYTES:
                    raise ValidationError("Video exceeds the 50 MB limit.")
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
                )
                validate_normalized_video(output)

            scanner = shutil.which("clamscan")
            if require_malware_scanner and not scanner:
                raise ValidationError("Malware scanner is unavailable.")
            if scanner:
                subprocess.run(
                    [scanner, "--no-summary", str(source)],
                    check=True,
                    capture_output=True,
                )
            digest = hashlib.sha256()
            with output.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if asset.normalized_file:
                asset.normalized_file.delete(save=False)
            with output.open("rb") as handle:
                asset.normalized_file.save(
                    normalized_media_name(asset, source), File(handle), save=False
                )
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
    finally:
        asset.save()
    return asset


@transaction.atomic
def publish_playlist(playlist, actor, urgent=False):
    locked = Playlist.objects.select_for_update().get(pk=playlist.pk)
    if locked.status != Playlist.Status.DRAFT:
        raise ValidationError("Only a draft playlist can be published.")
    locked.full_clean()
    items = list(locked.items.select_related("media"))
    limits = PlatformSettings.load()
    if not items:
        raise ValidationError("A playlist cannot be empty.")
    if len(items) > limits.playlist_max_entries:
        raise ValidationError("Playlist exceeds the configured entry limit.")
    if any(item.media.status != MediaAsset.Status.READY for item in items):
        raise ValidationError("Every media item must be validated before publishing.")
    duration = sum(item.media.duration_ms for item in items) / 1000
    if duration > limits.playlist_max_duration_seconds:
        raise ValidationError("Playlist exceeds the configured duration limit.")
    locked.status = Playlist.Status.PUBLISHED
    locked.published_at = timezone.now()
    locked.is_urgent = urgent
    locked.save(update_fields=["status", "published_at", "is_urgent", "updated_at"])
    audit(actor, "playlist.publish", locked, {"urgent": urgent})
    return locked


@transaction.atomic
def disable_device(device, actor):
    locked = Device.objects.select_for_update().get(pk=device.pk)
    locked.status = Device.Status.DISABLED
    locked.disabled_at = timezone.now()
    locked.save(update_fields=["status", "disabled_at", "updated_at"])
    audit(actor, "device.disable", locked)
    return locked


@transaction.atomic
def reactivate_device(device, actor):
    locked = Device.objects.select_for_update().get(pk=device.pk)
    locked.status = Device.Status.ACTIVE
    locked.disabled_at = None
    locked.save(update_fields=["status", "disabled_at", "updated_at"])
    audit(actor, "device.reactivate", locked)
    return locked
