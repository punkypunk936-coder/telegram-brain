from __future__ import annotations

import base64
import json
import math
import mimetypes
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests
from dotenv import load_dotenv
from telethon import TelegramClient


load_dotenv()

URL_RE = re.compile(r"https?://[^\s<>\]\)]+", re.IGNORECASE)
METADATA_VERSION = 7

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
    indexed_text TEXT NOT NULL DEFAULT '',
    embedding_json TEXT,
    category TEXT NOT NULL DEFAULT 'Uncategorised',
    is_sensitive INTEGER NOT NULL DEFAULT 0,
    sensitive_reason TEXT,
    metadata_version INTEGER NOT NULL DEFAULT 0,
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
    connection.commit()


def db(path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=15000")
    connection.executescript(SCHEMA)
    _migrate(connection)
    refresh_metadata(connection)
    return connection


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


def describe(config: Settings, path: str) -> str:
    image = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    data = ollama_post(
        f"{config.ollama_url}/api/chat",
        {
            "model": config.vision_model,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Describe this saved image for future search. "
                        "Transcribe visible text accurately, identify meme "
                        "context, objects, brands, tickers, charts and the "
                        "central idea. Be concise and information-dense."
                    ),
                    "images": [image],
                }
            ],
        },
    )
    return data.get("message", {}).get("content", "").strip()


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

    if FINANCIAL_TRANSACTION_RE.search(value):
        return True, "financial transaction"

    if file_name and SENSITIVE_FILE_RE.search(file_name):
        return True, "personal document"

    return False, None


def mask_sensitive_text(text: str | None) -> str:
    value = text or ""
    if not value:
        return ""

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
) -> str:
    combined = " ".join(
        value for value in (text, vision_text, file_name) if value
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


def message_metadata(record: dict) -> dict:
    is_sensitive, reason = sensitive_status(
        record.get("text"),
        record.get("file_name"),
    )
    category = detect_category(
        record.get("text"),
        record.get("vision_text"),
        record.get("file_name"),
        record.get("media_type"),
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
               vision_text, file_name, media_type
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
        "indexed_text",
        "embedding_json",
        "category",
        "is_sensitive",
        "sensitive_reason",
        "metadata_version",
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

    if message.media and not path:
        output = config.media_dir / str(config.chat_id)
        output.mkdir(parents=True, exist_ok=True)
        downloaded = await message.download_media(file=str(output))
        path = str(Path(downloaded).resolve()) if downloaded else None

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
    vision_text = ""

    if (
        config.enable_vision
        and kind == "image"
        and path
        and Path(path).suffix.lower() != ".gif"
    ):
        try:
            vision_text = describe(config, path)
        except Exception as error:
            print("[vision skipped]", message.id, error)

    indexed_text = "\n".join(
        value
        for value in (
            text,
            vision_text,
            file_name or "",
            *urls,
        )
        if value
    )
    embedding_json = None
    if config.enable_embeddings and indexed_text:
        try:
            embedding_json = json.dumps(embed(config, indexed_text))
        except Exception as error:
            print("[embedding skipped]", message.id, error)

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
            "vision_text": vision_text,
            "indexed_text": indexed_text,
            "embedding_json": embedding_json,
        },
    )
    return True


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
    clean = [token.replace('"', "") for token in tokens if token.strip(".-")]
    return " OR ".join(f'"{token}"*' for token in clean[:12])


def _base_filters(
    kind: str,
    include_sensitive: bool,
    category: str | None,
    starred_only: bool,
) -> tuple[list[str], list]:
    clauses = []
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
    return clauses, params


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
) -> list[dict]:
    clauses, filter_params = _base_filters(
        kind,
        include_sensitive,
        category,
        starred_only,
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
            [*filter_params, limit],
        ).fetchall()
        return [dict(row) for row in rows]

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
        terms = re.findall(r"[\w+#.-]+", query.lower(), re.UNICODE)[:8]
        if terms:
            like_clause = " OR ".join(
                "LOWER(m.indexed_text) LIKE ?" for _ in terms
            )
            fallback_clauses = [f"({like_clause})", *clauses]
            fallback_where = "WHERE " + " AND ".join(fallback_clauses)
            rows = connection.execute(
                f"""
                {select} {fallback_where}
                ORDER BY m.date_utc DESC, m.message_id DESC
                LIMIT ?
                """,
                [
                    *(f"%{term}%" for term in terms),
                    *filter_params,
                    limit * 2,
                ],
            ).fetchall()
            for index, row in enumerate(rows):
                item = dict(row)
                item["_keyword_score"] = 1 / (index + 1)
                item["_semantic_score"] = 0.0
                found[item["id"]] = item

    if config.enable_embeddings:
        try:
            query_vector = embed(config, query)
            semantic_clauses = [
                "m.embedding_json IS NOT NULL",
                *clauses,
            ]
            semantic_where = "WHERE " + " AND ".join(semantic_clauses)
            semantic_rows = connection.execute(
                f"{select} {semantic_where}",
                filter_params,
            ).fetchall()
            for row in semantic_rows:
                item = dict(row)
                score = cosine(
                    query_vector,
                    json.loads(item["embedding_json"]),
                )
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
    return sorted(
        results,
        key=lambda item: (
            item["_score"],
            item["date_utc"],
            item["message_id"],
        ),
        reverse=True,
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
