from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.test import override_settings
from rest_framework import exceptions

from signage.integrity import verify_integrity_token


def integrity_payload(request_hash="expected-hash"):
    return {
        "requestDetails": {
            "requestPackageName": "com.duducar.signage",
            "requestHash": request_hash,
            "timestampMillis": str(
                int(datetime.now(tz=dt_timezone.utc).timestamp() * 1000)
            ),
        },
        "deviceIntegrity": {
            "deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]
        },
    }


@override_settings(
    PLAY_INTEGRITY_PACKAGE_NAME="com.duducar.signage",
    PLAY_INTEGRITY_MAX_TOKEN_AGE_SECONDS=120,
)
def test_integrity_accepts_certified_sideload_without_license_verdict(monkeypatch):
    monkeypatch.setattr(
        "signage.integrity.decode_integrity_token",
        lambda token: integrity_payload(),
    )

    payload = verify_integrity_token("decoded-by-google", "expected-hash")

    assert "appIntegrity" not in payload


@pytest.mark.parametrize("failure", ["wrong_package", "wrong_hash", "expired"])
@override_settings(
    PLAY_INTEGRITY_PACKAGE_NAME="com.duducar.signage",
    PLAY_INTEGRITY_MAX_TOKEN_AGE_SECONDS=120,
)
def test_integrity_rejects_wrong_binding_or_expired_token(monkeypatch, failure):
    payload = integrity_payload()
    if failure == "wrong_package":
        payload["requestDetails"]["requestPackageName"] = "example.attacker"
    elif failure == "wrong_hash":
        payload["requestDetails"]["requestHash"] = "forged"
    else:
        expired = datetime.now(tz=dt_timezone.utc) - timedelta(minutes=5)
        payload["requestDetails"]["timestampMillis"] = str(
            int(expired.timestamp() * 1000)
        )
    monkeypatch.setattr(
        "signage.integrity.decode_integrity_token", lambda token: payload
    )

    with pytest.raises(exceptions.AuthenticationFailed):
        verify_integrity_token("forged", "expected-hash")


@override_settings(
    PLAY_INTEGRITY_PACKAGE_NAME="com.duducar.signage",
    PLAY_INTEGRITY_MAX_TOKEN_AGE_SECONDS=120,
)
def test_integrity_rejects_missing_device_verdict(monkeypatch):
    payload = integrity_payload()
    payload["deviceIntegrity"]["deviceRecognitionVerdict"] = []
    monkeypatch.setattr(
        "signage.integrity.decode_integrity_token", lambda token: payload
    )

    with pytest.raises(exceptions.PermissionDenied):
        verify_integrity_token("rooted-or-uncertified", "expected-hash")
