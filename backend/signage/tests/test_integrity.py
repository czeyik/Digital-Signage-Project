import base64
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.test import override_settings
from rest_framework import exceptions

from signage.integrity import verify_integrity_token

CERTIFICATE_FINGERPRINT = "ab" * 32
CERTIFICATE_DIGEST = base64.urlsafe_b64encode(
    bytes.fromhex(CERTIFICATE_FINGERPRINT)
).decode().rstrip("=")


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
        "appIntegrity": {
            "appRecognitionVerdict": "UNRECOGNIZED_VERSION",
            "packageName": "com.duducar.signage",
            "certificateSha256Digest": [CERTIFICATE_DIGEST],
        },
    }


@override_settings(
    PLAY_INTEGRITY_PACKAGE_NAME="com.duducar.signage",
    PLAY_INTEGRITY_MAX_TOKEN_AGE_SECONDS=120,
    PLAY_INTEGRITY_APP_CERTIFICATE_SHA256=[CERTIFICATE_FINGERPRINT],
)
def test_integrity_accepts_configured_certified_sideload(monkeypatch):
    monkeypatch.setattr(
        "signage.integrity.decode_integrity_token",
        lambda token: integrity_payload(),
    )

    payload = verify_integrity_token("decoded-by-google", "expected-hash")

    assert payload["appIntegrity"]["appRecognitionVerdict"] == "UNRECOGNIZED_VERSION"


@pytest.mark.parametrize("failure", ["wrong_package", "wrong_hash", "expired"])
@override_settings(
    PLAY_INTEGRITY_PACKAGE_NAME="com.duducar.signage",
    PLAY_INTEGRITY_MAX_TOKEN_AGE_SECONDS=120,
    PLAY_INTEGRITY_APP_CERTIFICATE_SHA256=[CERTIFICATE_FINGERPRINT],
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
    PLAY_INTEGRITY_APP_CERTIFICATE_SHA256=[CERTIFICATE_FINGERPRINT],
)
def test_integrity_rejects_missing_device_verdict(monkeypatch):
    payload = integrity_payload()
    payload["deviceIntegrity"]["deviceRecognitionVerdict"] = []
    monkeypatch.setattr(
        "signage.integrity.decode_integrity_token", lambda token: payload
    )

    with pytest.raises(exceptions.PermissionDenied):
        verify_integrity_token("rooted-or-uncertified", "expected-hash")


@pytest.mark.parametrize(
    "field,value",
    [
        ("appRecognitionVerdict", "UNEVALUATED"),
        (
            "certificateSha256Digest",
            [base64.urlsafe_b64encode(b"different-certificate-digest-32!").decode()],
        ),
    ],
)
@override_settings(
    PLAY_INTEGRITY_PACKAGE_NAME="com.duducar.signage",
    PLAY_INTEGRITY_MAX_TOKEN_AGE_SECONDS=120,
    PLAY_INTEGRITY_APP_CERTIFICATE_SHA256=[CERTIFICATE_FINGERPRINT],
)
def test_integrity_rejects_untrusted_app_build(monkeypatch, field, value):
    payload = integrity_payload()
    payload["appIntegrity"][field] = value
    monkeypatch.setattr(
        "signage.integrity.decode_integrity_token", lambda token: payload
    )

    with pytest.raises(exceptions.AuthenticationFailed):
        verify_integrity_token("forged", "expected-hash")
