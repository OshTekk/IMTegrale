from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path


def configure_synthetic_environment() -> None:
    defaults = {
        "BOTNOTE_ENVIRONMENT": "test",
        "BOTNOTE_DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "BOTNOTE_CREDENTIAL_KEY": base64.urlsafe_b64encode(b"o" * 32).decode(),
        "BOTNOTE_TOKEN_PEPPER": "openapi-generation-only-synthetic-pepper",
        "BOTNOTE_PUBLIC_ORIGIN": "https://openapi.invalid",
        "BOTNOTE_ALLOWED_HOSTS": '["openapi.invalid"]',
        "BOTNOTE_SECURE_COOKIES": "true",
        "BOTNOTE_FRONTEND_DIST": "/tmp/imtegrale-openapi-frontend",
        "BOTNOTE_SYNC_LOCK_DIR": "/tmp/imtegrale-openapi-locks",
        "BOTNOTE_ADMIN_ALLOWED_IDENTITIES": "[]",
    }
    for name, value in defaults.items():
        os.environ.setdefault(name, value)


def rendered_openapi() -> str:
    configure_synthetic_environment()
    from app.main import app

    return json.dumps(
        app.openapi(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    rendered = rendered_openapi()
    if args.check:
        try:
            existing = args.output.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise SystemExit(f"OpenAPI artifact is missing: {args.output}") from exc
        if existing != rendered:
            raise SystemExit(
                f"OpenAPI artifact drifted; run scripts/export_openapi.py {args.output}"
            )
        print(f"openapi: current {args.output}")
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"openapi: wrote {args.output}")


if __name__ == "__main__":
    main()
