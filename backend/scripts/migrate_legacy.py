#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import UTC, datetime

from app.calculations import clean_text, ue_code, ue_year
from app.database import SessionLocal, utcnow
from app.models import Account, Event, Note, UeSetting, new_id
from app.security import cipher_for
from app.services.imt import PassEntry
from app.services.sync import pass_source_key
from sqlalchemy import select


def parse_datetime(value: str | None) -> datetime:
    if not value:
        return utcnow()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    except ValueError:
        return utcnow()


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def legacy_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate the legacy single-user IMTégrale SQLite database")
    parser.add_argument("--source", default="/var/lib/botnote/botnote.db")
    args = parser.parse_args()

    username = required_env("IMTA_USERNAME").lower()
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    source = sqlite3.connect(args.source)
    source.row_factory = sqlite3.Row

    with SessionLocal() as db:
        account = db.scalar(select(Account).where(Account.imt_username == username))
        if account is None:
            account_id = new_id()
            account = Account(
                id=account_id,
                imt_username=username,
                display_name=username.split("@", 1)[0],
            )
            db.add(account)
            db.flush()

        if telegram_token and telegram_chat_id:
            cipher = cipher_for()
            account.encrypted_telegram_token = cipher.encrypt(
                telegram_token,
                context=f"telegram-token:{account.id}",
            )
            account.encrypted_telegram_chat_id = cipher.encrypt(
                telegram_chat_id,
                context=f"telegram-chat:{account.id}",
            )
            account.telegram_enabled = True

        note_columns = legacy_columns(source, "notes")
        inserted_notes = 0
        for row in source.execute("SELECT * FROM notes"):
            code = ue_code(row["ue"])
            source_name = row["source"]
            if source_name == "pass":
                source_key = pass_source_key(
                    PassEntry(
                        ue_code=code,
                        label=row["label"],
                        score=float(row["note"]),
                        coefficient=float(row["coeff"]),
                        is_resit=bool(row["is_rattrapage"]),
                    )
                )
            else:
                source_key = f"legacy-{row['id']}"
            exists = db.scalar(
                select(Note.id).where(
                    Note.account_id == account.id,
                    Note.source == source_name,
                    Note.source_key == source_key,
                )
            )
            if exists:
                continue
            note = Note(
                account_id=account.id,
                source=source_name,
                source_key=source_key,
                ue_code=code,
                raw_label=clean_text(row["label"]),
                raw_score=float(row["note"]),
                raw_coefficient=float(row["coeff"]),
                raw_is_resit=bool(row["is_rattrapage"]),
                label_override=row["label_override"] if "label_override" in note_columns else None,
                score_override=row["note_override"] if "note_override" in note_columns else None,
                coefficient_override=row["coeff_override"] if "coeff_override" in note_columns else None,
                detected_at=parse_datetime(row["detected_at"]),
                updated_at=parse_datetime(row["updated_at"]),
                archived=bool(row["archived"]),
            )
            db.add(note)
            inserted_notes += 1

        inserted_ues = 0
        for row in source.execute("SELECT * FROM ue_settings"):
            code = ue_code(row["ue"])
            setting = db.scalar(
                select(UeSetting).where(UeSetting.account_id == account.id, UeSetting.code == code)
            )
            if setting is None:
                setting = UeSetting(account_id=account.id, code=code)
                db.add(setting)
                inserted_ues += 1
            setting.credits_ects = row["credits_ects"]
            setting.title = clean_text(row["title"])
            setting.year = clean_text(row["year"]) or ue_year(code)
            setting.updated_at = parse_datetime(row["updated_at"])

        event_count = db.scalar(select(Event.id).where(Event.account_id == account.id).limit(1))
        if event_count is None:
            for row in source.execute("SELECT * FROM events ORDER BY id"):
                try:
                    payload = json.loads(row["payload"])
                except (TypeError, json.JSONDecodeError):
                    payload = {"legacy_payload": str(row["payload"])}
                db.add(
                    Event(
                        account_id=account.id,
                        kind=f"legacy:{row['kind']}",
                        payload=payload,
                        actor="migration",
                        created_at=parse_datetime(row["created_at"]),
                    )
                )

        db.add(
            Event(
                account_id=account.id,
                kind="migration:completed",
                payload={
                    "notes": inserted_notes,
                    "ues": inserted_ues,
                    "imt_password_persisted": False,
                },
                actor="migration",
            )
        )
        db.commit()
        print(
            json.dumps(
                {
                    "ok": True,
                    "account_id": account.id,
                    "notes_inserted": inserted_notes,
                    "ues_inserted": inserted_ues,
                }
            )
        )


if __name__ == "__main__":
    main()
