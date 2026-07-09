from io import BytesIO
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile, File
from django.core.files.storage import FileSystemStorage
from PIL import Image

from signage.models import MediaAsset, User
from signage.services import inspect_media, sniff_image_mime, sniff_video_mime


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
    assert asset.width == 4
    assert asset.height == 3
    assert asset.duration_ms == 10_000
    assert asset.normalized_file.name.startswith("validated/")
