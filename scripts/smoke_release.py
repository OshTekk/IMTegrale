#!/usr/bin/env python3
"""Load backend code from the built wheel and serve the built frontend in-process."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import secrets
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from release_snapshot import SnapshotError, verified_snapshot


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


def _isolated_sync_credentials_roundtrip() -> None:
    from app.crypto import RecipientPrivateKey
    from app.services.sync_worker_credentials import (
        CREDENTIAL_PRIVATE,
        CREDENTIAL_PUBLIC,
        SESSION_PRIVATE,
        SESSION_PUBLIC,
        load_sync_worker_credentials,
        self_test_sync_worker_credentials,
    )
    from cryptography.hazmat.primitives.asymmetric import x25519

    def pair() -> tuple[bytes, bytes]:
        native = x25519.X25519PrivateKey.generate()
        private = RecipientPrivateKey.from_raw_bytes(native.private_bytes_raw())
        return native.private_bytes_raw(), private.public_key.to_raw_bytes()

    with tempfile.TemporaryDirectory(prefix="imtegrale-sync-credentials-") as temporary:
        directory = Path(temporary)
        credential_private, credential_public = pair()
        session_private, session_public = pair()
        for name, value in {
            CREDENTIAL_PRIVATE: credential_private,
            CREDENTIAL_PUBLIC: credential_public,
            SESSION_PRIVATE: session_private,
            SESSION_PUBLIC: session_public,
        }.items():
            path = directory / name
            path.write_bytes(value)
            path.chmod(0o400)
        credentials = load_sync_worker_credentials(directory)
        self_test_sync_worker_credentials(credentials)
        del credentials, credential_private, credential_public, session_private, session_public


def _safe_extract_wheel(wheel: Path, destination: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        forbidden_key_names = {
            "imt-sync-credential-v1.private.raw",
            "imt-sync-credential-v1.public.raw",
            "pass-service-session-v1.private.raw",
            "pass-service-session-v1.public.raw",
            "keyset.json",
        }
        seen: set[str] = set()
        for entry in archive.infolist():
            name = entry.filename.rstrip("/")
            parsed = PurePosixPath(name)
            mode = entry.external_attr >> 16
            if (
                not name
                or parsed.is_absolute()
                or any(part in {"", ".", ".."} for part in parsed.parts)
                or "\\" in name
                or "\x00" in name
                or name in seen
                or stat.S_ISLNK(mode)
                or Path(name).name in forbidden_key_names
            ):
                raise SystemExit("release-smoke: wheel structure rejected")
            seen.add(name)
            target = destination / name
            if entry.is_dir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o400,
            )
            try:
                with archive.open(entry) as source:
                    while chunk := source.read(1024 * 1024):
                        view = memoryview(chunk)
                        while view:
                            written = os.write(descriptor, view)
                            if written <= 0:
                                raise SystemExit("release-smoke: wheel extraction failed")
                            view = view[written:]
            finally:
                os.close(descriptor)


def smoke(wheel: Path, dist: Path) -> None:
    os.environ.update(
        {
            "BOTNOTE_ENVIRONMENT": "test",
            "BOTNOTE_DATABASE_URL": "sqlite+pysqlite:///:memory:",
            "BOTNOTE_CREDENTIAL_KEY": base64.urlsafe_b64encode(b"r" * 32).decode(),
            "BOTNOTE_TOKEN_PEPPER": "synthetic-release-pepper-value-32",
            "BOTNOTE_PUBLIC_ORIGIN": "https://release.example.test",
            "BOTNOTE_ALLOWED_HOSTS": '["release.example.test"]',
            "BOTNOTE_FRONTEND_DIST": str(dist.resolve()),
        }
    )
    with tempfile.TemporaryDirectory(prefix="imtegrale-wheel-") as temporary:
        _safe_extract_wheel(wheel, Path(temporary))
        sys.path.insert(0, temporary)
        from app.main import app

        _hpke_wheel_roundtrip()
        _isolated_sync_credentials_roundtrip()
        live_status, live_body = asyncio.run(_asgi_get(app, "/health/live"))
        root_status, root_body = asyncio.run(_asgi_get(app, "/"))
        if live_status != 200 or json.loads(live_body).get("status") != "ok":
            raise SystemExit("release-smoke: liveness failed")
        if root_status != 200 or b'<div id="root"></div>' not in root_body:
            raise SystemExit("release-smoke: frontend failed")
    print("release-smoke: ok")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--non-release-directory", action="store_true")
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--dist", type=Path)
    args = parser.parse_args()
    try:
        if args.snapshot is not None:
            if (
                not args.expected_sha256
                or args.non_release_directory
                or args.wheel is not None
                or args.dist is not None
            ):
                parser.error("snapshot smoke requires only --snapshot and --expected-sha256")
            with verified_snapshot(args.snapshot, args.expected_sha256) as snapshot:
                smoke(snapshot.wheel, snapshot.frontend)
            return 0
        if (
            not args.non_release_directory
            or args.expected_sha256 is not None
            or args.wheel is None
            or args.dist is None
        ):
            parser.error("legacy smoke requires --non-release-directory, --wheel and --dist")
        smoke(args.wheel, args.dist)
        return 0
    except SnapshotError as exc:
        print(f"release-smoke: denied code={exc.code}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
