from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from tgbrain import db, get_runtime_state, set_runtime_state, Settings


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "data" / "logs"
WATCHER_LOG = LOG_DIR / "watcher.log"
SYNC_LOG = LOG_DIR / "sync.log"
INDEXER_LOG = LOG_DIR / "content_indexer.log"
OLLAMA_LOG = LOG_DIR / "ollama.log"


def process_alive(pid: int | str | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    try:
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(int(pid))],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        ).stdout.strip()
        if state.startswith("Z"):
            return False
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        pass
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


def _ollama_ready(config: Settings) -> bool:
    try:
        with urlopen(
            f"{config.ollama_url}/api/version",
            timeout=0.8,
        ) as response:
            return response.status == 200
    except Exception:
        return False


def ensure_local_ollama(config: Settings) -> bool:
    if not (config.enable_vision or config.enable_embeddings):
        return True
    parsed = urlparse(config.ollama_url)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return True
    if _ollama_ready(config):
        return True

    configured = os.getenv("OLLAMA_COMMAND", "").strip()
    command = (
        configured
        or shutil.which("ollama")
        or "/Applications/Ollama.app/Contents/Resources/ollama"
    )
    if not Path(command).exists():
        return False

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = OLLAMA_LOG.open("a", encoding="utf-8")
    subprocess.Popen(
        [command, "serve"],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    log.close()
    for _ in range(20):
        if _ollama_ready(config):
            return True
        time.sleep(0.5)
    return False


def reconcile_services(config: Settings) -> None:
    connection = db(config.db_path)
    watcher_desired, _ = get_runtime_state(
        connection,
        "watcher_desired",
    )
    indexer_desired, _ = get_runtime_state(
        connection,
        "indexer_desired",
    )
    connection.close()

    auto_watcher = os.getenv("AUTO_START_WATCHER", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if auto_watcher and watcher_desired != "paused":
        if not watcher_snapshot(config)["alive"]:
            start_watcher(config)
    if (
        config.enable_content_indexing
        and indexer_desired != "paused"
        and not indexer_snapshot(config)["alive"]
    ):
        start_content_indexer(config)


def start_watcher(config: Settings) -> tuple[bool, int | None]:
    snapshot = watcher_snapshot(config)
    if snapshot["alive"]:
        return False, snapshot["pid"]

    connection = db(config.db_path)
    set_runtime_state(connection, "watcher_desired", "online")
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
    connection = db(config.db_path)
    set_runtime_state(connection, "watcher_desired", "paused")
    connection.close()
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
    ensure_local_ollama(config)
    connection = db(config.db_path)
    set_runtime_state(connection, "indexer_desired", "online")
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
    connection = db(config.db_path)
    set_runtime_state(connection, "indexer_desired", "paused")
    connection.close()
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
