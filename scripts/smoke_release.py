#!/usr/bin/env python3
"""Load backend code from the built wheel and serve the built frontend in-process."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import secrets
import sys
import tempfile
import zipfile
from pathlib import Path


async def _asgi_get(app, path: str) -> tuple[int, bytes]:  # noqa: ANN001
    status_code = 0
    body = bytearray()
    request_consumed = False

    async def receive() -> dict[str, object]:
        nonlocal request_consumed
        if not request_consumed:
            request_consumed = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        nonlocal status_code
        if message["type"] == "http.response.start":
            status_code = int(message["status"])
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"release.example.test")],
            "client": ("127.0.0.1", 12345),
            "server": ("release.example.test", 443),
        },
        receive,
        send,
    )
    return status_code, bytes(body)


def _hpke_wheel_roundtrip() -> None:
    from app.crypto import (
        EnvelopePurpose,
        ImtSyncCredentialContext,
        PlaintextProfile,
        RecipientPrivateKey,
        RecipientPrivateKeyring,
        decode_imt_password_frame,
        encode_imt_password_frame,
        open_envelope,
        seal_envelope,
    )
    from cryptography.hazmat.primitives.asymmetric import x25519

    native_key = x25519.X25519PrivateKey.generate()
    recipient = RecipientPrivateKey.from_raw_bytes(native_key.private_bytes_raw())
    context = ImtSyncCredentialContext(
        account_id="11111111-1111-4111-8111-111111111111",
        imt_login="release.fixture",
        credential_generation=1,
        consent_version=1,
    )
    secret = secrets.token_urlsafe(24)
    frame = encode_imt_password_frame(secret)
    envelope = seal_envelope(
        recipient.public_key,
        purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
        profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
        context=context,
        plaintext=frame,
    )
    keyring = RecipientPrivateKeyring(
        [(recipient.key_id, recipient)],
        active_key_id=recipient.key_id,
    )
    opened = open_envelope(
        envelope,
        keyring,
        purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
        profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
        context=context,
    )
    if decode_imt_password_frame(opened) != secret:
        raise SystemExit("release-smoke: HPKE wheel roundtrip failed")
    del opened, keyring, envelope, frame, secret, context, recipient, native_key


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    args = parser.parse_args()
    os.environ.update(
        {
            "BOTNOTE_ENVIRONMENT": "test",
            "BOTNOTE_DATABASE_URL": "sqlite+pysqlite:///:memory:",
            "BOTNOTE_CREDENTIAL_KEY": base64.urlsafe_b64encode(b"r" * 32).decode(),
            "BOTNOTE_TOKEN_PEPPER": "synthetic-release-pepper-value-32",
            "BOTNOTE_PUBLIC_ORIGIN": "https://release.example.test",
            "BOTNOTE_ALLOWED_HOSTS": '["release.example.test"]',
            "BOTNOTE_FRONTEND_DIST": str(args.dist.resolve()),
        }
    )
    with tempfile.TemporaryDirectory(prefix="imtegrale-wheel-") as temporary:
        with zipfile.ZipFile(args.wheel) as archive:
            archive.extractall(temporary)
        sys.path.insert(0, temporary)
        from app.main import app

        _hpke_wheel_roundtrip()
        live_status, live_body = asyncio.run(_asgi_get(app, "/health/live"))
        root_status, root_body = asyncio.run(_asgi_get(app, "/"))
        if live_status != 200 or json.loads(live_body).get("status") != "ok":
            raise SystemExit("release-smoke: liveness failed")
        if root_status != 200 or b'<div id="root"></div>' not in root_body:
            raise SystemExit("release-smoke: frontend failed")
    print("release-smoke: ok")


if __name__ == "__main__":
    main()
