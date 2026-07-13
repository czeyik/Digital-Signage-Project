import hmac
import json
from datetime import datetime
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework import exceptions


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
    return payload
