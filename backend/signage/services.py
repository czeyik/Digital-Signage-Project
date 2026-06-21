import hashlib
import json
import mimetypes
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import transaction
from django.utils import timezone

from .models import AuditEvent, Device, MediaAsset, PlatformSettings, Playlist

ALLOWED_IMAGE_MIME = {"image/jpeg", "image/png"}
ALLOWED_VIDEO_MIME = {"video/mp4"}


def audit(actor, action, target, metadata=None):
    return AuditEvent.objects.create(
        actor=actor,
        action=action,
        target_type=target._meta.label_lower,
        target_id=str(target.pk),
        metadata=metadata or {},
    )


def inspect_media(asset, require_malware_scanner=True):
    source = Path(asset.source_file.path)
    asset.status = MediaAsset.Status.PROCESSING
    asset.save(update_fields=["status", "updated_at"])
    try:
        detected = mimetypes.guess_type(source.name)[0]
        if asset.kind == MediaAsset.Kind.IMAGE:
            if detected not in ALLOWED_IMAGE_MIME:
                raise ValidationError("Only JPEG and PNG images are accepted.")
            if source.stat().st_size > settings.MEDIA_MAX_IMAGE_BYTES:
                raise ValidationError("Image exceeds the 10 MB limit.")
            from PIL import Image

            with Image.open(source) as image:
                image.verify()
            with Image.open(source) as image:
                asset.width, asset.height = image.size
            output = source
            asset.duration_ms = 10_000
        else:
            if detected not in ALLOWED_VIDEO_MIME:
                raise ValidationError("Only MP4 video is accepted.")
            if source.stat().st_size > settings.MEDIA_MAX_VIDEO_BYTES:
                raise ValidationError("Video exceeds the 50 MB limit.")
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration:stream=width,height",
                    "-of",
                    "json",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            details = json.loads(probe.stdout)
            duration_ms = round(float(details["format"]["duration"]) * 1000)
            if duration_ms > 15_000:
                raise ValidationError("Video exceeds the 15-second limit.")
            video_stream = next(
                stream
                for stream in details.get("streams", [])
                if "width" in stream and "height" in stream
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
        with output.open("rb") as handle:
            asset.normalized_file.save(output.name, File(handle), save=False)
        asset.sha256 = digest.hexdigest()
        asset.file_size = output.stat().st_size
        asset.mime_type = (
            "video/mp4" if asset.kind == MediaAsset.Kind.VIDEO else detected
        )
        asset.status = MediaAsset.Status.READY
        asset.rejection_reason = ""
    except (
        ValidationError,
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
