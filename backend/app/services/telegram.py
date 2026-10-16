from __future__ import annotations

import html
import re
import time

import requests

from app.calculations import grade_for_average

TELEGRAM_MESSAGE_LIMIT = 3900
MAX_TELEGRAM_CHUNKS = 8
MAX_TELEGRAM_RESPONSE_BYTES = 256_000
MAX_NOTIFICATION_NOTES = 64
TELEGRAM_TOTAL_TIMEOUT_SECONDS = 45


class TelegramError(RuntimeError):
    pass


def split_message(
    message: str,
    limit: int = TELEGRAM_MESSAGE_LIMIT,
    max_chunks: int = MAX_TELEGRAM_CHUNKS,
) -> list[str]:
    if limit < 1 or max_chunks < 1:
        raise ValueError("Les limites Telegram doivent être positives")

    chunks: list[str] = []
    current = ""
    for source_line in message.splitlines() or [message]:
        line = source_line
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current or not chunks:
        chunks.append(current or message[:limit])

    if len(chunks) > max_chunks:
        marker = "\n[Notification tronquée]"
        chunks = chunks[:max_chunks]
        chunks[-1] = chunks[-1][: max(0, limit - len(marker))] + marker
    return chunks


def plain_text(message: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", message))


def _post_telegram(
    session: requests.Session,
    url: str,
    payload: dict[str, object],
    deadline: float,
) -> requests.Response:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TelegramError("Le délai global de notification Telegram est dépassé")
    try:
        response = session.post(
            url,
            data=payload,
            timeout=(max(0.1, min(5.0, remaining)), max(0.1, min(20.0, remaining))),
            allow_redirects=False,
            stream=True,
        )
        if 300 <= response.status_code < 400:
            response.close()
            raise TelegramError("Une redirection inattendue de Telegram a été refusée")

        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > MAX_TELEGRAM_RESPONSE_BYTES:
                    response.close()
                    raise TelegramError("La réponse Telegram dépasse la taille autorisée")
            except ValueError:
                pass

        content = bytearray()
        for chunk in response.iter_content(chunk_size=32 * 1024):
            if deadline - time.monotonic() <= 0:
                response.close()
                raise TelegramError("Le délai global de notification Telegram est dépassé")
            if not chunk:
                continue
            if len(content) + len(chunk) > MAX_TELEGRAM_RESPONSE_BYTES:
                response.close()
                raise TelegramError("La réponse Telegram dépasse la taille autorisée")
            content.extend(chunk)
        if deadline - time.monotonic() <= 0:
            response.close()
            raise TelegramError("Le délai global de notification Telegram est dépassé")
        response._content = bytes(content)
        response._content_consumed = True
        response.close()
        return response
    except TelegramError:
        raise
    except requests.RequestException as exc:
        raise TelegramError("Telegram n'a pas accepté la notification") from exc


def send_telegram(bot_token: str, chat_id: str, message: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    deadline = time.monotonic() + TELEGRAM_TOTAL_TIMEOUT_SECONDS
    with requests.Session() as session:
        session.trust_env = False
        for chunk in split_message(message):
            payload: dict[str, object] = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            response = _post_telegram(session, url, payload, deadline)
            if response.ok:
                continue
            fallback = _post_telegram(
                session,
                url,
                {
                    "chat_id": chat_id,
                    "text": plain_text(chunk),
                    "disable_web_page_preview": True,
                },
                deadline,
            )
            try:
                fallback.raise_for_status()
            except requests.RequestException as exc:
                raise TelegramError("Telegram n'a pas accepté la notification") from exc


def build_new_notes_message(new_notes: list[dict], ue_averages: dict[str, float]) -> str:
    limited_notes = new_notes[:MAX_NOTIFICATION_NOTES]
    omitted_count = max(0, len(new_notes) - len(limited_notes))
    lines = [
        "🔔 <b>NOUVELLES NOTES DÉTECTÉES</b>",
        "<i>Synchronisation IMTégrale depuis PASS</i>",
        "",
    ]
    grouped: dict[str, list[dict]] = {}
    for note in limited_notes:
        code = str(note["ue_code"])[:32]
        grouped.setdefault(code, []).append(note)

    for code, notes in sorted(grouped.items()):
        lines.append(f"📂 <b>{html.escape(code)}</b>")
        for note in notes:
            marker = "🔁" if note["is_resit"] else "•"
            label = html.escape(str(note["label"])[:240])
            lines.append(
                f"   {marker} {label} : <code>{note['score']:g}</code> <i>(c.{note['coefficient']:g})</i>"
            )
        average = ue_averages.get(code)
        if average is not None:
            grade = grade_for_average(average, any(note["is_resit"] for note in notes))
            grade_label = f" · grade {grade.grade}" if grade else ""
            lines.append(f"   Moyenne UE : <b>{average:g} / 20</b>{grade_label}")
        lines.append("")
    if omitted_count:
        lines.append(f"… et {omitted_count} autre(s) note(s), visibles dans IMTégrale.")
    return "\n".join(lines).rstrip()
