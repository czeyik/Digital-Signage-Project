from pathlib import Path

import pytest
from django.core.exceptions import ValidationError

from signage.services import sniff_image_mime, sniff_video_mime


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
