import base64
import binascii
import hmac
import json
import string
from datetime import datetime
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework import exceptions


def configured_certificate_fingerprints():
    """Return configured APK signing-certificate SHA-256 fingerprints.

    Operators record the hex fingerprint emitted by ``apksigner``. Play
    Integrity returns the same bytes in URL-safe Base64, so keep configuration
    readable and normalize the API value separately.
    """

    fingerprints = set()
    for raw in settings.PLAY_INTEGRITY_APP_CERTIFICATE_SHA256:
        fingerprint = raw.replace(":", "").replace(" ", "").lower()
        if len(fingerprint) != 64 or any(
            character not in string.hexdigits for character in fingerprint
        ):
            raise ImproperlyConfigured(
                "PLAY_INTEGRITY_APP_CERTIFICATE_SHA256 must contain comma-separated "
                "SHA-256 certificate fingerprints in 64-character hex."
            )
        fingerprints.add(fingerprint)
    if not fingerprints:
        raise ImproperlyConfigured(
            "PLAY_INTEGRITY_APP_CERTIFICATE_SHA256 is required in production."
        )
    return fingerprints


def certificate_digest_hex(value):
    if not isinstance(value, str):
        return None
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, UnicodeEncodeError, binascii.Error):
        return None
    return decoded.hex() if len(decoded) == 32 else None


def _credentials_info():
    raw = settings.PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON
    if not raw:
        raise ImproperlyConfigured(
            "PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON is required in production."
        )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ImproperlyConfigured(
            "PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON must be valid JSON."
        ) from exc


def decode_integrity_token(token):
    """Decode a Play Integrity token using a narrowly scoped Google credential."""
    import requests
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_info(
        _credentials_info(),
        scopes=["https://www.googleapis.com/auth/playintegrity"],
    )
    session = AuthorizedSession(credentials)
    package = settings.PLAY_INTEGRITY_PACKAGE_NAME
    url = f"https://playintegrity.googleapis.com/v1/{package}:decodeIntegrityToken"
    try:
        response = session.post(url, json={"integrityToken": token}, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise exceptions.AuthenticationFailed(
            "Device integrity could not be verified."
        ) from exc
    return response.json().get("tokenPayloadExternal", {})


def verify_integrity_token(token, expected_request_hash):
    payload = decode_integrity_token(token)
    request = payload.get("requestDetails", {})
    if request.get("requestPackageName") != settings.PLAY_INTEGRITY_PACKAGE_NAME:
        raise exceptions.AuthenticationFailed("Device integrity requirements failed.")
    if not hmac.compare_digest(
        str(request.get("requestHash", "")), expected_request_hash
    ):
        raise exceptions.AuthenticationFailed("Device integrity requirements failed.")
    try:
        token_time = datetime.fromtimestamp(
            int(request["timestampMillis"]) / 1000, tz=dt_timezone.utc
        )
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise exceptions.AuthenticationFailed(
            "Device integrity requirements failed."
        ) from exc
    age = abs((datetime.now(tz=dt_timezone.utc) - token_time).total_seconds())
    if age > settings.PLAY_INTEGRITY_MAX_TOKEN_AGE_SECONDS:
        raise exceptions.AuthenticationFailed("Device integrity token expired.")
    verdicts = payload.get("deviceIntegrity", {}).get("deviceRecognitionVerdict", [])
    if "MEETS_DEVICE_INTEGRITY" not in verdicts:
        raise exceptions.PermissionDenied("Device integrity requirements failed.")
    app_integrity = payload.get("appIntegrity", {})
    if not isinstance(app_integrity, dict):
        raise exceptions.AuthenticationFailed("Device integrity requirements failed.")
    if app_integrity.get("packageName") != settings.PLAY_INTEGRITY_PACKAGE_NAME:
        raise exceptions.AuthenticationFailed("Device integrity requirements failed.")
    # Staff sideload the company APK. Google labels that build
    # UNRECOGNIZED_VERSION, but still returns its signing certificate digest;
    # reject it unless that digest is explicitly approved by the operator.
    if app_integrity.get("appRecognitionVerdict") not in {
        "PLAY_RECOGNIZED",
        "UNRECOGNIZED_VERSION",
    }:
        raise exceptions.AuthenticationFailed("Device integrity requirements failed.")
    certificate_digests = app_integrity.get("certificateSha256Digest", [])
    if not isinstance(certificate_digests, list):
        raise exceptions.AuthenticationFailed("Device integrity requirements failed.")
    token_fingerprints = {
        fingerprint
        for value in certificate_digests
        if (fingerprint := certificate_digest_hex(value))
    }
    if not token_fingerprints.intersection(configured_certificate_fingerprints()):
        raise exceptions.AuthenticationFailed("Device integrity requirements failed.")
    return payload
