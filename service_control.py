from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from tgbrain import db, get_runtime_state, set_runtime_state, Settings


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "data" / "logs"
WATCHER_LOG = LOG_DIR / "watcher.log"
SYNC_LOG = LOG_DIR / "sync.log"
INDEXER_LOG = LOG_DIR / "content_indexer.log"


def process_alive(pid: int | str | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def watcher_snapshot(config: Settings) -> dict:
    connection = db(config.db_path)
    status, status_at = get_runtime_state(connection, "watcher_status")
    pid, _ = get_runtime_state(connection, "watcher_pid")
    error, error_at = get_runtime_state(connection, "watcher_error")
    last_count, last_count_at = get_runtime_state(
        connection,
        "last_sync_count",
    )
    connection.close()

    alive = process_alive(pid)
    if not alive and status in {"online", "connecting"}:
        status = "offline"
    return {
        "status": status or "offline",
        "status_at": status_at,
        "pid": int(pid) if pid and pid.isdigit() else None,
        "alive": alive,
        "error": error,
        "error_at": error_at,
        "last_count": int(last_count) if last_count else 0,
        "last_count_at": last_count_at,
    }


def indexer_snapshot(config: Settings) -> dict:
    connection = db(config.db_path)
    status, status_at = get_runtime_state(connection, "indexer_status")
    pid, _ = get_runtime_state(connection, "indexer_pid")
    error, error_at = get_runtime_state(connection, "indexer_error")
    current, _ = get_runtime_state(connection, "indexer_current")
    pending, _ = get_runtime_state(connection, "indexer_pending")
    ready, _ = get_runtime_state(connection, "indexer_ready")
    partial, _ = get_runtime_state(connection, "indexer_partial")
    failed, _ = get_runtime_state(connection, "indexer_failed")
    connection.close()

    alive = process_alive(pid)
    if not alive and status in {"online", "starting"}:
        status = "offline"
    return {
        "status": status or "offline",
        "status_at": status_at,
        "pid": int(pid) if pid and pid.isdigit() else None,
        "alive": alive,
        "error": error,
        "error_at": error_at,
        "current": current,
        "pending": int(pending) if pending else 0,
        "ready": int(ready) if ready else 0,
        "partial": int(partial) if partial else 0,
        "failed": int(failed) if failed else 0,
    }


def _spawn(script: str, log_path: Path, *args: str) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / script), *args],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    log.close()
    return process.pid


def start_watcher(config: Settings) -> tuple[bool, int | None]:
    snapshot = watcher_snapshot(config)
    if snapshot["alive"]:
        return False, snapshot["pid"]

    connection = db(config.db_path)
    set_runtime_state(connection, "watcher_status", "starting")
    set_runtime_state(connection, "watcher_error", "")
    connection.close()

    pid = _spawn("watch.py", WATCHER_LOG)
    connection = db(config.db_path)
    set_runtime_state(connection, "watcher_pid", pid)
    connection.close()
    return True, pid


def stop_watcher(config: Settings) -> bool:
    snapshot = watcher_snapshot(config)
    if not snapshot["alive"] or not snapshot["pid"]:
        return False
    os.kill(snapshot["pid"], signal.SIGTERM)
    connection = db(config.db_path)
    set_runtime_state(connection, "watcher_status", "offline")
    connection.close()
    return True


def start_content_indexer(
    config: Settings,
) -> tuple[bool, int | None]:
    snapshot = indexer_snapshot(config)
    if snapshot["alive"]:
        return False, snapshot["pid"]
    connection = db(config.db_path)
    set_runtime_state(connection, "indexer_status", "starting")
    set_runtime_state(connection, "indexer_error", "")
    connection.close()
    pid = _spawn("content_worker.py", INDEXER_LOG)
    connection = db(config.db_path)
    set_runtime_state(connection, "indexer_pid", pid)
    connection.close()
    return True, pid


def stop_content_indexer(config: Settings) -> bool:
    snapshot = indexer_snapshot(config)
    if not snapshot["alive"] or not snapshot["pid"]:
        return False
    os.kill(snapshot["pid"], signal.SIGTERM)
    connection = db(config.db_path)
    set_runtime_state(connection, "indexer_status", "offline")
    connection.close()
    return True


def start_incremental_sync(config: Settings) -> tuple[bool, int | None]:
    snapshot = watcher_snapshot(config)
    if snapshot["alive"]:
        return False, snapshot["pid"]
    connection = db(config.db_path)
    status, _ = get_runtime_state(connection, "sync_status")
    sync_pid, _ = get_runtime_state(connection, "sync_pid")
    if status in {"starting", "connecting", "syncing"} and process_alive(
        sync_pid
    ):
        connection.close()
        return False, int(sync_pid)

    set_runtime_state(connection, "sync_status", "starting")
    connection.close()
    pid = _spawn("sync_history.py", SYNC_LOG, "--incremental")
    connection = db(config.db_path)
    set_runtime_state(connection, "sync_pid", pid)
    connection.close()
    return True, pid
