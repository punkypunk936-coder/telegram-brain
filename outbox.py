from __future__ import annotations

import json
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

from tgbrain import Settings, ingest


MAX_FILES_PER_SEND = 10
MAX_CAPTION_LENGTH = 1024
MAX_MESSAGE_LENGTH = 4096
MAX_RETRIES = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(value: str, fallback: str) -> str:
    name = Path(value or "").name.strip()
    name = re.sub(r"[^A-Za-z0-9._() -]+", "_", name)
    return name[:180] or fallback


def _split_text(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    remaining = str(text or "")
    parts: list[str] = []
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at < limit // 2:
            split_at = limit
        part = remaining[:split_at]
        remaining = remaining[split_at:]
        if remaining.startswith("\n"):
            remaining = remaining[1:]
        if part:
            parts.append(part)
    if remaining:
        parts.append(remaining)
    return parts


def enqueue_outbound(
    connection: sqlite3.Connection,
    config: Settings,
    *,
    text: str = "",
    attachments: Sequence[tuple[str, str | None, bytes]] = (),
    client_token: str | None = None,
) -> int:
    body = str(text or "")
    files = list(attachments or ())
    if not body.strip() and not files:
        raise ValueError("Add text or at least one file before sending.")
    if config.chat_id is None:
        raise ValueError("Telegram Brain does not have a destination chat.")

    token = client_token or uuid.uuid4().hex
    upload_dir = config.db_path.parent / "outbox" / token
    staged: list[dict] = []
    try:
        for index, (original_name, mime_type, payload) in enumerate(files, 1):
            if not payload:
                continue
            name = _safe_name(original_name, f"attachment-{index}")
            upload_dir.mkdir(parents=True, exist_ok=True)
            path = upload_dir / name
            if path.exists():
                path = upload_dir / f"{path.stem}-{index}{path.suffix}"
            path.write_bytes(payload)
            staged.append(
                {
                    "name": name,
                    "mime_type": str(mime_type or ""),
                    "path": str(path.resolve()),
                    "size": len(payload),
                }
            )
        if not body.strip() and not staged:
            raise ValueError("The selected files were empty.")

        now = _now()
        cursor = connection.execute(
            """
            INSERT INTO outbound_queue(
                destination_chat_id, text, files_json, status, attempts,
                error, telegram_message_ids_json, client_token,
                next_attempt_at, created_at, updated_at
            ) VALUES(?, ?, ?, 'queued', 0, '', '[]', ?, NULL, ?, ?)
            """,
            (
                int(config.chat_id),
                body,
                json.dumps(staged),
                token,
                now,
                now,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)
    except Exception:
        if upload_dir.exists():
            shutil.rmtree(upload_dir, ignore_errors=True)
        raise


def recent_outbound(
    connection: sqlite3.Connection,
    limit: int = 5,
) -> list[dict]:
    rows = connection.execute(
        """
        SELECT *
        FROM outbound_queue
        ORDER BY id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]


def outbox_counts(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM outbound_queue
        GROUP BY status
        """
    ).fetchall()
    counts = {str(row["status"]): int(row["count"]) for row in rows}
    return {
        "queued": counts.get("queued", 0),
        "sending": counts.get("sending", 0),
        "sent": counts.get("sent", 0),
        "failed": counts.get("failed", 0),
    }


def retry_outbound(connection: sqlite3.Connection, row_id: int) -> bool:
    cursor = connection.execute(
        """
        UPDATE outbound_queue
        SET status = 'queued', attempts = 0, error = '',
            next_attempt_at = NULL, updated_at = ?
        WHERE id = ? AND status = 'failed'
        """,
        (_now(), int(row_id)),
    )
    connection.commit()
    return bool(cursor.rowcount)


def recover_interrupted_outbox(connection: sqlite3.Connection) -> int:
    cursor = connection.execute(
        """
        UPDATE outbound_queue
        SET status = 'queued', error = 'Delivery resumed after restart.',
            next_attempt_at = NULL, updated_at = ?
        WHERE status = 'sending'
        """,
        (_now(),),
    )
    connection.commit()
    return int(cursor.rowcount)


def claim_next_outbound(connection: sqlite3.Connection) -> dict | None:
    now = _now()
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            """
            SELECT *
            FROM outbound_queue
            WHERE status = 'queued'
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
            ORDER BY id
            LIMIT 1
            """,
            (now,),
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        connection.execute(
            """
            UPDATE outbound_queue
            SET status = 'sending', updated_at = ?
            WHERE id = ?
            """,
            (now, int(row["id"])),
        )
        connection.commit()
        claimed = dict(row)
        claimed["status"] = "sending"
        return claimed
    except Exception:
        connection.rollback()
        raise


def _message_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


async def _send_text(telegram, entity, text: str) -> list:
    sent = []
    for part in _split_text(text):
        sent.extend(
            _message_list(
                await telegram.send_message(
                    entity,
                    part,
                    parse_mode=None,
                    link_preview=True,
                )
            )
        )
    return sent


async def _send_files(
    telegram,
    entity,
    paths: Sequence[str],
    caption: str = "",
) -> list:
    sent = []
    for start in range(0, len(paths), MAX_FILES_PER_SEND):
        batch = list(paths[start : start + MAX_FILES_PER_SEND])
        batch_caption = caption if start == 0 else ""
        sent.extend(
            _message_list(
                await telegram.send_file(
                    entity,
                    batch[0] if len(batch) == 1 else batch,
                    caption=batch_caption or None,
                    parse_mode=None,
                    supports_streaming=True,
                )
            )
        )
    return sent


def _mark_sent(
    connection: sqlite3.Connection,
    row_id: int,
    messages: Iterable,
) -> None:
    message_ids = [
        int(message.id)
        for message in messages
        if getattr(message, "id", None) is not None
    ]
    now = _now()
    connection.execute(
        """
        UPDATE outbound_queue
        SET status = 'sent', error = '', telegram_message_ids_json = ?,
            sent_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (json.dumps(message_ids), now, now, int(row_id)),
    )
    connection.commit()


def _mark_failed(
    connection: sqlite3.Connection,
    row: dict,
    error: Exception,
) -> None:
    attempts = int(row.get("attempts") or 0) + 1
    terminal = attempts >= MAX_RETRIES
    delay = min(60, 2 ** attempts)
    next_attempt = (
        None
        if terminal
        else (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
    )
    connection.execute(
        """
        UPDATE outbound_queue
        SET status = ?, attempts = ?, error = ?, next_attempt_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            "failed" if terminal else "queued",
            attempts,
            str(error)[:800],
            next_attempt,
            _now(),
            int(row["id"]),
        ),
    )
    connection.commit()


def _cleanup_files(files: Sequence[dict]) -> None:
    parents: set[Path] = set()
    for item in files:
        path = Path(str(item.get("path") or ""))
        if path.exists():
            path.unlink(missing_ok=True)
        if path.parent.name:
            parents.add(path.parent)
    for parent in parents:
        try:
            parent.rmdir()
        except OSError:
            pass


async def deliver_outbound(
    telegram,
    config: Settings,
    connection: sqlite3.Connection,
    entity,
    row: dict,
) -> bool:
    files: list[dict] = []
    try:
        decoded_files = json.loads(row.get("files_json") or "[]")
        if not isinstance(decoded_files, list):
            raise ValueError("Queued attachments are malformed.")
        files = decoded_files
        paths = [str(item.get("path") or "") for item in files]
        missing = [
            path for path in paths if not path or not Path(path).is_file()
        ]
        if missing:
            raise FileNotFoundError("A queued file is unavailable.")

        text = str(row.get("text") or "")
        sent: list = []
        if paths and len(text) <= MAX_CAPTION_LENGTH:
            sent.extend(await _send_files(telegram, entity, paths, text))
        else:
            if text:
                sent.extend(await _send_text(telegram, entity, text))
            if paths:
                sent.extend(await _send_files(telegram, entity, paths))

        # Record Telegram's acknowledgement before local enrichment so a
        # downstream indexing issue can never duplicate an already-sent item.
        _mark_sent(connection, int(row["id"]), sent)
        for message in sent:
            try:
                await ingest(telegram, config, connection, message)
            except Exception:
                # The event listener and catch-up pass provide two more paths
                # to ingest the confirmed Telegram message.
                pass
        _cleanup_files(files)
        return True
    except Exception as error:
        _mark_failed(connection, row, error)
        return False
