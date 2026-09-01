import base64
import json
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.test import override_settings
from rest_framework import exceptions

from signage.integrity import (
    AWS_CREDENTIAL_BROKER_URL,
    AWS_SUBJECT_TOKEN_TYPE,
    WIF_IMPERSONATION_URL,
    _AwsCredentialBrokerSupplier,
    validate_play_integrity_credentials,
    verify_integrity_token,
)

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


def test_aws_credential_broker_supplier_returns_scoped_role_credentials(
    monkeypatch, tmp_path
):
    token_path = tmp_path / "aws-credentials-token"
    token = "a" * 64
    token_path.write_text(token, encoding="ascii")
    token_path.chmod(0o400)
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", AWS_CREDENTIAL_BROKER_URL)
    monkeypatch.setenv("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE", str(token_path))
    monkeypatch.setenv("AWS_S3_REGION_NAME", "ap-southeast-5")
    response = type(
        "Response",
        (),
        {
            "status": 200,
            "data": json.dumps(
                {
                    "AccessKeyId": "ASIATEST",
                    "SecretAccessKey": "secret",
                    "Token": "session",
                }
            ).encode("utf-8"),
        },
    )()
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        return response

    supplier = _AwsCredentialBrokerSupplier()
    credentials = supplier.get_aws_security_credentials(None, request)

    assert supplier.get_aws_region(None, request) == "ap-southeast-5"
    assert credentials.access_key_id == "ASIATEST"
    assert vars(credentials)["secret_access_key"] == "secret"  # noqa: S105
    assert vars(credentials)["session_token"] == "session"  # noqa: S105
    assert calls == [
        {
            "url": AWS_CREDENTIAL_BROKER_URL,
            "method": "GET",
            "headers": {"Authorization": token},
        }
    ]


@override_settings(PLAY_INTEGRITY_PROJECT_NUMBER="552923442234")
def test_aws_external_account_configuration_is_accepted():
    info = {
        "type": "external_account",
        "audience": (
            "//iam.googleapis.com/projects/552923442234/locations/global/"
            "workloadIdentityPools/duducar-production-aws/providers/ec2-application"
        ),
        "subject_token_type": AWS_SUBJECT_TOKEN_TYPE,
        "token_url": "https://sts.googleapis.com/v1/token",
        "service_account_impersonation_url": WIF_IMPERSONATION_URL,
        "credential_source": {
            "environment_id": "aws1",
            "imdsv2_session_token_url": (
                "http://169.254.169.254/latest/api/token"
            ),
            "region_url": (
                "http://169.254.169.254/latest/meta-data/placement/availability-zone"
            ),
            "regional_cred_verification_url": (
                "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&"
                "Version=2011-06-15"
            ),
            "url": (
                "http://169.254.169.254/latest/meta-data/iam/security-credentials"
            ),
        },
    }

    assert validate_play_integrity_credentials(info) is info
