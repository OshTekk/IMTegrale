#!/usr/bin/env python3
"""Load backend code from the built wheel and serve the built frontend in-process."""

from __future__ import annotations

import argparse
import base64
import os
import sys
import tempfile
import zipfile
from pathlib import Path


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
        from fastapi.testclient import TestClient

        with TestClient(app, base_url="https://release.example.test") as client:
            live = client.get("/health/live")
            root = client.get("/")
        if live.status_code != 200 or live.json().get("status") != "ok":
            raise SystemExit("release-smoke: liveness failed")
        if root.status_code != 200 or "<div id=\"root\"></div>" not in root.text:
            raise SystemExit("release-smoke: frontend failed")
    print("release-smoke: ok")


if __name__ == "__main__":
    main()
