from __future__ import annotations

import base64
from difflib import SequenceMatcher
import io
import json
import math
import mimetypes
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests
from dotenv import load_dotenv
from mnemonic import Mnemonic
from PIL import Image, ImageOps
from telethon import TelegramClient
from telethon.tl.types import InputMessagesFilterPinned

from media_dedupe import (
    find_duplicate,
    fingerprint_media,
)


load_dotenv()

URL_RE = re.compile(r"https?://[^\s<>\]\)]+", re.IGNORECASE)
METADATA_VERSION = 9
ENRICHMENT_VERSION = 4
CAPTURE_VERSION = 1
VISION_PROMPT_VERSION = 2
SEMANTIC_MIN_SCORE = 0.40
_INITIALIZED_DATABASES: set[str] = set()
MNEMONIC = Mnemonic("english")
SEARCH_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}
PERSON_QUERY_GENERIC = {
    "asian",
    "black",
    "boy",
    "chinese",
    "find",
    "girl",
    "government",
    "image",
    "jacket",
    "leader",
    "leather",
    "man",
    "meme",
    "person",
    "photo",
    "picture",
    "politician",
    "president",
    "red",
    "show",
    "standing",
    "suit",
    "video",
    "woman",
}

SECRET_PATTERNS = (
    (
        "API key",
        re.compile(r"\b(?:sk|key)-[A-Za-z0-9_-]{20,}\b", re.IGNORECASE),
    ),
    (
        "Telegram bot token",
        re.compile(r"\b\d{7,12}:[A-Za-z0-9_-]{30,}\b"),
    ),
    (
        "AWS access key",
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    ),
    (
        "private key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "bearer token",
        re.compile(
            r"\b[A-Za-z0-9_-]{20,}(?:%2B|%2F|%3D)[A-Za-z0-9%_-]{20,}\b",
            re.IGNORECASE,
        ),
    ),
)

SECRET_LABEL_RE = re.compile(
    r"\b(?:api[\s_-]*key|bearer[\s_-]*token|bot[\s_-]*token|"
    r"client[\s_-]*secret|access[\s_-]*token|private[\s_-]*key|password)\b",
    re.IGNORECASE,
)
PIN_RE = re.compile(
    r"\b(?:m?pin|otp|passcode|security[\s_-]*code)\s*[:=-]?\s*\d{4,12}\b",
    re.IGNORECASE,
)
NUMERIC_SECRET_RE = re.compile(r"^\s*\d{6,12}\s*$")
FINANCIAL_TRANSACTION_RE = re.compile(
    r"(?:app\.debridge\.com/order|(?:ether|arbi|base|bsc)scan\.io/tx|"
    r"solscan\.io/tx|txhash=)",
    re.IGNORECASE,
)
LONG_CREDENTIAL_RE = re.compile(r"(?<![\w/])[A-Za-z0-9_+%=/.-]{28,}(?![\w/])")
SENSITIVE_FILE_RE = re.compile(
    r"(?:account|acct|bank|statement|itr|tax|passport|aadhaar|pan[ _-]?card|"
    r"questionnaire|private[ _-]?key)",
    re.IGNORECASE,
)
SEED_LABEL_RE = re.compile(
    r"^\s*(?:(?:wallet|crypto)\s+)?"
    r"(?:seed|recovery|mnemonic)(?:\s+phrase)?\s*[:=-]?\s*",
    re.IGNORECASE,
)

TOPIC_RULES = {
    "Trading & Markets": {
        "phrases": (
            "perp dex",
            "price action",
            "stop loss",
            "open interest",
            "funding rate",
            "take profit",
            "market structure",
        ),
        "words": (
            "trading",
            "trade",
            "trader",
            "market",
            "markets",
            "stocks",
            "equity",
            "equities",
            "crypto",
            "bitcoin",
            "ethereum",
            "btc",
            "eth",
            "sol",
            "perp",
            "perps",
            "leverage",
            "airdrop",
            "nasdaq",
            "entry",
            "chart",
        ),
    },
    "Work & Career": {
        "phrases": (
            "growth manager",
            "community manager",
            "cover letter",
            "job application",
            "applying for",
            "applied for",
            "first 30 days",
            "trader success",
            "personally onboarded",
            "helped scale",
            "my experience",
        ),
        "words": (
            "job",
            "jobs",
            "career",
            "hiring",
            "hire",
            "role",
            "resume",
            "cv",
            "interview",
            "recruiter",
            "salary",
            "linkedin",
            "application",
            "applying",
            "applied",
            "sales",
            "bd",
            "pitch",
            "partnerships",
            "campaigns",
            "marketing",
            "community",
            "onboarding",
            "onboarded",
        ),
    },
    "Writing": {
        "phrases": ("article idea", "writing idea", "first draft"),
        "words": (
            "article",
            "essay",
            "draft",
            "headline",
            "substack",
            "writing",
            "write",
            "paragraph",
        ),
    },
    "Research & Learning": {
        "phrases": ("deep dive", "case study", "research note"),
        "words": (
            "research",
            "report",
            "analysis",
            "study",
            "paper",
            "learn",
            "guide",
            "explainer",
            "thesis",
            "data",
        ),
    },
    "Technology & AI": {
        "phrases": (
            "artificial intelligence",
            "machine learning",
            "large language model",
            "cloud compute",
            "advanced packaging",
            "co-packaged optics",
        ),
        "words": (
            "ai",
            "llm",
            "llms",
            "model",
            "models",
            "inference",
            "compute",
            "memory",
            "semiconductor",
            "semiconductors",
            "semis",
            "chip",
            "chips",
            "cloud",
            "nvidia",
            "cxl",
            "hbm",
            "hbf",
            "nand",
            "optics",
        ),
    },
    "Ideas & Building": {
        "phrases": (
            "product idea",
            "startup idea",
            "vibe coding",
            "build this",
        ),
        "words": (
            "idea",
            "ideas",
            "concept",
            "build",
            "building",
            "product",
            "startup",
            "agent",
            "automation",
        ),
    },
    "Memes & Culture": {
        "phrases": ("shit post", "shit posting"),
        "words": (
            "meme",
            "memes",
            "shitpost",
            "shitposting",
            "funny",
            "joke",
            "lol",
            "lmao",
        ),
    },
    "Personal & Admin": {
        "phrases": ("note to self", "remember to", "to do"),
        "words": (
            "personal",
            "remember",
            "todo",
            "shopping",
            "invoice",
            "receipt",
            "appointment",
            "address",
            "order",
            "orders",
            "paid",
            "payment",
            "shopping",
            "ply",
            "mica",
            "keel",
            "bricks",
            "board",
            "qty",
        ),
    },
}


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    phone: str | None
    chat_id: int | None
    session_path: Path
    db_path: Path
    media_dir: Path
    enable_embeddings: bool
    enable_vision: bool
    ollama_url: str
    embed_model: str
    vision_model: str
    enable_content_indexing: bool = True
    content_poll_seconds: int = 8
    whisper_command: str | None = None
    whisper_model_path: Path | None = None


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    return (
        default
        if raw is None
        else raw.lower().strip() in {"1", "true", "yes", "on"}
    )


def settings(require_chat: bool = False) -> Settings:
    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not api_id or not api_hash:
        raise RuntimeError(
            "Fill TELEGRAM_API_ID and TELEGRAM_API_HASH in .env"
        )

    chat_id_raw = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    chat_id = int(chat_id_raw) if chat_id_raw else None
    if require_chat and chat_id is None:
        raise RuntimeError(
            "Run list_chats.py and set TELEGRAM_CHAT_ID in .env"
        )

    db_path = Path(
        os.getenv("DB_PATH", "./data/archive.sqlite3")
    ).expanduser().resolve()
    media_dir = Path(
        os.getenv("MEDIA_DIR", "./data/media")
    ).expanduser().resolve()
    session_path = Path(
        os.getenv("TELEGRAM_SESSION_PATH", "./data/telegram_session")
    ).expanduser().resolve()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(parents=True, exist_ok=True)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        api_id=int(api_id),
        api_hash=api_hash,
        phone=os.getenv("TELEGRAM_PHONE") or None,
        chat_id=chat_id,
        session_path=session_path,
        db_path=db_path,
        media_dir=media_dir,
        enable_embeddings=env_bool("ENABLE_EMBEDDINGS"),
        enable_vision=env_bool("ENABLE_VISION"),
        ollama_url=os.getenv(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        ).rstrip("/"),
        embed_model=os.getenv(
            "OLLAMA_EMBED_MODEL", "embeddinggemma"
        ),
        vision_model=os.getenv("OLLAMA_VISION_MODEL", "gemma3:4b"),
        enable_content_indexing=env_bool("ENABLE_CONTENT_INDEXING", True),
        content_poll_seconds=max(
            2,
            int(os.getenv("CONTENT_POLL_SECONDS", "8")),
        ),
        whisper_command=os.getenv("WHISPER_COMMAND") or None,
        whisper_model_path=(
            Path(os.getenv("WHISPER_MODEL_PATH", "")).expanduser().resolve()
            if os.getenv("WHISPER_MODEL_PATH", "").strip()
            else None
        ),
    )


def client(config: Settings) -> TelegramClient:
    return TelegramClient(
        str(config.session_path),
        config.api_id,
        config.api_hash,
    )


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    date_utc TEXT NOT NULL,
    sender_name TEXT,
    text TEXT NOT NULL DEFAULT '',
    media_type TEXT,
    media_path TEXT,
    file_name TEXT,
    mime_type TEXT,
    urls_json TEXT NOT NULL DEFAULT '[]',
    vision_text TEXT NOT NULL DEFAULT '',
    vision_model TEXT NOT NULL DEFAULT '',
    vision_prompt_version INTEGER NOT NULL DEFAULT 0,
    vision_attempted_at TEXT,
    indexed_text TEXT NOT NULL DEFAULT '',
    embedding_json TEXT,
    embedding_model TEXT NOT NULL DEFAULT '',
    embedding_attempted_at TEXT,
    extracted_text TEXT NOT NULL DEFAULT '',
    link_metadata_json TEXT NOT NULL DEFAULT '[]',
    content_status TEXT NOT NULL DEFAULT 'pending',
    content_error TEXT NOT NULL DEFAULT '',
    enrichment_version INTEGER NOT NULL DEFAULT 0,
    enriched_at TEXT,
    telegram_group_id TEXT,
    capture_id TEXT,
    capture_position INTEGER NOT NULL DEFAULT 0,
    capture_version INTEGER NOT NULL DEFAULT 0,
    media_sha256 TEXT,
    visual_hash TEXT,
    media_aspect REAL,
    duplicate_of_id INTEGER,
    duplicate_reason TEXT,
    duplicate_distance REAL,
    fingerprint_version INTEGER NOT NULL DEFAULT 0,
    category TEXT NOT NULL DEFAULT 'Uncategorised',
    is_sensitive INTEGER NOT NULL DEFAULT 0,
    sensitive_reason TEXT,
    metadata_version INTEGER NOT NULL DEFAULT 0,
    is_pinned INTEGER NOT NULL DEFAULT 0,
    UNIQUE(chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_date ON messages(date_utc DESC);
CREATE INDEX IF NOT EXISTS idx_media_type ON messages(media_type);

CREATE TABLE IF NOT EXISTS item_state(
    message_row_id INTEGER PRIMARY KEY,
    starred INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    user_category TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(message_row_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS runtime_state(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text,
    vision_text,
    file_name,
    sender_name,
    indexed_text,
    content='messages',
    content_rowid='id',
    tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS msg_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(
        rowid, text, vision_text, file_name, sender_name, indexed_text
    ) VALUES(
        new.id, new.text, new.vision_text, new.file_name,
        new.sender_name, new.indexed_text
    );
END;
CREATE TRIGGER IF NOT EXISTS msg_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(
        messages_fts, rowid, text, vision_text, file_name,
        sender_name, indexed_text
    ) VALUES(
        'delete', old.id, old.text, old.vision_text, old.file_name,
        old.sender_name, old.indexed_text
    );
END;
CREATE TRIGGER IF NOT EXISTS msg_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(
        messages_fts, rowid, text, vision_text, file_name,
        sender_name, indexed_text
    ) VALUES(
        'delete', old.id, old.text, old.vision_text, old.file_name,
        old.sender_name, old.indexed_text
    );
    INSERT INTO messages_fts(
        rowid, text, vision_text, file_name, sender_name, indexed_text
    ) VALUES(
        new.id, new.text, new.vision_text, new.file_name,
        new.sender_name, new.indexed_text
    );
END;
"""


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table})")
    }


def _migrate(connection: sqlite3.Connection) -> None:
    columns = _column_names(connection, "messages")
    additions = {
        "category": "TEXT NOT NULL DEFAULT 'Uncategorised'",
        "is_sensitive": "INTEGER NOT NULL DEFAULT 0",
        "sensitive_reason": "TEXT",
        "metadata_version": "INTEGER NOT NULL DEFAULT 0",
        "extracted_text": "TEXT NOT NULL DEFAULT ''",
        "link_metadata_json": "TEXT NOT NULL DEFAULT '[]'",
        "content_status": "TEXT NOT NULL DEFAULT 'pending'",
        "content_error": "TEXT NOT NULL DEFAULT ''",
        "enrichment_version": "INTEGER NOT NULL DEFAULT 0",
        "enriched_at": "TEXT",
        "telegram_group_id": "TEXT",
        "capture_id": "TEXT",
        "capture_position": "INTEGER NOT NULL DEFAULT 0",
        "capture_version": "INTEGER NOT NULL DEFAULT 0",
        "media_sha256": "TEXT",
        "visual_hash": "TEXT",
        "media_aspect": "REAL",
        "duplicate_of_id": "INTEGER",
        "duplicate_reason": "TEXT",
        "duplicate_distance": "REAL",
        "fingerprint_version": "INTEGER NOT NULL DEFAULT 0",
        "is_pinned": "INTEGER NOT NULL DEFAULT 0",
        "vision_model": "TEXT NOT NULL DEFAULT ''",
        "vision_prompt_version": "INTEGER NOT NULL DEFAULT 0",
        "vision_attempted_at": "TEXT",
        "embedding_model": "TEXT NOT NULL DEFAULT ''",
        "embedding_attempted_at": "TEXT",
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(
                f"ALTER TABLE messages ADD COLUMN {name} {definition}"
            )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_category ON messages(category)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_sensitive ON messages(is_sensitive)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_capture_id ON messages(capture_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_status "
        "ON messages(content_status)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_sha256 "
        "ON messages(media_sha256)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_visual_hash "
        "ON messages(visual_hash)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_duplicate_of "
        "ON messages(duplicate_of_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_pinned ON messages(is_pinned)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_vision_refresh "
        "ON messages(media_type, vision_model, vision_prompt_version)"
    )
    connection.commit()


def db(path: Path | str) -> sqlite3.Connection:
    resolved = str(Path(path).resolve())
    connection = sqlite3.connect(resolved, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    if resolved not in _INITIALIZED_DATABASES:
        connection.executescript(SCHEMA)
        _migrate(connection)
        needs_capture_refresh = connection.execute(
            """
            SELECT 1
            FROM messages
            WHERE capture_version < ?
            LIMIT 1
            """,
            (CAPTURE_VERSION,),
        ).fetchone()
        if needs_capture_refresh:
            rebuild_captures(connection)
        refresh_metadata(connection)
        _INITIALIZED_DATABASES.add(resolved)
    return connection


def rebuild_search_index(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO messages_fts(messages_fts) VALUES('rebuild')"
    )
    connection.commit()


def ollama_post(url: str, payload: dict, timeout: int = 180) -> dict:
    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def embed(config: Settings, text: str) -> list[float]:
    data = ollama_post(
        f"{config.ollama_url}/api/embed",
        {"model": config.embed_model, "input": text},
    )
    return data["embeddings"][0]


def image_for_vision(path: str, max_edge: int = 768) -> str:
    with Image.open(path) as source:
        source.seek(0)
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(
            output,
            format="JPEG",
            quality=84,
            optimize=True,
        )
    return base64.b64encode(output.getvalue()).decode("ascii")


def describe(config: Settings, path: str) -> str:
    image = image_for_vision(path)
    data = ollama_post(
        f"{config.ollama_url}/api/chat",
        {
            "model": config.vision_model,
            "stream": False,
            "keep_alive": "5m",
            "options": {
                "temperature": 0,
                "num_predict": 240,
            },
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Index this image for a private visual search library. "
                        "Return concise plain text using exactly these labels: "
                        "Summary, People, Visible text, Objects, Setting, "
                        "Meme context, Search terms. Read all useful text. "
                        "Name recognizable public figures when confident, "
                        "using their full canonical names and common aliases. "
                        "For a meme, identify its subjects, template and joke. "
                        "Search terms must include concrete names, entities, "
                        "actions, visual traits, topics and likely user query "
                        "phrases. Use Unknown rather than inventing identity. "
                        "Stay below 180 words and do not include a preamble."
                    ),
                    "images": [image],
                }
            ],
        },
        timeout=60,
    )
    message = data.get("message", {})
    content = message.get("content", "").strip()
    if not content:
        content = re.sub(
            r"</?think>",
            "",
            message.get("thinking", ""),
        ).strip()
    return re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", content).strip()


def media_kind(path: str | None, mime: str | None) -> str | None:
    if not path and not mime:
        return None
    suffix = Path(path).suffix.lower() if path else ""
    mime = (mime or "").lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"} or mime.startswith(
        "image/"
    ):
        return "image"
    if suffix in {".mp4", ".mov", ".mkv", ".webm"} or mime.startswith("video/"):
        return "video"
    if suffix in {".mp3", ".m4a", ".wav", ".ogg", ".opus"} or mime.startswith(
        "audio/"
    ):
        return "audio"
    if suffix == ".pdf" or mime == "application/pdf":
        return "pdf"
    return "document"


def sensitive_status(
    text: str | None,
    file_name: str | None = None,
) -> tuple[bool, str | None]:
    value = text or ""
    for reason, pattern in SECRET_PATTERNS:
        if pattern.search(value):
            return True, reason

    if SECRET_LABEL_RE.search(value) and LONG_CREDENTIAL_RE.search(value):
        return True, "labelled credential"

    if PIN_RE.search(value) or NUMERIC_SECRET_RE.fullmatch(value):
        return True, "PIN or numeric credential"

    if contains_recovery_phrase(value):
        return True, "wallet recovery phrase"

    if FINANCIAL_TRANSACTION_RE.search(value):
        return True, "financial transaction"

    if file_name and SENSITIVE_FILE_RE.search(file_name):
        return True, "personal document"

    return False, None


def contains_recovery_phrase(text: str | None) -> bool:
    value = text or ""
    candidates = [value, *value.splitlines()]
    for candidate in candidates:
        candidate = SEED_LABEL_RE.sub("", candidate.strip())
        words = re.findall(r"[a-z]+", candidate.lower())
        if len(words) not in {12, 15, 18, 21, 24}:
            continue
        normalized = " ".join(words)
        if MNEMONIC.check(normalized):
            return True
    return False


def mask_sensitive_text(text: str | None) -> str:
    value = text or ""
    if not value:
        return ""
    if contains_recovery_phrase(value):
        return "[hidden recovery phrase]"

    masked = value
    for _, pattern in SECRET_PATTERNS:
        masked = pattern.sub("[hidden credential]", masked)

    if SECRET_LABEL_RE.search(masked):
        masked = LONG_CREDENTIAL_RE.sub("[hidden credential]", masked)
    return masked


def _topic_score(
    lowered: str,
    tokens: set[str],
    phrases: Iterable[str],
    words: Iterable[str],
) -> int:
    phrase_score = sum(3 for phrase in phrases if phrase in lowered)
    word_score = sum(1 for word in words if word in tokens)
    return phrase_score + word_score


def detect_category(
    text: str | None,
    vision_text: str | None = None,
    file_name: str | None = None,
    media_type: str | None = None,
    extracted_text: str | None = None,
    link_text: str | None = None,
) -> str:
    combined = " ".join(
        value
        for value in (
            text,
            vision_text,
            extracted_text,
            link_text,
            file_name,
        )
        if value
    ).lower()
    tokens = {
        token.strip(".-")
        for token in re.findall(r"[a-z0-9][a-z0-9+#.-]*", combined)
        if token.strip(".-")
    }

    career_markers = {
        "applying",
        "applied",
        "application",
        "hiring",
        "role",
        "resume",
        "cv",
        "interview",
        "recruiter",
    }
    if len(tokens & career_markers) >= 2:
        return "Work & Career"

    scores = {
        category: _topic_score(
            combined,
            tokens,
            rules["phrases"],
            rules["words"],
        )
        for category, rules in TOPIC_RULES.items()
    }
    best_category = max(scores, key=scores.get)
    if scores[best_category] > 0:
        return best_category

    if URL_RE.search(combined):
        return "Links & References"

    if media_type in {"pdf", "document"}:
        return "Documents"
    if media_type in {"image", "video", "audio"}:
        return "Images & Media"
    return "Uncategorised"


def link_metadata_text(value: str | None) -> str:
    try:
        records = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return ""
    pieces = []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        pieces.extend(
            str(record.get(key) or "")
            for key in ("title", "description", "site_name")
        )
    return "\n".join(piece for piece in pieces if piece)


def build_indexed_text(record: dict) -> str:
    try:
        urls = json.loads(record.get("urls_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        urls = []
    if not isinstance(urls, list):
        urls = []
    values = (
        record.get("text") or "",
        record.get("vision_text") or "",
        record.get("extracted_text") or "",
        link_metadata_text(record.get("link_metadata_json")),
        record.get("file_name") or "",
        *(str(url) for url in urls if isinstance(url, str)),
    )
    return "\n".join(value.strip() for value in values if value.strip())


def message_metadata(record: dict) -> dict:
    link_text = link_metadata_text(record.get("link_metadata_json"))
    searchable_content = "\n".join(
        value
        for value in (
            record.get("text"),
            record.get("vision_text"),
            record.get("extracted_text"),
            link_text,
        )
        if value
    )
    is_sensitive, reason = sensitive_status(
        searchable_content,
        record.get("file_name"),
    )
    category = detect_category(
        record.get("text"),
        record.get("vision_text"),
        record.get("file_name"),
        record.get("media_type"),
        record.get("extracted_text"),
        link_text,
    )
    return {
        "category": category,
        "is_sensitive": int(is_sensitive),
        "sensitive_reason": reason,
        "metadata_version": METADATA_VERSION,
    }


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _capture_contextual(row: dict) -> bool:
    text = (row.get("text") or "").strip()
    category = row.get("category") or "Uncategorised"
    return (
        len(text) >= 80
        or bool(row.get("media_type"))
        or category
        not in {
            "Uncategorised",
            "Images & Media",
            "Documents",
            "Links & References",
        }
    )


def _belongs_to_capture(previous: dict, current: dict) -> bool:
    previous_group = previous.get("telegram_group_id")
    current_group = current.get("telegram_group_id")
    if previous_group and previous_group == current_group:
        return True

    previous_time = _parse_timestamp(previous.get("date_utc"))
    current_time = _parse_timestamp(current.get("date_utc"))
    if not previous_time or not current_time:
        return False
    gap = (current_time - previous_time).total_seconds()
    message_gap = int(current.get("message_id") or 0) - int(
        previous.get("message_id") or 0
    )
    if gap < 0 or message_gap <= 0:
        return False
    if gap == 0 and message_gap <= 10:
        return True
    if gap > 120 or message_gap > 5:
        return False

    previous_category = previous.get("category") or "Uncategorised"
    current_category = current.get("category") or "Uncategorised"
    same_specific_topic = (
        previous_category == current_category
        and previous_category
        not in {
            "Uncategorised",
            "Images & Media",
            "Documents",
            "Links & References",
        }
    )
    media_pair = bool(previous.get("media_type")) != bool(
        current.get("media_type")
    )
    long_continuation = (
        len((previous.get("text") or "").strip()) >= 80
        and len((current.get("text") or "").strip()) >= 80
    )
    return (
        gap <= 45
        and (
            same_specific_topic
            or media_pair
            or long_continuation
            or (
                _capture_contextual(previous)
                and _capture_contextual(current)
                and gap <= 15
            )
        )
    )


def rebuild_captures(
    connection: sqlite3.Connection,
    chat_id: int | None = None,
) -> int:
    clauses = ""
    params: list = []
    if chat_id is not None:
        clauses = "WHERE chat_id = ?"
        params.append(chat_id)
    rows = [
        dict(row)
        for row in connection.execute(
            f"""
            SELECT id, chat_id, message_id, date_utc, text, media_type,
                   category, telegram_group_id, capture_id,
                   capture_position, capture_version
            FROM messages
            {clauses}
            ORDER BY chat_id, date_utc, message_id
            """,
            params,
        ).fetchall()
    ]
    updates = []
    previous = None
    root_message_id = None
    position = 0
    for row in rows:
        same_chat = previous and previous["chat_id"] == row["chat_id"]
        if same_chat and _belongs_to_capture(previous, row):
            position += 1
        else:
            root_message_id = row["message_id"]
            position = 0
        capture_id = f"{row['chat_id']}:{root_message_id}"
        if (
            row.get("capture_id") != capture_id
            or int(row.get("capture_position") or 0) != position
            or int(row.get("capture_version") or 0) != CAPTURE_VERSION
        ):
            updates.append(
                (capture_id, position, CAPTURE_VERSION, row["id"])
            )
        previous = row
    if updates:
        connection.executemany(
            """
            UPDATE messages
            SET capture_id = ?, capture_position = ?, capture_version = ?
            WHERE id = ?
            """,
            updates,
        )
        connection.commit()
    return len(updates)


def _inherited_category(
    connection: sqlite3.Connection,
    record: dict,
) -> str | None:
    text = (record.get("text") or "").strip()
    if (
        record.get("category") != "Uncategorised"
        or record.get("is_sensitive")
        or record.get("media_type")
        or len(text) < 80
    ):
        return None

    previous = connection.execute(
        """
        SELECT message_id, date_utc, category
        FROM messages
        WHERE chat_id = ?
          AND message_id < ?
          AND is_sensitive = 0
          AND category NOT IN (
              'Uncategorised', 'Images & Media', 'Documents',
              'Links & References'
          )
        ORDER BY message_id DESC
        LIMIT 1
        """,
        (record.get("chat_id"), record.get("message_id")),
    ).fetchone()
    if not previous:
        return None

    current_time = _parse_timestamp(record.get("date_utc"))
    previous_time = _parse_timestamp(previous["date_utc"])
    if not current_time or not previous_time:
        return None
    age_seconds = (current_time - previous_time).total_seconds()
    message_gap = int(record.get("message_id") or 0) - int(
        previous["message_id"]
    )
    if 0 <= age_seconds <= 600 and 0 < message_gap <= 10:
        return str(previous["category"])
    return None


def refresh_metadata(connection: sqlite3.Connection) -> int:
    rows = connection.execute(
        """
        SELECT id, chat_id, message_id, date_utc, sender_name, text,
               vision_text, extracted_text, link_metadata_json,
               file_name, media_type
        FROM messages
        WHERE metadata_version < ?
        ORDER BY date_utc, message_id
        """,
        (METADATA_VERSION,),
    ).fetchall()
    for row in rows:
        metadata = message_metadata(dict(row))
        connection.execute(
            """
            UPDATE messages
            SET category = ?, is_sensitive = ?, sensitive_reason = ?,
                metadata_version = ?
            WHERE id = ?
            """,
            (
                metadata["category"],
                metadata["is_sensitive"],
                metadata["sensitive_reason"],
                metadata["metadata_version"],
                row["id"],
            ),
        )
    if rows:
        connection.commit()

    continuation_rows = connection.execute(
        """
        SELECT id, chat_id, message_id, date_utc, text, media_type,
               category, is_sensitive
        FROM messages
        WHERE metadata_version = ?
          AND category = 'Uncategorised'
          AND is_sensitive = 0
        ORDER BY date_utc, message_id
        """,
        (METADATA_VERSION,),
    ).fetchall()
    for row in continuation_rows:
        inherited = _inherited_category(connection, dict(row))
        if inherited:
            connection.execute(
                "UPDATE messages SET category = ? WHERE id = ?",
                (inherited, row["id"]),
            )
    if rows:
        connection.commit()
    return len(rows)


def upsert(connection: sqlite3.Connection, record: dict) -> None:
    enriched = dict(record)
    existing = connection.execute(
        """
        SELECT media_path, urls_json, vision_text, extracted_text,
               link_metadata_json, content_status, content_error,
               enrichment_version, enriched_at, embedding_json,
               embedding_model, embedding_attempted_at,
               vision_model, vision_prompt_version, vision_attempted_at,
               media_sha256, visual_hash, media_aspect,
               duplicate_of_id, duplicate_reason, duplicate_distance,
               fingerprint_version
        FROM messages
        WHERE chat_id = ? AND message_id = ?
        """,
        (enriched.get("chat_id"), enriched.get("message_id")),
    ).fetchone()
    same_sources = bool(
        existing
        and (existing["media_path"] or "") == (enriched.get("media_path") or "")
        and (existing["urls_json"] or "[]")
        == (enriched.get("urls_json") or "[]")
    )
    if same_sources:
        for field in (
            "extracted_text",
            "link_metadata_json",
            "content_status",
            "content_error",
            "enrichment_version",
            "enriched_at",
            "embedding_json",
            "embedding_model",
            "embedding_attempted_at",
            "vision_model",
            "vision_prompt_version",
            "vision_attempted_at",
            "media_sha256",
            "visual_hash",
            "media_aspect",
            "duplicate_of_id",
            "duplicate_reason",
            "duplicate_distance",
            "fingerprint_version",
        ):
            if enriched.get(field) in {None, "", "[]"}:
                enriched[field] = existing[field]
        if not enriched.get("vision_text"):
            enriched["vision_text"] = existing["vision_text"]
    else:
        enriched.setdefault("extracted_text", "")
        enriched.setdefault("link_metadata_json", "[]")
        enriched["content_status"] = "pending"
        enriched["content_error"] = ""
        enriched["enrichment_version"] = 0
        enriched["enriched_at"] = None
        enriched["embedding_json"] = None
        enriched["embedding_model"] = ""
        enriched["embedding_attempted_at"] = None
        enriched["vision_text"] = ""
        enriched["vision_model"] = ""
        enriched["vision_prompt_version"] = 0
        enriched["vision_attempted_at"] = None
        enriched.setdefault("media_sha256", None)
        enriched.setdefault("visual_hash", None)
        enriched.setdefault("media_aspect", None)
        enriched.setdefault("duplicate_of_id", None)
        enriched.setdefault("duplicate_reason", None)
        enriched.setdefault("duplicate_distance", None)
        enriched.setdefault("fingerprint_version", 0)

    enriched.setdefault("vision_text", "")
    enriched.setdefault("vision_model", "")
    enriched.setdefault("vision_prompt_version", 0)
    enriched.setdefault("vision_attempted_at", None)
    enriched.setdefault("embedding_model", "")
    enriched.setdefault("embedding_attempted_at", None)
    enriched.setdefault("extracted_text", "")
    enriched.setdefault("link_metadata_json", "[]")
    enriched.setdefault("content_status", "pending")
    enriched.setdefault("content_error", "")
    enriched.setdefault("enrichment_version", 0)
    enriched.setdefault("capture_position", 0)
    enriched.setdefault("capture_version", 0)
    enriched.setdefault("fingerprint_version", 0)
    enriched.setdefault("is_pinned", 0)
    enriched["indexed_text"] = build_indexed_text(enriched)
    enriched.update(message_metadata(enriched))
    inherited = _inherited_category(connection, enriched)
    if inherited:
        enriched["category"] = inherited
    columns = [
        "chat_id",
        "message_id",
        "date_utc",
        "sender_name",
        "text",
        "media_type",
        "media_path",
        "file_name",
        "mime_type",
        "urls_json",
        "vision_text",
        "vision_model",
        "vision_prompt_version",
        "vision_attempted_at",
        "indexed_text",
        "embedding_json",
        "embedding_model",
        "embedding_attempted_at",
        "extracted_text",
        "link_metadata_json",
        "content_status",
        "content_error",
        "enrichment_version",
        "enriched_at",
        "telegram_group_id",
        "capture_id",
        "capture_position",
        "capture_version",
        "media_sha256",
        "visual_hash",
        "media_aspect",
        "duplicate_of_id",
        "duplicate_reason",
        "duplicate_distance",
        "fingerprint_version",
        "category",
        "is_sensitive",
        "sensitive_reason",
        "metadata_version",
        "is_pinned",
    ]
    placeholders = ",".join("?" * len(columns))
    updates = ",".join(
        f"{column}=excluded.{column}"
        for column in columns
        if column not in {"chat_id", "message_id"}
    )
    connection.execute(
        f"""
        INSERT INTO messages({",".join(columns)})
        VALUES({placeholders})
        ON CONFLICT(chat_id, message_id) DO UPDATE SET {updates}
        """,
        [enriched.get(column) for column in columns],
    )
    connection.commit()
    rebuild_captures(connection, enriched.get("chat_id"))


async def ingest(
    telegram: TelegramClient,
    config: Settings,
    connection: sqlite3.Connection,
    message,
) -> bool:
    # Telegram's service chat can contain login codes and device alerts.
    # Archive only messages sent by the account owner from that chat.
    if (
        config.chat_id == 777000
        and not bool(getattr(message, "out", False))
    ):
        return False

    sender = await message.get_sender()
    sender_name = (
        getattr(sender, "title", None)
        or " ".join(
            value
            for value in (
                getattr(sender, "first_name", None),
                getattr(sender, "last_name", None),
            )
            if value
        ).strip()
        or getattr(sender, "username", None)
    )

    existing = connection.execute(
        """
        SELECT media_path, file_name, mime_type
        FROM messages
        WHERE chat_id = ? AND message_id = ?
        """,
        (config.chat_id, message.id),
    ).fetchone()

    path = existing["media_path"] if existing else None
    if path and not Path(path).exists():
        path = None

    downloaded_path = None
    if message.media and not path:
        output = config.media_dir / str(config.chat_id)
        output.mkdir(parents=True, exist_ok=True)
        downloaded = await message.download_media(file=str(output))
        path = str(Path(downloaded).resolve()) if downloaded else None
        downloaded_path = path

    file_name = (
        getattr(getattr(message, "file", None), "name", None)
        or (existing["file_name"] if existing else None)
        or (Path(path).name if path else None)
    )
    mime_type = (
        getattr(getattr(message, "file", None), "mime_type", None)
        or (existing["mime_type"] if existing else None)
        or (mimetypes.guess_type(path)[0] if path else None)
    )
    text = message.raw_text or ""
    urls = URL_RE.findall(text)
    kind = media_kind(path, mime_type)
    fingerprint = {
        "media_sha256": None,
        "visual_hash": None,
        "media_aspect": None,
        "fingerprint_version": 0,
    }
    duplicate = None
    if path and Path(path).exists():
        try:
            fingerprint = fingerprint_media(path, kind)
            duplicate = find_duplicate(
                connection,
                fingerprint,
                path,
                kind,
                chat_id=config.chat_id,
                message_id=message.id,
            )
        except OSError:
            pass
    if (
        duplicate
        and duplicate["reason"] == "exact"
        and downloaded_path
        and Path(downloaded_path) != Path(duplicate["media_path"])
        and Path(duplicate["media_path"]).exists()
    ):
        Path(downloaded_path).unlink(missing_ok=True)
        path = duplicate["media_path"]

    upsert(
        connection,
        {
            "chat_id": config.chat_id,
            "message_id": message.id,
            "date_utc": message.date.isoformat(),
            "sender_name": sender_name,
            "text": text,
            "media_type": kind,
            "media_path": path,
            "file_name": file_name,
            "mime_type": mime_type,
            "urls_json": json.dumps(urls),
            "vision_text": "",
            "indexed_text": "",
            "embedding_json": None,
            **fingerprint,
            "duplicate_of_id": duplicate["id"] if duplicate else None,
            "duplicate_reason": (
                duplicate["reason"] if duplicate else None
            ),
            "duplicate_distance": (
                duplicate["distance"] if duplicate else None
            ),
            "telegram_group_id": (
                str(message.grouped_id)
                if getattr(message, "grouped_id", None)
                else None
            ),
            "is_pinned": int(bool(getattr(message, "pinned", False))),
        },
    )
    return True


async def sync_pinned_messages(
    telegram: TelegramClient,
    config: Settings,
    connection: sqlite3.Connection,
    entity,
) -> int:
    pinned_messages = []
    async for message in telegram.iter_messages(
        entity,
        filter=InputMessagesFilterPinned,
    ):
        pinned_messages.append(message)

    pinned_ids = {int(message.id) for message in pinned_messages}
    known_ids = {
        int(row["message_id"])
        for row in connection.execute(
            "SELECT message_id FROM messages WHERE chat_id = ?",
            (config.chat_id,),
        ).fetchall()
    }
    for message in pinned_messages:
        if int(message.id) not in known_ids:
            await ingest(telegram, config, connection, message)

    connection.execute(
        "UPDATE messages SET is_pinned = 0 WHERE chat_id = ?",
        (config.chat_id,),
    )
    if pinned_ids:
        ordered_ids = sorted(pinned_ids)
        for start in range(0, len(ordered_ids), 400):
            batch = ordered_ids[start : start + 400]
            placeholders = ",".join("?" * len(batch))
            connection.execute(
                f"""
                UPDATE messages
                SET is_pinned = 1
                WHERE chat_id = ?
                  AND message_id IN ({placeholders})
                """,
                [config.chat_id, *batch],
            )
    connection.commit()
    set_runtime_state(connection, "pinned_count", len(pinned_ids))
    return len(pinned_ids)


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return -1
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return -1
    return sum(a * b for a, b in zip(left, right)) / (
        left_norm * right_norm
    )


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[\w+#.-]+", query.lower(), re.UNICODE)
    clean = [
        token.replace('"', "")
        for token in tokens
        if len(token.strip(".-")) >= 2 and token not in SEARCH_STOPWORDS
    ]
    return " AND ".join(f'"{token}"*' for token in clean[:12])


def _base_filters(
    kind: str,
    include_sensitive: bool,
    category: str | None,
    starred_only: bool,
    pinned_only: bool,
) -> tuple[list[str], list]:
    clauses = ["m.duplicate_of_id IS NULL"]
    params: list = []
    if not include_sensitive:
        clauses.append("m.is_sensitive = 0")
    if kind == "text":
        clauses.append("m.media_type IS NULL")
    elif kind != "all":
        clauses.append("m.media_type = ?")
        params.append(kind)
    if category and category != "All topics":
        clauses.append("COALESCE(s.user_category, m.category) = ?")
        params.append(category)
    if starred_only:
        clauses.append("COALESCE(s.starred, 0) = 1")
    if pinned_only:
        clauses.append(
            """
            EXISTS (
                SELECT 1
                FROM messages pinned
                WHERE pinned.chat_id = m.chat_id
                  AND pinned.is_pinned = 1
                  AND COALESCE(
                      pinned.capture_id,
                      pinned.chat_id || ':' || pinned.message_id
                  ) = COALESCE(
                      m.capture_id,
                      m.chat_id || ':' || m.message_id
                  )
            )
            """
        )
    return clauses, params


def _query_terms(query: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[\w+#.-]+", query, re.UNICODE)
        if len(token.strip(".-")) >= 2
        and token.lower() not in SEARCH_STOPWORDS
    ][:12]


def _person_query_terms(query: str) -> list[str]:
    terms = [
        term
        for term in _query_terms(query)
        if term not in PERSON_QUERY_GENERIC and term.isalpha()
    ]
    return terms if 2 <= len(terms) <= 3 else []


def _covers_person_terms(value: str | None, terms: list[str]) -> bool:
    value_terms = _query_terms(value or "")

    def close(left: str, right: str) -> bool:
        return left == right or (
            min(len(left), len(right)) >= 4
            and SequenceMatcher(None, left, right).ratio() >= 0.84
        )

    return bool(value_terms) and all(
        any(close(term, value_term) for value_term in value_terms)
        for term in terms
    )


def _matches_people_field(vision_text: str | None, terms: list[str]) -> bool:
    if not terms:
        return True
    match = re.search(
        r"(?im)^people\s*:\s*(.+)$",
        vision_text or "",
    )
    if not match or match.group(1).strip().lower() in {"", "none", "unknown"}:
        return False
    return _covers_person_terms(match.group(1), terms)


def _match_reasons(parts: list[dict], query: str) -> list[str]:
    terms = _query_terms(query)
    if not terms:
        return ["Shown by the current library filters"]

    reasons = []

    def matched(value: str | None) -> bool:
        value_terms = {
            token.lower()
            for token in re.findall(r"[\w+#.-]+", value or "", re.UNICODE)
        }
        return bool(value_terms) and any(
            term in value_terms
            or (
                len(term) >= 4
                and any(token.startswith(term) for token in value_terms)
            )
            for term in terms
        )

    if any(matched(part.get("text")) for part in parts):
        reasons.append("Matched the original Telegram text")
    if any(matched(part.get("file_name")) for part in parts):
        reasons.append("Matched a saved file name")
    if any(matched(part.get("urls_json")) for part in parts):
        reasons.append("Matched a saved URL")
    if any(
        matched(link_metadata_text(part.get("link_metadata_json")))
        for part in parts
    ):
        reasons.append("Matched a link title or description")

    extracted_types = {
        part.get("media_type")
        for part in parts
        if matched(part.get("extracted_text"))
    }
    if "image" in extracted_types:
        reasons.append("Matched text read from an image")
    if extracted_types & {"pdf", "document"}:
        reasons.append("Matched text extracted from a document")
    if "audio" in extracted_types:
        reasons.append("Matched a local voice-note transcript")
    if any(matched(part.get("vision_text")) for part in parts):
        reasons.append("Matched an AI image description")
    if not reasons and any(
        float(part.get("_semantic_score") or 0) > 0 for part in parts
    ):
        reasons.append("Related by semantic meaning")
    return reasons or ["Matched the capture's searchable content"]


def _capture_category(parts: list[dict]) -> tuple[str, str | None]:
    for part in parts:
        if part.get("user_category"):
            return str(part["user_category"]), str(part["user_category"])
    generic = {
        "Uncategorised",
        "Images & Media",
        "Documents",
        "Links & References",
    }
    for part in parts:
        category = part.get("category")
        if category and category not in generic:
            return str(category), None
    for part in parts:
        category = part.get("category")
        if category:
            return str(category), None
    return "Uncategorised", None


def _combine_capture(parts: list[dict], query: str) -> dict:
    parts.sort(
        key=lambda item: (
            int(item.get("capture_position") or 0),
            item.get("date_utc") or "",
            int(item.get("message_id") or 0),
        )
    )
    root = dict(parts[0])
    latest = max(
        parts,
        key=lambda item: (
            item.get("date_utc") or "",
            int(item.get("message_id") or 0),
        ),
    )
    category, user_category = _capture_category(parts)

    def unique_values(field: str) -> list[str]:
        values = []
        for part in parts:
            value = (part.get(field) or "").strip()
            if value and value not in values:
                values.append(value)
        return values

    urls = []
    link_metadata = []
    media_items = []
    notes = []
    for part in parts:
        try:
            part_urls = json.loads(part.get("urls_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            part_urls = []
        for url in part_urls if isinstance(part_urls, list) else []:
            if isinstance(url, str) and url not in urls:
                urls.append(url)
        try:
            metadata = json.loads(part.get("link_metadata_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            metadata = []
        for item in metadata if isinstance(metadata, list) else []:
            if isinstance(item, dict):
                link_metadata.append(item)
        if part.get("media_path"):
            media_items.append(
                {
                    "id": part["id"],
                    "path": part["media_path"],
                    "media_type": part.get("media_type"),
                    "file_name": part.get("file_name"),
                    "mime_type": part.get("mime_type"),
                }
            )
        note = (part.get("note") or "").strip()
        if note and note not in notes:
            notes.append(note)

    statuses = {part.get("content_status") or "pending" for part in parts}
    if statuses & {"pending", "processing"}:
        content_status = "indexing"
    elif statuses == {"ready"}:
        content_status = "ready"
    elif statuses == {"failed"}:
        content_status = "failed"
    else:
        content_status = "partial"
    errors = unique_values("content_error")
    expected_parts = sum(
        bool(part.get("media_path"))
        or (part.get("urls_json") or "[]") != "[]"
        for part in parts
    )
    duplicate_count = sum(
        int(part.get("_duplicate_count") or 0) for part in parts
    )
    indexed_parts = sum(
        (part.get("content_status") or "") == "ready" for part in parts
    )

    root.update(
        {
            "date_utc": latest.get("date_utc"),
            "message_id": latest.get("message_id"),
            "root_message_id": parts[0].get("message_id"),
            "message_ids": [part.get("message_id") for part in parts],
            "row_ids": [part.get("id") for part in parts],
            "capture_size": len(parts),
            "text": "\n\n".join(unique_values("text")),
            "vision_text": "\n\n".join(unique_values("vision_text")),
            "extracted_text": "\n\n".join(
                unique_values("extracted_text")
            ),
            "indexed_text": "\n\n".join(unique_values("indexed_text")),
            "urls_json": json.dumps(urls),
            "link_metadata_json": json.dumps(link_metadata),
            "media_items": media_items,
            "media_path": (
                media_items[0]["path"] if media_items else None
            ),
            "media_type": (
                media_items[0]["media_type"] if media_items else None
            ),
            "file_name": (
                media_items[0]["file_name"] if media_items else None
            ),
            "mime_type": (
                media_items[0]["mime_type"] if media_items else None
            ),
            "starred": int(any(part.get("starred") for part in parts)),
            "is_pinned": int(any(part.get("is_pinned") for part in parts)),
            "note": "\n".join(notes),
            "user_category": user_category,
            "category": category,
            "display_category": category,
            "content_status": content_status,
            "content_error": " · ".join(errors),
            "_expected_parts": expected_parts,
            "_indexed_parts": indexed_parts,
            "_duplicate_count": duplicate_count,
            "_match_reasons": _match_reasons(parts, query),
            "_keyword_score": max(
                float(part.get("_keyword_score") or 0) for part in parts
            ),
            "_semantic_score": max(
                float(part.get("_semantic_score") or 0) for part in parts
            ),
            "_score": max(float(part.get("_score") or 0) for part in parts),
        }
    )
    return root


def collapse_captures(
    connection: sqlite3.Connection,
    rows: list[dict],
    query: str,
    *,
    include_sensitive: bool = False,
) -> list[dict]:
    if not rows:
        return []
    ordered_ids = []
    scores = {}
    for row in rows:
        capture_id = row.get("capture_id") or (
            f"{row.get('chat_id')}:{row.get('message_id')}"
        )
        if capture_id not in ordered_ids:
            ordered_ids.append(capture_id)
        scores[row["id"]] = {
            key: row.get(key, 0)
            for key in ("_keyword_score", "_semantic_score", "_score")
        }

    members: list[dict] = []
    for start in range(0, len(ordered_ids), 400):
        batch = ordered_ids[start : start + 400]
        placeholders = ",".join("?" * len(batch))
        sensitivity = "" if include_sensitive else "AND m.is_sensitive = 0"
        fetched = connection.execute(
            f"""
            SELECT m.*, COALESCE(s.starred, 0) AS starred,
                   COALESCE(s.note, '') AS note, s.user_category,
                   COALESCE(s.user_category, m.category)
                       AS display_category
            FROM messages m
            LEFT JOIN item_state s ON s.message_row_id = m.id
            WHERE m.capture_id IN ({placeholders})
              {sensitivity}
              AND m.duplicate_of_id IS NULL
            ORDER BY m.capture_id, m.capture_position, m.message_id
            """,
            batch,
        ).fetchall()
        members.extend(dict(row) for row in fetched)

    member_ids = [member["id"] for member in members]
    duplicate_counts: dict[int, int] = {}
    for start in range(0, len(member_ids), 400):
        batch = member_ids[start : start + 400]
        placeholders = ",".join("?" * len(batch))
        for row in connection.execute(
            f"""
            SELECT duplicate_of_id, COUNT(*) AS n
            FROM messages
            WHERE duplicate_of_id IN ({placeholders})
            GROUP BY duplicate_of_id
            """,
            batch,
        ).fetchall():
            duplicate_counts[int(row["duplicate_of_id"])] = int(row["n"])
    for member in members:
        member["_duplicate_count"] = duplicate_counts.get(member["id"], 0)

    grouped: dict[str, list[dict]] = {}
    for member in members:
        capture_id = member.get("capture_id") or (
            f"{member.get('chat_id')}:{member.get('message_id')}"
        )
        member.update(scores.get(member["id"], {}))
        grouped.setdefault(capture_id, []).append(member)
    return [
        _combine_capture(grouped[capture_id], query)
        for capture_id in ordered_ids
        if capture_id in grouped
    ]


def search(
    connection: sqlite3.Connection,
    config: Settings,
    query: str,
    kind: str = "all",
    limit: int = 30,
    *,
    include_sensitive: bool = False,
    category: str | None = None,
    starred_only: bool = False,
    pinned_only: bool = False,
) -> list[dict]:
    clauses, filter_params = _base_filters(
        kind,
        include_sensitive,
        category,
        starred_only,
        pinned_only,
    )
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    select = """
        SELECT m.*, COALESCE(s.starred, 0) AS starred,
               COALESCE(s.note, '') AS note,
               s.user_category,
               COALESCE(s.user_category, m.category) AS display_category
        FROM messages m
        LEFT JOIN item_state s ON s.message_row_id = m.id
    """

    if not query.strip():
        rows = connection.execute(
            f"""
            {select} {where}
            ORDER BY m.date_utc DESC, m.message_id DESC
            LIMIT ?
            """,
            [*filter_params, max(limit * 4, limit)],
        ).fetchall()
        return collapse_captures(
            connection,
            [dict(row) for row in rows],
            query,
            include_sensitive=include_sensitive,
        )[:limit]

    found: dict[int, dict] = {}
    match_query = _fts_query(query)
    if match_query:
        fts_clauses = ["messages_fts MATCH ?", *clauses]
        fts_where = "WHERE " + " AND ".join(fts_clauses)
        try:
            rows = connection.execute(
                f"""
                SELECT m.*, COALESCE(s.starred, 0) AS starred,
                       COALESCE(s.note, '') AS note,
                       s.user_category,
                       COALESCE(s.user_category, m.category)
                           AS display_category,
                       bm25(messages_fts) AS rank
                FROM messages_fts
                JOIN messages m ON m.id = messages_fts.rowid
                LEFT JOIN item_state s ON s.message_row_id = m.id
                {fts_where}
                ORDER BY rank
                LIMIT ?
                """,
                [match_query, *filter_params, limit * 4],
            ).fetchall()
            for index, row in enumerate(rows):
                item = dict(row)
                item["_keyword_score"] = 1 / (index + 1)
                item["_semantic_score"] = 0.0
                found[item["id"]] = item
        except sqlite3.Error as error:
            set_runtime_state(
                connection,
                "last_search_warning",
                f"FTS fallback used: {error}",
            )

    if not found:
        terms = _query_terms(query)[:8]
        if terms:
            rows = connection.execute(
                f"""
                {select} {where}
                ORDER BY m.date_utc DESC, m.message_id DESC
                """,
                filter_params,
            ).fetchall()
            person_terms = _person_query_terms(query)
            candidates = []
            for row in rows:
                item = dict(row)
                value_terms = {
                    token.lower()
                    for token in re.findall(
                        r"[\w+#.-]+",
                        item.get("indexed_text") or "",
                        re.UNICODE,
                    )
                }
                coverage = sum(
                    1
                    for term in terms
                    if term in value_terms
                    or (
                        len(term) >= 4
                        and any(
                            token.startswith(term)
                            for token in value_terms
                        )
                    )
                )
                if person_terms:
                    if not _matches_people_field(
                        item.get("vision_text"),
                        person_terms,
                    ):
                        continue
                    coverage = max(coverage, len(person_terms))
                elif coverage < min(2, len(terms)):
                    continue
                item["_keyword_score"] = coverage / len(terms)
                item["_semantic_score"] = 0.0
                candidates.append(item)
            candidates.sort(
                key=lambda item: (
                    item["_keyword_score"],
                    item["date_utc"],
                    item["message_id"],
                ),
                reverse=True,
            )
            for item in candidates[: limit * 2]:
                found[item["id"]] = item

    if config.enable_embeddings:
        try:
            query_vector = embed(config, query)
            person_query_terms = _person_query_terms(query)
            semantic_clauses = [
                "m.embedding_json IS NOT NULL",
                "m.embedding_model = ?",
                *clauses,
            ]
            semantic_where = "WHERE " + " AND ".join(semantic_clauses)
            semantic_rows = connection.execute(
                f"{select} {semantic_where}",
                [config.embed_model, *filter_params],
            ).fetchall()
            for row in semantic_rows:
                item = dict(row)
                score = cosine(
                    query_vector,
                    json.loads(item["embedding_json"]),
                )
                if score < SEMANTIC_MIN_SCORE:
                    continue
                if item["id"] not in found and person_query_terms:
                    if item.get("media_type") == "image":
                        matches_person = _matches_people_field(
                            item.get("vision_text"),
                            person_query_terms,
                        )
                    else:
                        matches_person = _covers_person_terms(
                            item.get("indexed_text"),
                            person_query_terms,
                        )
                    if not matches_person:
                        continue
                if item["id"] in found:
                    found[item["id"]]["_semantic_score"] = max(
                        found[item["id"]]["_semantic_score"],
                        score,
                    )
                else:
                    item["_keyword_score"] = 0.0
                    item["_semantic_score"] = score
                    found[item["id"]] = item
        except Exception as error:
            set_runtime_state(
                connection,
                "last_search_warning",
                f"Semantic search skipped: {error}",
            )

    results = list(found.values())
    for item in results:
        item["_score"] = (
            0.62 * max(0, item.get("_keyword_score", 0))
            + 0.38 * max(0, item.get("_semantic_score", 0))
        )
    ordered = sorted(
        results,
        key=lambda item: (
            item["_score"],
            item["date_utc"],
            item["message_id"],
        ),
        reverse=True,
    )
    return collapse_captures(
        connection,
        ordered,
        query,
        include_sensitive=include_sensitive,
    )[:limit]


def set_item_state(
    connection: sqlite3.Connection,
    message_row_id: int,
    *,
    starred: bool | None = None,
    note: str | None = None,
    user_category: str | None = None,
) -> None:
    current = connection.execute(
        "SELECT * FROM item_state WHERE message_row_id = ?",
        (message_row_id,),
    ).fetchone()
    current_starred = bool(current["starred"]) if current else False
    current_note = current["note"] if current else ""
    current_category = current["user_category"] if current else None
    now = datetime.now(timezone.utc).isoformat()

    connection.execute(
        """
        INSERT INTO item_state(
            message_row_id, starred, note, user_category, updated_at
        ) VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(message_row_id) DO UPDATE SET
            starred = excluded.starred,
            note = excluded.note,
            user_category = excluded.user_category,
            updated_at = excluded.updated_at
        """,
        (
            message_row_id,
            int(current_starred if starred is None else starred),
            current_note if note is None else note.strip(),
            current_category if user_category is None else user_category,
            now,
        ),
    )
    connection.commit()


def set_runtime_state(
    connection: sqlite3.Connection,
    key: str,
    value: str | int | float | bool,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for attempt in range(5):
        try:
            connection.execute(
                """
                INSERT INTO runtime_state(key, value, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, str(value), now),
            )
            connection.commit()
            return
        except sqlite3.OperationalError as error:
            connection.rollback()
            if "locked" not in str(error).lower() or attempt == 4:
                raise
            time.sleep(0.2 * (attempt + 1))


def get_runtime_state(
    connection: sqlite3.Connection,
    key: str,
) -> tuple[str | None, str | None]:
    row = connection.execute(
        "SELECT value, updated_at FROM runtime_state WHERE key = ?",
        (key,),
    ).fetchone()
    if not row:
        return None, None
    return row["value"], row["updated_at"]
