from __future__ import annotations

import argparse
import json
import ssl

import uvicorn
from sqlalchemy import select

from app.admin_security import (
    generate_admin_password,
    hash_admin_password,
    normalize_admin_username,
    write_initial_credentials,
)
from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import AdminUser
from app.observability import configure_json_logging
from app.services.key_rotation import reencrypt_stored_secrets
from app.services.operations import operational_alert_codes
from app.services.sync import sync_account, sync_all_accounts, sync_due_accounts
from app.services.worker_runtime import run_worker


def main() -> None:
    parser = argparse.ArgumentParser(prog="botnote")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)

    sync = subparsers.add_parser("sync")
    sync.add_argument("--account", required=True)

    subparsers.add_parser("sync-all")
    subparsers.add_parser("sync-due")
    worker = subparsers.add_parser("worker")
    worker.add_argument("kind", choices=("sync", "calendar", "outbox", "scheduler"))
    subparsers.add_parser("create-schema")
    admin_bootstrap = subparsers.add_parser("admin-bootstrap")
    admin_bootstrap.add_argument("--username", required=True)
    admin_bootstrap.add_argument("--output", required=True)
    key_rotation = subparsers.add_parser("keys-reencrypt")
    key_rotation.add_argument("--batch-size", type=int, default=100)
    key_rotation.add_argument("--dry-run", action="store_true")
    key_rotation.add_argument("--limit", type=int)
    subparsers.add_parser("operations-check")

    args = parser.parse_args()
    configure_json_logging()
    get_settings().validate_secrets()

    if args.command == "serve":
        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            workers=1,
            proxy_headers=False,
            server_header=False,
            # Nginx owns the deliberately redacted access log. Uvicorn's default
            # formatter includes request paths, which may contain private content IDs.
            access_log=False,
            log_config=None,
            ssl_certfile=str(get_settings().backend_tls_cert),
            ssl_keyfile=str(get_settings().backend_tls_key),
            ssl_ca_certs=str(get_settings().backend_tls_ca),
            ssl_cert_reqs=ssl.CERT_REQUIRED,
            timeout_graceful_shutdown=10,
        )
    elif args.command == "sync":
        print(json.dumps(sync_account(args.account), ensure_ascii=False))
    elif args.command == "sync-all":
        results = sync_all_accounts()
        print(json.dumps(results, ensure_ascii=False))
        if any(not item["ok"] for item in results):
            raise SystemExit(1)
    elif args.command == "sync-due":
        results = sync_due_accounts()
        print(json.dumps(results, ensure_ascii=False))
        if any(not item["ok"] for item in results):
            raise SystemExit(1)
    elif args.command == "worker":
        run_worker(args.kind)
    elif args.command == "create-schema":
        Base.metadata.create_all(engine)
    elif args.command == "admin-bootstrap":
        try:
            username = normalize_admin_username(args.username)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        password = generate_admin_password()
        with SessionLocal() as db:
            if db.scalar(select(AdminUser).where(AdminUser.username == username)) is not None:
                raise SystemExit("Administrator already exists")
            write_initial_credentials(args.output, username, password)
            db.add(
                AdminUser(
                    username=username,
                    password_hash=hash_admin_password(password),
                    must_change_password=True,
                )
            )
            db.commit()
        print(json.dumps({"ok": True, "username": username, "output": args.output}))
    elif args.command == "keys-reencrypt":
        with SessionLocal() as db:
            result = reencrypt_stored_secrets(
                db,
                batch_size=args.batch_size,
                dry_run=args.dry_run,
                max_items=args.limit,
            )
        print(json.dumps(result, sort_keys=True))
    elif args.command == "operations-check":
        with SessionLocal() as db:
            alerts = operational_alert_codes(db, get_settings())
        print(json.dumps({"ok": not alerts, "alerts": alerts}, sort_keys=True))
        if alerts:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
