#!/usr/bin/python3
import hashlib
import base64
import importlib.machinery
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


root = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader(
    "duducar_backup_verify", str(root / "duducar-backup-verify")
)
spec = importlib.util.spec_from_loader(loader.name, loader)
verifier = importlib.util.module_from_spec(spec)
loader.exec_module(verifier)

with tempfile.TemporaryDirectory() as directory:
    work = Path(directory)
    backup_root = work / "backups"
    receipt = work / "state" / "latest-remote.json"
    host_config = work / "host.env"
    backup_root.mkdir()
    archive = backup_root / "duducar-signage-postgres-20260818T000000Z.dump"
    archive.write_bytes(b"verified database archive")
    archive.chmod(0o600)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sidecar = archive.with_suffix(".dump.sha256")
    sidecar.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    sidecar.chmod(0o600)
    host_config.write_text(
        "AWS_REGION=ap-southeast-5\n"
        "APPLICATION_ROLE_ARN=arn:aws:iam::173454940059:role/duducar-signage-production-ec2-application\n",
        encoding="ascii",
    )
    host_config.chmod(0o644)

    verifier.BACKUP_ROOT = backup_root
    verifier.RECEIPT = receipt
    verifier.HOST_CONFIG = host_config
    real_fstat = os.fstat
    real_lstat = os.lstat
    corrupt_metadata = False

    def reviewed_fstat(descriptor):
        value = real_fstat(descriptor)
        target = os.readlink(f"/proc/self/fd/{descriptor}")
        uid = 0 if target in {str(receipt), str(host_config)} else 10001
        return SimpleNamespace(st_mode=value.st_mode, st_uid=uid)

    def reviewed_lstat(path):
        value = real_lstat(path)
        return SimpleNamespace(st_mode=value.st_mode, st_uid=0)

    def fake_aws_run(command, **_kwargs):
        nonlocal_corrupt = corrupt_metadata
        if command[1:3] == ["sts", "assume-role"]:
            assert command[command.index("--role-arn") + 1] == (
                "arn:aws:iam::173454940059:role/duducar-signage-production-ec2-application"
            )
            assert command[command.index("--region") + 1] == "ap-southeast-5"
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "Credentials": {
                            "AccessKeyId": "assumed-access-key",
                            "SecretAccessKey": "assumed-secret-key",
                            "SessionToken": "assumed-session-token",
                        }
                    }
                )
            )
        key = command[command.index("--key") + 1]
        if command[2] == "head-object":
            object_digest = digest if key.endswith(".dump") else hashlib.sha256(sidecar.read_bytes()).hexdigest()
            document = {
                "ContentLength": archive.stat().st_size if key.endswith(".dump") else sidecar.stat().st_size,
                "Metadata": {"sha256": "0" * 64 if nonlocal_corrupt else digest},
                "ServerSideEncryption": "aws:kms",
                "ChecksumSHA256": base64.b64encode(bytes.fromhex(object_digest)).decode("ascii"),
                "VersionId": "archive-version" if key.endswith(".dump") else "sidecar-version",
            }
            return SimpleNamespace(stdout=json.dumps(document))
        if command[2] == "get-object":
            Path(command[-1]).write_bytes(sidecar.read_bytes())
            return SimpleNamespace(stdout="{}")
        raise AssertionError(f"Unexpected AWS command: {command}")

    with mock.patch.object(verifier.os, "fstat", side_effect=reviewed_fstat), mock.patch.object(
        verifier.os, "lstat", side_effect=reviewed_lstat
    ), mock.patch.object(verifier.subprocess, "run", side_effect=fake_aws_run):
        with mock.patch.dict(os.environ, {"DUDUCAR_BACKUP_ASSUME_ROLE": "1"}, clear=True):
            assumed_credentials = verifier.application_credentials()
        assert assumed_credentials["AWS_ACCESS_KEY_ID"] == "assumed-access-key"
        assert assumed_credentials["AWS_EC2_METADATA_DISABLED"] == "true"
        verifier.record("backup-bucket", {})
        document = json.loads(receipt.read_text(encoding="utf-8"))
        assert document["archive_version_id"] == "archive-version"
        assert document["sidecar_version_id"] == "sidecar-version"
        assert document["sha256"] == digest
        verifier.check("backup-bucket", {})

        corrupt_metadata = True
        try:
            verifier.check("backup-bucket", {})
            raise AssertionError("Verifier accepted changed remote digest metadata.")
        except RuntimeError as error:
            assert "digest metadata changed" in str(error)

print("Remote backup version, sidecar, receipt, and drift checks passed.")
