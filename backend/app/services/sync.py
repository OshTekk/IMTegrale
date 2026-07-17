from __future__ import annotations

import fcntl
import hashlib
import logging
import math
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.calculations import clean_text, ue_code, ue_year
from app.config import get_settings
from app.database import SessionLocal, utcnow
from app.limits import MAX_ARCHIVED_PASS_NOTES_PER_ACCOUNT, MAX_UE_SETTINGS_PER_ACCOUNT
from app.models import Account, Note, SyncRequest, UeSetting
from app.security import cipher_for
from app.services.cohort_pulse import emit_cohort_pulse
from app.services.dashboard import calculate_ues
from app.services.events import record_event
from app.services.imt import (
    MAX_NOTE_COEFFICIENT,
    MAX_NOTE_LABEL_LENGTH,
    MAX_PASS_ENTRIES,
    MAX_UE_CODE_LENGTH,
    ImtAuthenticationError,
    ImtFetchError,
    PassEntry,
    PassProfile,
)
from app.services.leaderboard import (
    apply_detected_academic_profile,
    apply_detected_campus,
    apply_official_identity,
    normalize_detected_campus,
)
from app.services.pass_gateway import PassAccessRejected, perform_sync_operation
from app.services.sync_control import (
    ACTIVE_SYNC_STATUSES,
    SYNC_LEASE_SECONDS,
    SyncInProgress,
    SyncLeaseExpired,
    ensure_utc,
    finalize_sync_request,
    reserve_sync_request,
    server_idempotency_key,
    sync_log_reference,
)
from app.services.sync_schedule import (
    auto_sync_is_due,
    automatic_lateness_ratio,
    defer_automatic_sync,
    update_adaptive_cadence,
)
from app.services.telegram import TelegramError, build_new_notes_message, send_telegram

logger = logging.getLogger(__name__)


class SyncAlreadyRunning(RuntimeError):
    pass


class AutomaticSyncNotAllowed(RuntimeError):
    pass


def pass_source_key(entry: PassEntry) -> str:
    stable = "|".join(
        (
            ue_code(entry.ue_code),
            clean_text(entry.label).casefold(),
            f"{entry.coefficient:g}",
            "resit" if entry.is_resit else "normal",
        )
    )
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:32]


def validate_pass_entries(entries: list[PassEntry]) -> None:
    if len(entries) > MAX_PASS_ENTRIES:
        raise ImtFetchError("PASS a fourni trop de notes")
    for entry in entries:
        code = ue_code(entry.ue_code)
        label = clean_text(entry.label)
        if not code or len(code) > MAX_UE_CODE_LENGTH:
            raise ImtFetchError("PASS a fourni un code UE invalide")
        if not label or len(label) > MAX_NOTE_LABEL_LENGTH:
            raise ImtFetchError("PASS a fourni un libellé de note invalide")
        if not math.isfinite(entry.score) or not 0 <= entry.score <= 20:
            raise ImtFetchError("PASS a fourni une note invalide")
        if (
            not math.isfinite(entry.coefficient)
            or entry.coefficient <= 0
            or entry.coefficient > MAX_NOTE_COEFFICIENT
        ):
            raise ImtFetchError("PASS a fourni un coefficient invalide")


@contextmanager
def account_sync_lock(account_id: str):
    directory = get_settings().sync_lock_dir
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = Path(directory) / f"sync-{account_id}.lock"
    with lock_path.open("w") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SyncAlreadyRunning("Une synchronisation est déjà en cours") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def apply_pass_entries(
    db: Session,
    account: Account,
    entries: list[PassEntry],
    *,
    actor: str = "system",
    initial_import: bool = False,
) -> dict:
    validate_pass_entries(entries)
    incoming_ue_codes = {ue_code(entry.ue_code) for entry in entries}
    if len(incoming_ue_codes) > MAX_UE_SETTINGS_PER_ACCOUNT:
        raise ImtFetchError("PASS a fourni trop d'UE distinctes")
    existing = {
        note.source_key: note
        for note in db.scalars(select(Note).where(Note.account_id == account.id, Note.source == "pass"))
    }
    known_ue_codes = set(db.scalars(select(UeSetting.code).where(UeSetting.account_id == account.id)))
    missing_ue_codes = incoming_ue_codes - known_ue_codes
    if len(known_ue_codes) + len(missing_ue_codes) > MAX_UE_SETTINGS_PER_ACCOUNT:
        raise ImtFetchError("La limite d'UE du compte serait dépassée")
    active_keys: set[str] = set()
    inserted: list[Note] = []
    changed = 0

    for entry in entries:
        code = ue_code(entry.ue_code)
        key = pass_source_key(entry)
        active_keys.add(key)
        note = existing.get(key)
        if note is None:
            note = Note(
                account_id=account.id,
                source="pass",
                source_key=key,
                ue_code=code,
                raw_label=clean_text(entry.label),
                raw_score=entry.score,
                raw_coefficient=entry.coefficient,
                raw_is_resit=entry.is_resit,
            )
            db.add(note)
            inserted.append(note)
            existing[key] = note
        else:
            values = (
                code,
                clean_text(entry.label),
                entry.score,
                entry.coefficient,
                entry.is_resit,
                False,
            )
            current = (
                note.ue_code,
                note.raw_label,
                note.raw_score,
                note.raw_coefficient,
                note.raw_is_resit,
                note.archived,
            )
            if values != current:
                note.ue_code = code
                note.raw_label = clean_text(entry.label)
                note.raw_score = entry.score
                note.raw_coefficient = entry.coefficient
                note.raw_is_resit = entry.is_resit
                note.archived = False
                note.updated_at = utcnow()
                changed += 1

        if code not in known_ue_codes:
            db.add(UeSetting(account_id=account.id, code=code, year=ue_year(code)))
            known_ue_codes.add(code)

    archive_filters = [
        Note.account_id == account.id,
        Note.source == "pass",
        Note.archived.is_(False),
    ]
    if active_keys:
        archive_filters.append(Note.source_key.not_in(active_keys))
    archived_result = db.execute(
        update(Note).where(*archive_filters).values(archived=True, updated_at=utcnow())
    )
    archived_count = max(0, archived_result.rowcount or 0)

    db.flush()
    stale_archived_ids = (
        select(Note.id)
        .where(
            Note.account_id == account.id,
            Note.source == "pass",
            Note.archived.is_(True),
        )
        .order_by(Note.updated_at.desc(), Note.id.desc())
        .offset(MAX_ARCHIVED_PASS_NOTES_PER_ACCOUNT)
    )
    db.execute(delete(Note).where(Note.id.in_(stale_archived_ids)))
    if not initial_import:
        for note in inserted:
            record_event(
                db,
                account_id=account.id,
                kind="note:new",
                actor=actor,
                payload={
                    "note_id": note.id,
                    "ue_code": note.ue_code,
                    "label": note.raw_label,
                    "score": note.raw_score,
                },
            )
    record_event(
        db,
        account_id=account.id,
        kind="sync:completed",
        actor=actor,
        payload={
            "total": len(active_keys),
            "inserted": len(inserted),
            "updated": changed,
            "archived": archived_count,
        },
    )
    account.last_sync_at = utcnow()
    account.last_sync_status = "success"
    account.last_sync_error = None
    has_changes = bool(inserted or changed or archived_count)
    if has_changes:
        account.last_note_change_at = utcnow()
    return {
        "total": len(active_keys),
        "inserted": inserted,
        "updated": changed,
        "archived": archived_count,
        "changed": has_changes,
    }


def apply_pass_profile(account: Account, profile: PassProfile | None) -> None:
    if profile is None:
        return
    now = utcnow()
    if profile.campus:
        apply_detected_campus(
            account,
            normalize_detected_campus(profile.campus),
            detected_at=now,
        )
    apply_detected_academic_profile(
        account,
        program=profile.program,
        promotion_year=profile.promotion_year,
        detected_at=now,
    )
    apply_official_identity(
        account,
        first_name=profile.first_name,
        last_name=profile.last_name,
        detected_at=now,
    )
    account.profile_refreshed_at = now
    account.profile_refresh_requested_at = None


def _notify_new_notes(db: Session, account: Account, inserted: list[Note]) -> None:
    if not inserted or not account.telegram_enabled:
        return
    if not account.encrypted_telegram_token or not account.encrypted_telegram_chat_id:
        return
    cipher = cipher_for()
    token = cipher.decrypt(account.encrypted_telegram_token, context=f"telegram-token:{account.id}")
    chat_id = cipher.decrypt(account.encrypted_telegram_chat_id, context=f"telegram-chat:{account.id}")
    all_notes = list(
        db.scalars(
            select(Note).where(
                Note.account_id == account.id,
                Note.archived.is_(False),
                Note.hidden_by_user.is_(False),
            )
        )
    )
    all_settings = list(db.scalars(select(UeSetting).where(UeSetting.account_id == account.id)))
    averages = {item["code"]: item["average"] for item in calculate_ues(all_notes, all_settings)}
    payload = [
        {
            "ue_code": note.ue_code,
            "label": note.raw_label,
            "score": note.raw_score,
            "coefficient": note.raw_coefficient,
            "is_resit": note.raw_is_resit,
        }
        for note in inserted
    ]
    send_telegram(token, chat_id, build_new_notes_message(payload, averages))


def _sync_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, ImtAuthenticationError):
        return "SYNC_AUTHENTICATION_FAILED", "Les identifiants IMT ne sont plus valides."
    if isinstance(exc, ImtFetchError):
        return "SYNC_UPSTREAM_FAILED", "PASS n'a pas pu être synchronisé."
    if isinstance(exc, SyncAlreadyRunning):
        return "SYNC_LOCAL_LOCKED", "Une autre synchronisation est déjà en cours."
    if isinstance(exc, PermissionError):
        return "SYNC_ACCOUNT_DISABLED", "Le compte est désactivé."
    if isinstance(exc, SyncLeaseExpired):
        return "SYNC_WORKER_LOST", "La demande de synchronisation a expiré."
    if isinstance(exc, PassAccessRejected):
        return exc.code, exc.message
    return "SYNC_INTERNAL_ERROR", "La synchronisation n'a pas pu aboutir."


def _record_sync_failure(account_id: str, request_id: str, exc: Exception) -> None:
    error_code, public_message = _sync_error(exc)
    now = utcnow()
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        request = db.get(SyncRequest, request_id)
        if account is None or request is None or request.status not in ACTIVE_SYNC_STATUSES:
            return
        account.last_sync_at = now
        account.last_sync_status = "error"
        account.last_sync_error = public_message
        if request.actor == "automatic":
            defer_automatic_sync(account, now=now)
        finalize_sync_request(
            db,
            account,
            request,
            status="failed",
            error_code=error_code,
            now=now,
        )
        record_event(
            db,
            account_id=account.id,
            kind="sync:error",
            actor=request.actor,
            payload={"code": error_code},
        )
        db.commit()


def _record_sync_deferred(
    account_id: str,
    request_id: str,
    exc: PassAccessRejected,
) -> None:
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        request = db.get(SyncRequest, request_id)
        if account is None or request is None or request.status not in ACTIVE_SYNC_STATUSES:
            return
        account.last_sync_status = "success" if account.last_sync_at else "never"
        account.last_sync_error = None
        if request.actor == "automatic":
            defer_automatic_sync(
                account,
                available_at=exc.available_at,
            )
        finalize_sync_request(
            db,
            account,
            request,
            status="skipped",
            error_code=exc.code,
            result={
                "retry_after_seconds": exc.retry_after_seconds,
                "available_at": exc.available_at.isoformat(),
            },
        )
        record_event(
            db,
            account_id=account.id,
            kind="sync:deferred",
            actor=request.actor,
            payload={"code": exc.code},
        )
        db.commit()


def _skip_automatic_request(
    db: Session,
    account: Account,
    request: SyncRequest,
    *,
    previous_status: str,
    previous_error: str | None,
) -> None:
    account.last_sync_status = previous_status
    account.last_sync_error = previous_error
    finalize_sync_request(
        db,
        account,
        request,
        status="skipped",
        error_code="SYNC_AUTO_NOT_DUE",
    )
    record_event(
        db,
        account_id=account.id,
        kind="sync:automatic_skipped",
        actor="automatic",
        payload={"code": "SYNC_AUTO_NOT_DUE"},
    )
    db.commit()


def execute_sync_request(
    account_id: str,
    request_id: str,
    *,
    notify: bool = True,
    quota_bypass: bool = False,
    bypass_reason: str | None = None,
    force_probe: bool = False,
) -> dict:
    try:
        with account_sync_lock(account_id), SessionLocal() as db:
            account = db.get(Account, account_id)
            request = db.get(SyncRequest, request_id)
            if account is None or request is None:
                raise LookupError("Demande de synchronisation introuvable")
            if request.status not in ACTIVE_SYNC_STATUSES:
                return request.result or {"skipped": True, "status": request.status}
            now = utcnow()
            if (
                account.sync_active_request_id != request.id
                or ensure_utc(request.lease_expires_at) <= now
            ):
                raise SyncLeaseExpired("Le lease de synchronisation a expiré")
            if account.is_disabled:
                raise PermissionError("Compte désactivé")
            previous_status = account.last_sync_status
            previous_error = account.last_sync_error
            if request.actor == "automatic" and not auto_sync_is_due(account, now):
                _skip_automatic_request(
                    db,
                    account,
                    request,
                    previous_status=previous_status,
                    previous_error=previous_error,
                )
                raise AutomaticSyncNotAllowed(
                    "Actualisation automatique non autorisée ou non échue"
                )

            lease_expires_at = now + timedelta(seconds=SYNC_LEASE_SECONDS)
            request.status = "running"
            request.started_at = request.started_at or now
            request.lease_expires_at = lease_expires_at
            account.sync_active_until = lease_expires_at
            account.last_sync_status = "running"
            account.last_sync_error = None
            record_event(db, account_id=account.id, kind="sync:started", actor=request.actor)
            db.commit()

            db.refresh(account)
            db.refresh(request)
            if request.actor == "automatic" and not auto_sync_is_due(account, utcnow()):
                _skip_automatic_request(
                    db,
                    account,
                    request,
                    previous_status=previous_status,
                    previous_error=previous_error,
                )
                raise AutomaticSyncNotAllowed(
                    "Actualisation automatique non autorisée ou non échue"
                )

            cipher = cipher_for()
            password = cipher.decrypt(
                account.encrypted_imt_password,
                context=f"imt-password:{account.id}",
            )
            gateway = perform_sync_operation(
                account=account,
                password=password,
                actor=request.actor,
                quota_bypass=quota_bypass,
                bypass_reason=bypass_reason,
                force_probe=force_probe,
            )
            result = apply_pass_entries(db, account, gateway.entries, actor=request.actor)
            apply_pass_profile(account, gateway.profile)
            update_adaptive_cadence(
                account,
                changed=result["changed"],
                actor=request.actor,
            )
            if result["changed"]:
                emit_cohort_pulse(db, account)
            db.commit()
            if notify:
                try:
                    _notify_new_notes(db, account, result["inserted"])
                except TelegramError as exc:
                    logger.warning(
                        "Telegram notification failed sync_ref=%s error_type=%s",
                        sync_log_reference(account.id),
                        type(exc).__name__,
                    )
                    record_event(
                        db,
                        account_id=account.id,
                        kind="telegram:error",
                        payload={"code": "TELEGRAM_DELIVERY_FAILED"},
                    )
                    db.commit()
            response = {
                "total": result["total"],
                "inserted": len(result["inserted"]),
                "updated": result["updated"],
                "archived": result["archived"],
            }
            finalize_sync_request(
                db,
                account,
                request,
                status="succeeded",
                result=response,
            )
            db.commit()
            return response
    except AutomaticSyncNotAllowed:
        raise
    except PassAccessRejected as exc:
        _record_sync_deferred(account_id, request_id, exc)
        raise
    except Exception as exc:
        _record_sync_failure(account_id, request_id, exc)
        raise


def run_sync_background(
    account_id: str,
    request_id: str,
    *,
    notify: bool = True,
    quota_bypass: bool = False,
    bypass_reason: str | None = None,
    force_probe: bool = False,
) -> None:
    try:
        execute_sync_request(
            account_id,
            request_id,
            notify=notify,
            quota_bypass=quota_bypass,
            bypass_reason=bypass_reason,
            force_probe=force_probe,
        )
    except AutomaticSyncNotAllowed:
        return
    except Exception as exc:
        logger.error(
            "Background sync failed sync_ref=%s error_type=%s",
            sync_log_reference(account_id),
            type(exc).__name__,
        )


def sync_account(
    account_id: str,
    *,
    notify: bool = True,
    actor: str = "system",
    quota_bypass: bool = False,
    bypass_reason: str | None = None,
    force_probe: bool = False,
) -> dict:
    now = utcnow()
    if actor == "automatic":
        with SessionLocal() as db:
            account = db.get(Account, account_id)
            if account is None:
                raise LookupError("Compte introuvable")
            if not auto_sync_is_due(account, now):
                raise AutomaticSyncNotAllowed(
                    "Actualisation automatique non autorisée ou non échue"
                )
    reservation = reserve_sync_request(
        account_id,
        actor=actor,
        idempotency_key=server_idempotency_key(actor),
        enforce_cooldown=False,
        now=now,
    )
    return execute_sync_request(
        account_id,
        reservation.request_id,
        notify=notify,
        quota_bypass=quota_bypass,
        bypass_reason=bypass_reason,
        force_probe=force_probe,
    )


def sync_all_accounts() -> list[dict]:
    with SessionLocal() as db:
        account_ids = list(db.scalars(select(Account.id).order_by(Account.created_at)))
    results: list[dict] = []
    for account_id in account_ids:
        try:
            results.append({"account_id": account_id, "ok": True, **sync_account(account_id)})
        except Exception as exc:  # CLI must continue syncing other tenants
            error_code, public_message = _sync_error(exc)
            logger.error(
                "Account sync failed sync_ref=%s error_type=%s",
                sync_log_reference(account_id),
                type(exc).__name__,
            )
            results.append(
                {
                    "account_id": account_id,
                    "ok": False,
                    "error_code": error_code,
                    "error": public_message,
                }
            )
    return results


def sync_due_accounts() -> list[dict]:
    now = utcnow()
    with SessionLocal() as db:
        accounts = list(
            db.scalars(
                select(Account).where(
                    Account.auto_sync_enabled.is_(True),
                    Account.auto_sync_consented_at.is_not(None),
                    Account.is_disabled.is_(False),
                )
            )
        )
        due_accounts = [account for account in accounts if auto_sync_is_due(account, now)]
        due_accounts.sort(
            key=lambda account: (
                -automatic_lateness_ratio(account, now),
                account.created_at,
                account.id,
            )
        )
        from app.models import PassSystemState

        state = db.get(PassSystemState, 1)
        if (
            len(due_accounts) > 1
            and state is not None
            and due_accounts[0].id == state.last_auto_account_id
        ):
            due_accounts.append(due_accounts.pop(0))
        account_ids = [account.id for account in due_accounts[:1]]
    results: list[dict] = []
    for account_id in account_ids:
        try:
            results.append(
                {
                    "account_id": account_id,
                    "ok": True,
                    **sync_account(account_id, actor="automatic"),
                }
            )
        except AutomaticSyncNotAllowed:
            results.append({"account_id": account_id, "ok": True, "skipped": True})
        except SyncInProgress:
            results.append(
                {
                    "account_id": account_id,
                    "ok": True,
                    "skipped": True,
                    "reason": "in_progress",
                }
            )
        except PassAccessRejected as exc:
            results.append(
                {
                    "account_id": account_id,
                    "ok": True,
                    "skipped": True,
                    "reason": exc.code,
                    "retry_after_seconds": exc.retry_after_seconds,
                }
            )
        except Exception as exc:  # Worker must continue syncing other consenting accounts
            error_code, public_message = _sync_error(exc)
            logger.error(
                "Scheduled sync failed sync_ref=%s error_type=%s",
                sync_log_reference(account_id),
                type(exc).__name__,
            )
            results.append(
                {
                    "account_id": account_id,
                    "ok": False,
                    "error_code": error_code,
                    "error": public_message,
                }
            )
        finally:
            with SessionLocal() as db:
                from app.models import PassSystemState

                state = db.get(PassSystemState, 1)
                if state is None:
                    state = PassSystemState(id=1)
                    db.add(state)
                state.last_auto_account_id = account_id
                db.commit()
    return results
