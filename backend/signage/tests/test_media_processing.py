import hashlib
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile, File
from django.core.files.storage import FileSystemStorage
from PIL import Image

from signage.models import MediaAsset, User
from signage.services import (
    inspect_media,
    run_ffprobe,
    sniff_image_mime,
    sniff_video_mime,
    validate_ready_media_delivery,
)


class NoPathStorage(FileSystemStorage):
    def _full_path(self, name):
        return Path(self.location) / name

    def _open(self, name, mode="rb"):
        return File(self._full_path(name).open(mode))

    def _save(self, name, content):
        destination = self._full_path(name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as output:
            for chunk in content.chunks():
                output.write(chunk)
        return name

    def exists(self, name):
        return self._full_path(name).exists()

    def path(self, name):
        raise NotImplementedError("Remote object storage does not expose paths.")


def test_image_sniffing_rejects_spoofed_extension(tmp_path):
    fake_jpeg = tmp_path / "poster.jpg"
    fake_jpeg.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image")

    with pytest.raises(ValidationError, match="extension does not match"):
        sniff_image_mime(fake_jpeg)


def test_video_sniffing_rejects_non_mp4_content(tmp_path):
    fake_video = tmp_path / "advert.mp4"
    fake_video.write_bytes(b"not an mp4")

    with pytest.raises(ValidationError, match="not an MP4"):
        sniff_video_mime(fake_video)


def test_video_sniffing_accepts_mp4_container_signature(tmp_path):
    video = Path(tmp_path / "advert.mp4")
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00")

    assert sniff_video_mime(video) == "video/mp4"


@pytest.mark.django_db
def test_image_processing_does_not_require_storage_path(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    image = Image.new("RGB", (4, 3), color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    asset = MediaAsset(
        business_name="DUDU",
        title="Storage path test",
        kind=MediaAsset.Kind.IMAGE,
        uploaded_by=user,
    )
    asset.source_file.save("poster.png", ContentFile(buffer.getvalue()), save=False)
    asset.save()
    remote_like_storage = NoPathStorage(location=tmp_path)
    asset.source_file.storage = remote_like_storage
    asset.normalized_file.storage = remote_like_storage

    inspect_media(asset, require_malware_scanner=False)

    assert asset.status == MediaAsset.Status.READY
    assert asset.width == 1920
    assert asset.height == 1080
    assert asset.duration_ms == 10_000
    assert asset.normalized_file.name.startswith("validated/")
    assert len(asset.normalized_file.name) <= MediaAsset._meta.get_field(
        "normalized_file"
    ).max_length
    with Image.open(tmp_path / asset.normalized_file.name) as normalized:
        normalized.load()
        assert normalized.format == "PNG"
        assert normalized.mode == "RGB"
        assert normalized.size == (1920, 1080)
        assert normalized.getpixel((0, 0)) == (0, 0, 0)
        assert normalized.getpixel((960, 540)) == (255, 255, 255)


@pytest.mark.django_db
def test_quarantined_source_is_scanned_before_image_decoder(
    tmp_path,
    settings,
    monkeypatch,
):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(
        "scanner-order@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    image = Image.new("RGB", (4, 3), color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    asset = MediaAsset.objects.create(
        business_name="DUDU",
        title="Scan ordering",
        kind=MediaAsset.Kind.IMAGE,
        source_file=ContentFile(buffer.getvalue(), name="poster.png"),
        uploaded_by=user,
    )
    order = []
    real_sniff = sniff_image_mime

    def fake_run(command, **kwargs):
        order.append("scan")
        assert command[0] == "/usr/bin/clamscan"
        assert kwargs["timeout"] == settings.MEDIA_CLAMAV_TIMEOUT_SECONDS
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    def ordered_sniff(path):
        order.append("decode")
        return real_sniff(path)

    monkeypatch.setattr(
        "signage.services.shutil.which", lambda name: "/usr/bin/clamscan"
    )
    monkeypatch.setattr("signage.services.subprocess.run", fake_run)
    monkeypatch.setattr("signage.services.sniff_image_mime", ordered_sniff)

    inspect_media(asset)

    assert asset.status == MediaAsset.Status.READY
    assert order[:2] == ["scan", "decode"]


@pytest.mark.django_db
def test_malware_scan_timeout_rejects_upload(tmp_path, settings, monkeypatch):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(
        "scanner-timeout@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    image = Image.new("RGB", (4, 3), color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    asset = MediaAsset.objects.create(
        business_name="DUDU",
        title="Scan timeout",
        kind=MediaAsset.Kind.IMAGE,
        source_file=ContentFile(buffer.getvalue(), name="poster.png"),
        uploaded_by=user,
    )
    monkeypatch.setattr(
        "signage.services.shutil.which", lambda name: "/usr/bin/clamscan"
    )
    monkeypatch.setattr(
        "signage.services.subprocess.run",
        lambda command, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(command, kwargs["timeout"])
        ),
    )

    inspect_media(asset)

    assert asset.status == MediaAsset.Status.REJECTED
    assert asset.rejection_reason == "['Malware scan timed out.']"


@pytest.mark.django_db
def test_image_pixel_limit_rejects_compressed_high_resolution_source(
    tmp_path,
    settings,
):
    settings.MEDIA_ROOT = tmp_path
    settings.MEDIA_MAX_IMAGE_PIXELS = 100
    user = User.objects.create_user(
        "pixel-limit@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    image = Image.new("RGB", (20, 20), color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    assert len(buffer.getvalue()) < 1024
    asset = MediaAsset.objects.create(
        business_name="DUDU",
        title="Compressed high resolution",
        kind=MediaAsset.Kind.IMAGE,
        source_file=ContentFile(buffer.getvalue(), name="poster.png"),
        uploaded_by=user,
    )

    inspect_media(asset, require_malware_scanner=False)

    assert asset.status == MediaAsset.Status.REJECTED
    assert "safe limit" in asset.rejection_reason
    assert not asset.normalized_file


@pytest.mark.django_db
def test_jpeg_is_fully_decoded_and_reencoded_as_bounded_jpeg(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(
        "jpeg-normalization@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    image = Image.new("RGB", (3, 4), color="red")
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    source_bytes = buffer.getvalue()
    asset = MediaAsset.objects.create(
        business_name="DUDU",
        title="JPEG normalization",
        kind=MediaAsset.Kind.IMAGE,
        source_file=ContentFile(source_bytes, name="poster.jpg"),
        uploaded_by=user,
    )

    inspect_media(asset, require_malware_scanner=False)

    assert asset.status == MediaAsset.Status.READY
    assert asset.mime_type == "image/jpeg"
    assert asset.normalized_file.name.endswith("media.jpg")
    normalized_path = tmp_path / asset.normalized_file.name
    assert normalized_path.read_bytes() != source_bytes
    with Image.open(normalized_path) as normalized:
        normalized.load()
        assert normalized.format == "JPEG"
        assert normalized.mode == "RGB"
        assert normalized.size == (1920, 1080)


@pytest.mark.django_db
def test_ready_image_delivery_rejects_wave_9_oversized_dimensions(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(
        "delivered-image@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    image = Image.new("RGB", (2842, 1396), color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    payload = buffer.getvalue()
    asset = MediaAsset.objects.create(
        business_name="DUDU",
        title="Oversized delivery",
        kind=MediaAsset.Kind.IMAGE,
        status=MediaAsset.Status.READY,
        source_file=ContentFile(payload, name="source.png"),
        normalized_file=ContentFile(payload, name="delivered.png"),
        sha256=hashlib.sha256(payload).hexdigest(),
        file_size=len(payload),
        mime_type="image/png",
        width=1920,
        height=1080,
        uploaded_by=user,
    )

    with pytest.raises(ValidationError, match="dimensions are invalid"):
        validate_ready_media_delivery(asset)


def test_ffprobe_has_a_bounded_timeout(tmp_path, settings, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"format": {"duration": "1"}, "streams": []}',
            stderr="",
        )

    monkeypatch.setattr("signage.services.subprocess.run", fake_run)

    run_ffprobe(tmp_path / "video.mp4")

    assert calls[0]["timeout"] == settings.MEDIA_FFPROBE_TIMEOUT_SECONDS
