from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
CLIPBOARD_SOURCE = ROOT / "tools" / "clipboard_copy.swift"
CLIPBOARD_BINARY = ROOT / "data" / "bin" / "clipboard-copy"
STATIC_IMAGE_SUFFIXES = {
    ".bmp",
    ".heic",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}


class ClipboardError(RuntimeError):
    pass


def ensure_clipboard_binary() -> Path:
    if platform.system() != "Darwin":
        raise ClipboardError(
            "Native copy is currently available on the Mac dashboard."
        )
    if (
        CLIPBOARD_BINARY.exists()
        and CLIPBOARD_BINARY.stat().st_mtime
        >= CLIPBOARD_SOURCE.stat().st_mtime
    ):
        return CLIPBOARD_BINARY

    CLIPBOARD_BINARY.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "/usr/bin/swiftc",
                "-O",
                str(CLIPBOARD_SOURCE),
                "-o",
                str(CLIPBOARD_BINARY),
            ],
            check=True,
            capture_output=True,
            timeout=90,
        )
    except (OSError, subprocess.SubprocessError) as error:
        detail = getattr(error, "stderr", b"")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise ClipboardError(
            (detail or "The native clipboard helper could not be built.").strip()
        ) from error
    return CLIPBOARD_BINARY


def _run_clipboard(
    mode: str,
    paths: Iterable[Path] = (),
    *,
    text: str | None = None,
) -> None:
    binary = ensure_clipboard_binary()
    command = [str(binary), mode, *(str(path) for path in paths)]
    try:
        subprocess.run(
            command,
            input=text.encode("utf-8") if text is not None else None,
            check=True,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        detail = getattr(error, "stderr", b"")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise ClipboardError(
            (detail or "The item could not be copied.").strip()
        ) from error


def copy_text(value: str) -> None:
    if not value.strip():
        raise ClipboardError("There is no text to copy.")
    _run_clipboard("text", text=value)


def copy_media(paths: Iterable[str | Path]) -> int:
    unique_paths: list[Path] = []
    seen: set[Path] = set()
    for value in paths:
        path = Path(value).expanduser().resolve()
        if path in seen:
            continue
        if not path.is_file():
            raise ClipboardError(f"{path.name or 'Media'} is unavailable.")
        unique_paths.append(path)
        seen.add(path)

    if not unique_paths:
        raise ClipboardError("There is no media to copy.")

    mode = (
        "image"
        if len(unique_paths) == 1
        and unique_paths[0].suffix.lower() in STATIC_IMAGE_SUFFIXES
        else "files"
    )
    _run_clipboard(mode, unique_paths)
    return len(unique_paths)
