from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import signal
import sqlite3
import time

from content_indexing import (
    apply_enrichment,
    enrich_record,
    next_pending,
    pending_count,
)
from tgbrain import (
    ENRICHMENT_VERSION,
    db,
    rebuild_captures,
    rebuild_search_index,
    set_runtime_state,
    settings,
)


running = True
DATABASE_RETRIES = 8


def stop_worker(_signum, _frame) -> None:
    global running
    running = False


def update_counts(connection, config) -> None:
    counts = {
        row["content_status"]: row["n"]
        for row in connection.execute(
            """
            SELECT content_status, COUNT(*) AS n
            FROM messages
            WHERE duplicate_of_id IS NULL
            GROUP BY content_status
            """
        ).fetchall()
    }
    set_runtime_state(
        connection,
        "indexer_pending",
        pending_count(
            connection,
            require_vision=config.enable_vision,
        ),
    )
    set_runtime_state(
        connection,
        "indexer_ready",
        counts.get("ready", 0),
    )
    set_runtime_state(
        connection,
        "indexer_partial",
        counts.get("partial", 0),
    )
    set_runtime_state(
        connection,
        "indexer_failed",
        counts.get("failed", 0),
    )


def database_retry(connection, operation):
    for attempt in range(DATABASE_RETRIES):
        try:
            return operation()
        except sqlite3.OperationalError as error:
            connection.rollback()
            if (
                "locked" not in str(error).lower()
                or attempt == DATABASE_RETRIES - 1
            ):
                raise
            time.sleep(0.5 * (attempt + 1))


def process_batch(
    connection,
    config,
    batch_size: int,
    workers: int,
) -> int:
    rows = database_retry(
        connection,
        lambda: next_pending(
            connection,
            batch_size,
            require_vision=config.enable_vision,
        ),
    )
    if rows:
        set_runtime_state(
            connection,
            "indexer_current",
            f"{len(rows)} captures in this batch",
        )
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(enrich_record, config, row): row
            for row in rows
        }
        for future in as_completed(futures):
            row = futures[future]
            try:
                result = future.result()
                database_retry(
                    connection,
                    lambda: apply_enrichment(connection, result),
                )
            except Exception as error:
                def mark_failed() -> None:
                    connection.execute(
                        """
                        UPDATE messages
                        SET content_status = 'failed', content_error = ?,
                            enrichment_version = ?, enriched_at = ?
                        WHERE id = ?
                        """,
                        (
                            str(error)[:2000],
                            ENRICHMENT_VERSION,
                            time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ",
                                time.gmtime(),
                            ),
                            row["id"],
                        ),
                    )
                    connection.commit()

                database_retry(connection, mark_failed)
    if rows:
        database_retry(
            connection,
            lambda: rebuild_captures(connection),
        )
        if pending_count(
            connection,
            require_vision=config.enable_vision,
        ) == 0:
            database_retry(
                connection,
                lambda: rebuild_search_index(connection),
            )
    database_retry(
        connection,
        lambda: update_counts(connection, config),
    )
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Index saved Telegram content in the background."
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--batch-size", type=int, default=6)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, stop_worker)
    signal.signal(signal.SIGINT, stop_worker)

    config = settings()
    connection = db(config.db_path)
    set_runtime_state(connection, "indexer_pid", os.getpid())
    set_runtime_state(connection, "indexer_status", "online")
    set_runtime_state(connection, "indexer_error", "")
    try:
        while running:
            try:
                processed = process_batch(
                    connection,
                    config,
                    max(1, args.batch_size),
                    max(1, args.workers),
                )
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).lower():
                    raise
                connection.rollback()
                time.sleep(2)
                continue
            if args.once:
                while processed and running:
                    processed = process_batch(
                        connection,
                        config,
                        max(1, args.batch_size),
                        max(1, args.workers),
                    )
                break
            if not processed:
                set_runtime_state(connection, "indexer_current", "")
                time.sleep(config.content_poll_seconds)
    except Exception as error:
        try:
            set_runtime_state(connection, "indexer_status", "error")
            set_runtime_state(connection, "indexer_error", str(error))
        except sqlite3.Error:
            pass
        raise
    finally:
        try:
            database_retry(
                connection,
                lambda: update_counts(connection, config),
            )
            set_runtime_state(connection, "indexer_current", "")
            set_runtime_state(connection, "indexer_status", "offline")
        except sqlite3.Error:
            pass
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
