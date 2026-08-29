import base64
import binascii
import hmac
import json
import os
import re
import stat
import string
from datetime import datetime
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework import exceptions

AWS_CREDENTIAL_BROKER_URL = "http://169.254.170.2:51679/v2/credentials"
AWS_SUBJECT_TOKEN_TYPE = "urn:ietf:params:aws:token-type:aws4_request"  # noqa: S105
PLAY_INTEGRITY_SCOPE = "https://www.googleapis.com/auth/playintegrity"
WIF_SERVICE_ACCOUNT = (
    "play-integrity-decoder@healthy-wares-506910-g5.iam.gserviceaccount.com"
)
WIF_IMPERSONATION_URL = (
    "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
    f"{WIF_SERVICE_ACCOUNT}:generateAccessToken"
)


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


def validate_play_integrity_credentials(info):
    """Validate the supported static or AWS-federated credential shape."""

    if not isinstance(info, dict):
        raise ImproperlyConfigured(
            "PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON must contain a JSON object."
        )
    credential_type = info.get("type")
    if credential_type == "service_account":
        required_fields = {"project_id", "private_key", "client_email"}
        missing = required_fields.difference(info)
        if missing:
            raise ImproperlyConfigured(
                "Play Integrity credentials are missing: "
                + ", ".join(sorted(missing))
            )
        return info
    if credential_type != "external_account":
        raise ImproperlyConfigured(
            "Play Integrity credentials must be a service account or AWS "
            "external account."
        )

    required_fields = {
        "audience",
        "subject_token_type",
        "token_url",
        "service_account_impersonation_url",
        "credential_source",
    }
    missing = required_fields.difference(info)
    if missing:
        raise ImproperlyConfigured(
            "AWS federation credentials are missing: " + ", ".join(sorted(missing))
        )
    if info["subject_token_type"] != AWS_SUBJECT_TOKEN_TYPE:
        raise ImproperlyConfigured(
            "AWS federation credentials must use the AWS SigV4 subject-token type."
        )
    if info["token_url"] != "https://sts.googleapis.com/v1/token":  # noqa: S105
        raise ImproperlyConfigured(
            "AWS federation credentials must use the Google STS endpoint."
        )
    expected_audience = (
        "//iam.googleapis.com/projects/"
        f"{settings.PLAY_INTEGRITY_PROJECT_NUMBER}/locations/global/"
        "workloadIdentityPools/"
    )
    audience = info["audience"]
    if not isinstance(audience, str) or not re.fullmatch(
        re.escape(expected_audience) + r"[a-z0-9-]+/providers/[a-z0-9-]+",
        audience,
    ):
        raise ImproperlyConfigured(
            "AWS federation audience does not match the configured Play "
            "Integrity project."
        )
    if info["service_account_impersonation_url"] != WIF_IMPERSONATION_URL:
        raise ImproperlyConfigured(
            "AWS federation must impersonate the approved Play Integrity "
            "service account."
        )

    credential_source = info["credential_source"]
    expected_source = {
        "environment_id": "aws1",
        "imdsv2_session_token_url": "http://169.254.169.254/latest/api/token",
        "region_url": "http://169.254.169.254/latest/meta-data/placement/availability-zone",
        "regional_cred_verification_url": (
            "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15"
        ),
        "url": "http://169.254.169.254/latest/meta-data/iam/security-credentials",
    }
    if credential_source != expected_source:
        raise ImproperlyConfigured(
            "AWS federation credential-source endpoints are not the approved "
            "AWS endpoints."
        )
    return info


def _credentials_info():
    raw = settings.PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON
    if not raw:
        raise ImproperlyConfigured(
            "PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON is required in production."
        )
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ImproperlyConfigured(
            "PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON must be valid JSON."
        ) from exc
    return validate_play_integrity_credentials(info)


class _AwsCredentialBrokerSupplier:
    """Supply short-lived AWS application-role credentials to google-auth."""

    def __init__(self):
        self._broker_url = os.getenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", "").strip()
        self._token_path = os.getenv(
            "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE", ""
        ).strip()
        if self._broker_url != AWS_CREDENTIAL_BROKER_URL or not self._token_path:
            raise ImproperlyConfigured(
                "The AWS credential broker is not configured for Play Integrity "
                "federation."
            )

    def _authorization_token(self):
        descriptor = -1
        try:
            descriptor = os.open(self._token_path, os.O_RDONLY | os.O_NOFOLLOW)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o400
            ):
                raise ValueError
            with os.fdopen(descriptor, encoding="ascii") as handle:
                descriptor = -1
                token = handle.read().strip()
        except (OSError, UnicodeError, ValueError) as exc:
            if descriptor >= 0:
                os.close(descriptor)
            from google.auth import exceptions as auth_exceptions

            raise auth_exceptions.RefreshError(
                "The AWS credential broker authorization file is unsafe."
            ) from exc
        if len(token) != 64 or any(
            character not in string.hexdigits.lower() for character in token
        ):
            from google.auth import exceptions as auth_exceptions

            raise auth_exceptions.RefreshError(
                "The AWS credential broker authorization token is invalid."
            )
        return token

    def get_aws_region(self, _context, _request):
        for name in ("AWS_REGION", "AWS_DEFAULT_REGION", "AWS_S3_REGION_NAME"):
            region = os.getenv(name, "").strip()
            if region:
                return region
        from google.auth import exceptions as auth_exceptions

        raise auth_exceptions.RefreshError("The AWS region is not configured.")

    def get_aws_security_credentials(self, _context, request):
        from google.auth import aws
        from google.auth import exceptions as auth_exceptions

        try:
            response = request(
                url=self._broker_url,
                method="GET",
                headers={"Authorization": self._authorization_token()},
            )
            if response.status != 200:
                raise ValueError
            payload = json.loads(
                response.data.decode("utf-8")
                if isinstance(response.data, bytes)
                else response.data
            )
            access_key_id = payload["AccessKeyId"]
            secret_access_key = payload["SecretAccessKey"]
            session_token = payload.get("Token")
            if not isinstance(access_key_id, str) or not access_key_id:
                raise ValueError
            if not isinstance(secret_access_key, str) or not secret_access_key:
                raise ValueError
            if session_token is not None and not isinstance(session_token, str):
                raise ValueError
        except (
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
        ) as exc:
            raise auth_exceptions.RefreshError(
                "The AWS credential broker did not return valid credentials."
            ) from exc
        return aws.AwsSecurityCredentials(
            access_key_id,
            secret_access_key,
            session_token,
        )


def _google_credentials(info):
    from google.auth import aws
    from google.oauth2 import service_account

    if info["type"] == "external_account":
        return aws.Credentials(
            audience=info["audience"],
            subject_token_type=info["subject_token_type"],
            token_url=info["token_url"],
            credential_source=None,
            service_account_impersonation_url=info[
                "service_account_impersonation_url"
            ],
            aws_security_credentials_supplier=_AwsCredentialBrokerSupplier(),
            scopes=[PLAY_INTEGRITY_SCOPE],
        )
    return service_account.Credentials.from_service_account_info(
        info,
        scopes=[PLAY_INTEGRITY_SCOPE],
    )


def decode_integrity_token(token):
    """Decode a Play Integrity token using a narrowly scoped Google credential."""
    import requests
    from google.auth.transport.requests import AuthorizedSession

    credentials = _google_credentials(_credentials_info())
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
