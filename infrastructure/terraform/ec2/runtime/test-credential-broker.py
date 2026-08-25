#!/usr/bin/python3
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


root = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader(
    "duducar_credential_broker", str(root / "duducar-credential-broker")
)
spec = importlib.util.spec_from_loader(loader.name, loader)
broker = importlib.util.module_from_spec(spec)
loader.exec_module(broker)

assert broker.BIND_ADDRESS == "169.254.170.2"
assert broker.BIND_PORT == 51679
assert broker.TOKEN_PATH == "/run/duducar/broker-secrets/aws-credentials-token"

token = "a" * 64
expiry = (datetime.now(timezone.utc) + timedelta(minutes=59)).isoformat()
aws_response = json.dumps(
    {
        "Credentials": {
            "AccessKeyId": "ASIATEST",
            "SecretAccessKey": "test-secret",
            "SessionToken": "test-session",
            "Expiration": expiry,
        }
    }
)

with tempfile.TemporaryDirectory() as directory:
    token_path = Path(directory) / "token"
    token_path.write_text(token, encoding="ascii")
    token_path.chmod(0o400)
    broker.TOKEN_PATH = str(token_path)
    broker._credentials = None
    real_fstat = os.fstat

    def safe_token_stat(descriptor):
        value = real_fstat(descriptor)
        return SimpleNamespace(st_mode=value.st_mode, st_uid=0, st_gid=0)

    completed = SimpleNamespace(stdout=aws_response)
    environment = {
        "APPLICATION_ROLE_ARN": "arn:aws:iam::173454940059:role/duducar-application",
        "AWS_REGION": "ap-southeast-5",
    }
    with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
        broker.os, "fstat", side_effect=safe_token_stat
    ), mock.patch.object(broker.subprocess, "run", return_value=completed) as assume:
        assert broker.authorization_token() == token
        first = broker.current_credentials()
        second = broker.current_credentials()
        assert first == second
        assert assume.call_count == 1
        command = assume.call_args.args[0]
        assert command[:3] == ["/usr/bin/aws", "sts", "assume-role"]
        assert environment["APPLICATION_ROLE_ARN"] in command

        server = broker.ThreadingHTTPServer(("127.0.0.1", 0), broker.CredentialHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        endpoint = f"http://127.0.0.1:{server.server_port}{broker.CREDENTIAL_PATH}"
        try:
            try:
                urllib.request.urlopen(endpoint, timeout=2)
                raise AssertionError("Broker accepted a request without its bearer token.")
            except urllib.error.HTTPError as error:
                assert error.code == 401

            request = urllib.request.Request(
                endpoint, headers={"Authorization": token}
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                document = json.load(response)
                assert response.headers["Cache-Control"] == "no-store"
                assert document["AccessKeyId"] == "ASIATEST"
                assert document["Token"] == "test-session"
                assert "RoleArn" not in document
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

print("Credential broker authorization, role assumption, and refresh cache checks passed.")
