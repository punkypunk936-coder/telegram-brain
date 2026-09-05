from __future__ import annotations

import html
import io
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import streamlit as st
from PIL import Image, ImageOps
from streamlit_paste_button import paste_image_button

from clipboard_utils import ClipboardError, copy_links, copy_media, copy_text
from outbox import (
    enqueue_outbound,
    outbox_counts,
    recent_outbound,
    retry_outbound,
)
from service_control import (
    indexer_snapshot,
    reconcile_services,
    start_content_indexer,
    start_incremental_sync,
    start_watcher,
    stop_content_indexer,
    stop_watcher,
    watcher_snapshot,
)
from tgbrain import (
    VIDEO_VISION_PROMPT_VERSION,
    VISION_PROMPT_VERSION,
    db,
    get_runtime_state,
    is_gif_media,
    mask_sensitive_text,
    search,
    set_item_state,
    settings,
)


st.set_page_config(
    page_title="Telegram Brain",
    page_icon=":material/library_books:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
        :root {
            --brain-line: rgba(128, 128, 128, 0.24);
            --brain-teal: #18a59a;
            --brain-amber: #d8842f;
        }

        .block-container {
            max-width: 1480px;
            padding-top: 2.2rem;
            padding-bottom: 4rem;
        }

        [data-testid="stSidebar"] {
            border-right: 1px solid var(--brain-line);
        }

        [data-testid="stSidebar"] .block-container {
            padding-top: 1.2rem;
        }

        h1, h2, h3 {
            letter-spacing: 0 !important;
        }

        .app-title {
            color: inherit;
            font-size: 1.55rem;
            font-weight: 720;
            line-height: 1.15;
            margin: 0;
        }

        .app-subtitle {
            color: inherit;
            font-size: 0.94rem;
            margin: 0.2rem 0 0.55rem;
            opacity: 0.68;
        }

        .search-label {
            font-size: 0.78rem;
            font-weight: 680;
            margin: 0.35rem 0 0.35rem;
            opacity: 0.72;
        }

        .library-context {
            color: inherit;
            font-size: 0.78rem;
            line-height: 1.4;
            margin: 0.55rem 0 1.15rem;
            opacity: 0.62;
        }

        .result-title {
            color: inherit;
            font-size: 1.05rem;
            font-weight: 680;
            line-height: 1.35;
            margin: 0.1rem 0 0.3rem;
            overflow-wrap: anywhere;
        }

        .result-meta {
            color: inherit;
            font-size: 0.78rem;
            line-height: 1.4;
            margin-bottom: 0.5rem;
            opacity: 0.68;
        }

        .result-count {
            color: inherit;
            font-size: 0.82rem;
            margin: -0.35rem 0 0.8rem;
            opacity: 0.64;
        }

        .match-note {
            font-size: 0.72rem;
            line-height: 1.35;
            margin: 0.15rem 0 0.35rem;
            min-height: 1.95rem;
            opacity: 0.62;
            overflow-wrap: anywhere;
        }

        .filter-summary {
            font-size: 0.78rem;
            line-height: 1.4;
            margin-top: 0.35rem;
            opacity: 0.62;
        }

        .sync-online {
            color: var(--brain-teal);
            font-size: 0.84rem;
            font-weight: 650;
        }

        .sync-offline {
            color: var(--brain-amber);
            font-size: 0.84rem;
            font-weight: 650;
        }

        .status-strip {
            align-items: center;
            border-bottom: 1px solid var(--brain-line);
            display: flex;
            flex-wrap: wrap;
            gap: 0.55rem 1.1rem;
            margin: 0.1rem 0 0.75rem;
            padding: 0 0 0.6rem;
        }

        .status-item {
            align-items: center;
            display: inline-flex;
            font-size: 0.75rem;
            gap: 0.38rem;
            opacity: 0.72;
        }

        .status-dot {
            background: var(--brain-amber);
            border-radius: 50%;
            display: inline-block;
            height: 0.45rem;
            width: 0.45rem;
        }

        .status-dot.online {
            background: var(--brain-teal);
        }

        .section-title {
            font-size: 1.02rem;
            font-weight: 680;
            margin: 1.45rem 0 0.18rem;
            overflow-wrap: anywhere;
        }

        .section-subtitle {
            font-size: 0.82rem;
            line-height: 1.45;
            margin: 0 0 0.75rem;
            opacity: 0.64;
        }

        .pinned-section {
            border-left: 3px solid var(--brain-amber);
            margin-top: 1.45rem;
            padding-left: 0.75rem;
        }

        .pinned-title {
            font-size: 1.02rem;
            font-weight: 700;
            line-height: 1.3;
            margin: 0;
        }

        .pinned-preview {
            font-size: 0.86rem;
            line-height: 1.45;
            margin: 0.35rem 0 0.65rem;
            min-height: 3.75rem;
            opacity: 0.78;
            overflow-wrap: anywhere;
        }

        .latest-preview {
            font-size: 0.94rem;
            line-height: 1.55;
            margin-top: 0.45rem;
            opacity: 0.84;
        }

        .bucket-count {
            font-size: 1.32rem;
            font-weight: 650;
            line-height: 1.15;
            margin: 0.35rem 0 0.2rem;
        }

        .bucket-description {
            font-size: 0.8rem;
            line-height: 1.4;
            min-height: 2.3rem;
            opacity: 0.7;
        }

        .bucket-latest {
            min-height: 2.7rem;
            font-size: 0.85rem;
            line-height: 1.35;
            margin: 0.4rem 0 0.65rem;
            overflow-wrap: anywhere;
        }

        .review-count {
            color: var(--brain-amber);
            font-size: 2rem;
            font-weight: 720;
            line-height: 1;
            margin: 0.1rem 0 0.35rem;
        }

        .review-copy {
            font-size: 0.9rem;
            line-height: 1.5;
            opacity: 0.78;
        }

        .action-label {
            font-size: 0.72rem;
            font-weight: 680;
            margin: 0.85rem 0 0.4rem;
            opacity: 0.62;
            text-transform: uppercase;
        }

        .link-title {
            font-size: 0.9rem;
            font-weight: 650;
            line-height: 1.35;
            margin-top: 0.1rem;
            overflow-wrap: anywhere;
        }

        .link-url {
            font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            font-size: 0.72rem;
            line-height: 1.35;
            margin-top: 0.18rem;
            opacity: 0.62;
            overflow-wrap: anywhere;
        }

        .link-description {
            font-size: 0.8rem;
            line-height: 1.4;
            margin-top: 0.2rem;
            opacity: 0.72;
        }

        .result-divider {
            border-top: 1px solid var(--brain-line);
            margin: 0.8rem 0 0.2rem;
        }

        .library-heading {
            align-items: baseline;
            display: flex;
            gap: 0.7rem;
            margin: 0.9rem 0 0.55rem;
        }

        .library-heading strong {
            font-size: 1.02rem;
            font-weight: 700;
        }

        .library-heading span {
            font-size: 0.78rem;
            opacity: 0.58;
        }

        .feed-intro {
            align-items: baseline;
            display: flex;
            flex-wrap: wrap;
            gap: 0.35rem 0.8rem;
            margin: 1rem 0 0.55rem;
        }

        .feed-intro strong {
            font-size: 1.08rem;
            font-weight: 710;
        }

        .feed-intro span {
            font-size: 0.8rem;
            opacity: 0.6;
        }

        .feed-copy {
            font-size: 0.94rem;
            line-height: 1.55;
            margin: 0.45rem 0 0.65rem;
            white-space: pre-wrap;
        }

        .feed-visual-note {
            font-size: 0.8rem;
            line-height: 1.4;
            margin: 0.25rem 0 0.55rem;
            opacity: 0.66;
        }

        .detail-kicker {
            font-size: 0.72rem;
            font-weight: 700;
            margin-bottom: 0.35rem;
            opacity: 0.58;
            text-transform: uppercase;
        }

        .detail-copy {
            font-size: 0.92rem;
            line-height: 1.52;
            margin: 0.45rem 0 0.8rem;
            opacity: 0.84;
            overflow-wrap: anywhere;
        }

        .asset-file {
            border-bottom: 1px solid var(--brain-line);
            padding: 0.65rem 0 0.75rem;
        }

        .composer-title {
            font-size: 1.35rem;
            font-weight: 720;
            line-height: 1.2;
            margin: 1.4rem 0 0.25rem;
        }

        .composer-subtitle {
            font-size: 0.88rem;
            line-height: 1.5;
            margin-bottom: 1rem;
            max-width: 680px;
            opacity: 0.66;
        }

        .delivery-status {
            border-top: 1px solid var(--brain-line);
            font-size: 0.82rem;
            margin-top: 1.2rem;
            padding-top: 0.85rem;
        }

        .delivery-sent {
            color: var(--brain-teal);
            font-weight: 680;
        }

        .delivery-pending {
            color: var(--brain-amber);
            font-weight: 680;
        }

        .asset-file-title {
            font-size: 0.94rem;
            font-weight: 650;
            line-height: 1.35;
            overflow-wrap: anywhere;
        }

        .asset-file-meta {
            font-size: 0.76rem;
            margin-top: 0.18rem;
            opacity: 0.58;
        }

        div[data-testid="stImage"] img {
            border-radius: 2px;
        }

        div[data-testid="stImage"] {
            margin-bottom: -0.55rem;
        }

        div[data-testid="stHorizontalBlock"] {
            gap: 0.48rem;
        }

        [data-testid="stSegmentedControl"] {
            margin-bottom: 0.2rem;
        }

        [data-testid="stSegmentedControl"] button {
            min-height: 2.35rem;
        }

        [class*="st-key-grid-copy-"] button,
        [class*="st-key-grid-open-"] button {
            min-height: 2rem;
            padding-bottom: 0.2rem;
            padding-top: 0.2rem;
        }

        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 6px;
            border-color: var(--brain-line);
        }

        .stButton button,
        .stDownloadButton button,
        .stLinkButton a {
            border-radius: 6px;
        }

        [data-testid="stTextInputRootElement"] {
            border-radius: 6px;
        }

        [data-testid="stTextInputRootElement"] input {
            font-size: 1rem;
        }

        [data-testid="stSidebar"] [data-testid="stExpander"] {
            border-left: 0;
            border-right: 0;
            border-radius: 0;
        }

        @media (max-width: 720px) {
            .block-container {
                padding-top: 3.7rem;
                padding-left: 0.75rem;
                padding-right: 0.75rem;
            }

            [data-testid="stHorizontalBlock"]:has(> div:nth-child(6)) {
                flex-wrap: wrap;
            }

            [data-testid="stHorizontalBlock"]:has(> div:nth-child(6)) > div {
                flex: 1 1 calc(33.333% - 0.5rem) !important;
                min-width: calc(33.333% - 0.5rem) !important;
            }

            [data-testid="stHorizontalBlock"]:has(> div:nth-child(6))
            [data-testid="stHorizontalBlock"] {
                flex-wrap: nowrap;
            }

            [data-testid="stHorizontalBlock"]:has(> div:nth-child(6))
            [data-testid="stHorizontalBlock"] > div {
                flex: 1 1 50% !important;
                min-width: 0 !important;
            }

            .app-title {
                font-size: 1.3rem;
            }

            .status-strip {
                gap: 0.45rem 0.85rem;
            }

            .link-url {
                font-size: 0.68rem;
            }
        }
    </style>
    """,
    unsafe_allow_html=True,
)


ORGANISE_TOPICS = [
    "Trading & Markets",
    "Technology & AI",
    "Work & Career",
    "Writing",
    "Research & Learning",
    "Ideas & Building",
    "Memes & Culture",
    "Personal & Admin",
    "Links & References",
    "Documents",
    "Images & Media",
    "Uncategorised",
]

PRIMARY_TOPIC_ORDER = [
    "Trading & Markets",
    "Technology & AI",
    "Work & Career",
    "Writing",
    "Research & Learning",
    "Ideas & Building",
    "Memes & Culture",
    "Personal & Admin",
]

def reset_page() -> None:
    st.session_state.page = 1
    st.session_state.feed_limit = 12
    st.session_state.selected_library_item = None


def open_pinned() -> None:
    st.session_state.library_view = "Feed"
    st.session_state.topic_filter = "All topics"
    st.session_state.scope_filter = "Pinned"
    st.session_state.search_query = ""
    st.session_state.page = 1


def parse_urls(row: dict) -> list[str]:
    try:
        values = json.loads(row.get("urls_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [value for value in values if isinstance(value, str)]


def parse_link_metadata(row: dict) -> list[dict]:
    try:
        values = json.loads(row.get("link_metadata_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [value for value in values if isinstance(value, dict)]


def row_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def clean_preview(value: str, limit: int = 520) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def visual_preview(value: str) -> str:
    if not value.strip():
        return ""
    fields = {}
    for label in ("summary", "people", "meme context"):
        match = re.search(
            rf"(?im)^{re.escape(label)}\s*:\s*(.+)$",
            value,
        )
        if match:
            fields[label] = match.group(1).strip()
    pieces = []
    people = fields.get("people", "")
    if people.lower() not in {"", "none", "unknown"}:
        pieces.append(people)
    if fields.get("summary"):
        pieces.append(fields["summary"])
    if fields.get("meme context", "").lower() not in {
        "",
        "none",
        "not applicable",
    }:
        pieces.append(fields["meme context"])
    return clean_preview(" · ".join(pieces) or value, 420)


def result_title(row: dict) -> str:
    text = (row.get("text") or "").strip()
    if text:
        first_line = next(
            (line.strip() for line in text.splitlines() if line.strip()),
            "",
        )
        first_line = re.sub(r"^#{1,6}\s+", "", first_line)
        if first_line.startswith(("http://", "https://")):
            metadata_title = next(
                (
                    item.get("title")
                    for item in parse_link_metadata(row)
                    if item.get("title")
                ),
                None,
            )
            if metadata_title:
                return clean_preview(str(metadata_title), 100)
            host = urlparse(first_line).netloc.replace("www.", "")
            return f"Link from {host}" if host else "Saved link"
        return clean_preview(first_line, 100)
    note = (row.get("note") or "").strip()
    if note:
        return clean_preview(note, 100)
    metadata_title = next(
        (
            item.get("title")
            for item in parse_link_metadata(row)
            if item.get("title")
        ),
        None,
    )
    if metadata_title:
        return clean_preview(str(metadata_title), 100)
    if row.get("file_name"):
        return row["file_name"]
    kind = row.get("media_type")
    return f"Saved {kind}" if kind else "Saved message"


def media_copy_label(items: list[dict]) -> str:
    if len(items) != 1:
        return f"Copy {len(items)} media"
    item = items[0]
    media_type = item.get("media_type")
    suffix = item["_path"].suffix.lower()
    if suffix == ".gif":
        return "Copy GIF"
    if media_type == "image":
        return "Copy image"
    if media_type == "video":
        return "Copy video"
    if media_type == "audio":
        return "Copy audio"
    return "Copy file"


def existing_media_items(row: dict) -> list[dict]:
    items = row.get("media_items") or []
    if not items and row.get("media_path"):
        items = [
            {
                "id": row["id"],
                "path": row["media_path"],
                "media_type": row.get("media_type"),
                "file_name": row.get("file_name"),
                "mime_type": row.get("mime_type"),
            }
        ]
    return [
        {**item, "_path": Path(item["path"])}
        for item in items
        if item.get("path") and Path(item["path"]).exists()
    ]


def result_copy_text(row: dict) -> tuple[str, str]:
    original = mask_sensitive_text(row.get("text") or "").strip()
    if original:
        return original, "Copy text"
    extracted = mask_sensitive_text(
        row.get("extracted_text") or ""
    ).strip()
    if extracted:
        return extracted, "Copy indexed text"
    return "", ""


def link_details(row: dict) -> list[dict]:
    urls = parse_urls(row)
    metadata = parse_link_metadata(row)
    details = []
    for index, url in enumerate(urls):
        match = next(
            (
                item
                for item in metadata
                if item.get("url") == url
            ),
            metadata[index] if index < len(metadata) else {},
        )
        host = urlparse(url).netloc.replace("www.", "") or "Saved link"
        details.append(
            {
                "url": url,
                "host": host,
                "title": (
                    str(match.get("title") or "").strip()
                    or f"Link from {host}"
                ),
                "description": str(
                    match.get("description") or ""
                ).strip(),
            }
        )
    return details


def clipboard_error(error: ClipboardError) -> None:
    st.toast(str(error), icon=":material/error:")


def render_copy_actions(row: dict, key_prefix: str) -> None:
    copyable_text, text_label = result_copy_text(row)
    media_items = existing_media_items(row)
    urls = parse_urls(row)
    actions = []
    if copyable_text:
        actions.append(("text", text_label))
    if media_items:
        actions.append(("media", media_copy_label(media_items)))
    if len(urls) > 1:
        actions.append(("links", f"Copy all {len(urls)} links"))
    if not actions:
        return

    columns = st.columns(len(actions))
    for column, (action, label) in zip(columns, actions):
        with column:
            if not st.button(
                label,
                icon=":material/content_copy:",
                key=f"{key_prefix}-{action}",
                width="stretch",
            ):
                continue
            try:
                if action == "text":
                    copy_text(copyable_text)
                    st.toast(
                        "Text copied exactly as saved.",
                        icon=":material/check_circle:",
                    )
                elif action == "media":
                    copied = copy_media(
                        item["_path"] for item in media_items
                    )
                    noun = "asset" if copied == 1 else "assets"
                    st.toast(
                        f"{copied} media {noun} copied.",
                        icon=":material/check_circle:",
                    )
                else:
                    copied = copy_links(urls)
                    st.toast(
                        f"{copied} links copied in their original order.",
                        icon=":material/check_circle:",
                    )
            except ClipboardError as error:
                clipboard_error(error)


def render_link_actions(row: dict, key_prefix: str) -> None:
    details = link_details(row)
    if not details:
        return
    st.markdown(
        '<div class="action-label">Links</div>',
        unsafe_allow_html=True,
    )
    for index, item in enumerate(details[:5]):
        if index:
            st.markdown(
                '<div class="result-divider"></div>',
                unsafe_allow_html=True,
            )
        st.markdown(
            '<div class="link-title">'
            + html.escape(item["title"])
            + "</div>"
            + '<div class="link-url">'
            + html.escape(item["url"])
            + "</div>"
            + (
                '<div class="link-description">'
                + html.escape(clean_preview(item["description"], 180))
                + "</div>"
                if item["description"]
                else ""
            ),
            unsafe_allow_html=True,
        )
        copy_column, open_column = st.columns(2)
        with copy_column:
            if st.button(
                "Copy link",
                icon=":material/link:",
                key=f"{key_prefix}-copy-link-{index}",
                width="stretch",
            ):
                try:
                    copy_links([item["url"]])
                    st.toast(
                        "Link copied.",
                        icon=":material/check_circle:",
                    )
                except ClipboardError as error:
                    clipboard_error(error)
        with open_column:
            st.link_button(
                "Open link",
                item["url"],
                icon=":material/open_in_new:",
                width="stretch",
            )
    if len(details) > 5:
        st.caption(
            f"{len(details) - 5} additional links are included in "
            '"Copy all links."'
        )


def index_label(row: dict) -> tuple[str, str]:
    status = row.get("content_status") or "indexing"
    if status == "ready":
        return "Fully indexed", "green"
    if status == "indexing":
        return "Still indexing", "blue"
    if status == "partial":
        return "Partially indexed", "orange"
    return "Index needs attention", "red"


def service_age(timestamp: str | None) -> str:
    if not timestamp:
        return "not yet"
    try:
        then = datetime.fromisoformat(timestamp)
        seconds = max(
            0,
            int((datetime.now(timezone.utc) - then).total_seconds()),
        )
    except ValueError:
        return "recently"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


LIBRARY_VIEWS = ("Feed", "Media", "Videos", "GIFs", "Links", "Files", "Voice")
GALLERY_PAGE_SIZE = 36
LIST_PAGE_SIZE = 18


def _is_gif_item(item: dict) -> bool:
    path = Path(str(item.get("path") or item.get("media_path") or ""))
    return is_gif_media(
        item.get("media_type"),
        item.get("file_name") or path.name,
        item.get("mime_type"),
    )


def row_matches_view(row: dict, view: str) -> bool:
    media_items = row.get("media_items") or []
    if not media_items and row.get("media_path"):
        media_items = [row]
    if view == "Media":
        return any(
            str(item.get("media_type") or "").lower() == "image"
            and not _is_gif_item(item)
            for item in media_items
        )
    if view == "GIFs":
        return any(_is_gif_item(item) for item in media_items)
    if view == "Videos":
        return any(
            str(item.get("media_type") or "").lower() == "video"
            and not _is_gif_item(item)
            for item in media_items
        )
    if view == "Files":
        return any(
            str(item.get("media_type") or "").lower() in {"pdf", "document"}
            for item in media_items
        )
    if view == "Voice":
        return any(
            str(item.get("media_type") or "").lower() == "audio"
            for item in media_items
        )
    if view == "Links":
        return bool(parse_urls(row))
    return True


def _view_sql(view: str) -> str:
    if view == "Media":
        return """
            m.media_type = 'image'
            AND LOWER(COALESCE(m.file_name, '')) NOT LIKE '%.gif'
            AND LOWER(COALESCE(m.mime_type, '')) != 'image/gif'
        """
    if view == "GIFs":
        return """
            (LOWER(COALESCE(m.file_name, '')) LIKE '%.gif'
             OR LOWER(COALESCE(m.file_name, '')) LIKE '%.gif.%'
             OR LOWER(COALESCE(m.mime_type, '')) = 'image/gif')
        """
    if view == "Videos":
        return """
            m.media_type = 'video'
            AND LOWER(COALESCE(m.file_name, '')) NOT LIKE '%.gif.%'
        """
    if view == "Files":
        return "m.media_type IN ('pdf', 'document')"
    if view == "Voice":
        return "m.media_type = 'audio'"
    return "COALESCE(m.urls_json, '[]') != '[]'"


def browse_library(
    connection,
    *,
    view: str,
    scope: str,
    topic: str,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    clauses = [
        "m.is_sensitive = 0",
        "m.duplicate_of_id IS NULL",
        _view_sql(view),
    ]
    params: list[object] = []
    if scope == "Pinned":
        clauses.append("m.is_pinned = 1")
    elif scope == "Saved":
        clauses.append("COALESCE(s.starred, 0) = 1")
    if topic and topic != "All topics":
        clauses.append("COALESCE(s.user_category, m.category) = ?")
        params.append(topic)
    where = " AND ".join(f"({clause.strip()})" for clause in clauses)
    total = connection.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM messages m
        LEFT JOIN item_state s ON s.message_row_id = m.id
        WHERE {where}
        """,
        params,
    ).fetchone()["n"]
    rows = connection.execute(
        f"""
        SELECT m.*, COALESCE(s.starred, 0) AS starred,
               COALESCE(s.note, '') AS note, s.user_category,
               COALESCE(s.user_category, m.category) AS display_category
        FROM messages m
        LEFT JOIN item_state s ON s.message_row_id = m.id
        WHERE {where}
        ORDER BY m.date_utc DESC, m.message_id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()
    return [dict(row) for row in rows], int(total or 0)


def search_library(
    connection,
    config,
    *,
    query: str,
    view: str,
    scope: str,
    topic: str,
) -> list[dict]:
    kind = {
        "Media": "image",
        "Videos": "video",
        "GIFs": "video",
        "Voice": "audio",
    }.get(view, "all")
    rows = search(
        connection,
        config,
        query,
        kind,
        400,
        include_sensitive=False,
        category=topic,
        starred_only=scope == "Saved",
        pinned_only=scope == "Pinned",
    )
    return [row for row in rows if row_matches_view(row, view)]


@st.cache_data(show_spinner=False, max_entries=768)
def gallery_thumbnail(path_string: str, modified_ns: int, edge: int = 360) -> bytes | None:
    del modified_ns
    try:
        with Image.open(path_string) as source:
            source.seek(0)
            source.draft("RGB", (edge, edge))
            image = ImageOps.exif_transpose(source).convert("RGB")
            square = ImageOps.fit(
                image,
                (edge, edge),
                method=Image.Resampling.BILINEAR,
                centering=(0.5, 0.5),
            )
            output = io.BytesIO()
            square.save(output, format="JPEG", quality=80)
            return output.getvalue()
    except (OSError, ValueError):
        return None


def load_library_row(connection, row_id: int | None) -> dict | None:
    if not row_id:
        return None
    row = connection.execute(
        """
        SELECT m.*, COALESCE(s.starred, 0) AS starred,
               COALESCE(s.note, '') AS note, s.user_category,
               COALESCE(s.user_category, m.category) AS display_category
        FROM messages m
        LEFT JOIN item_state s ON s.message_row_id = m.id
        WHERE m.id = ? AND m.is_sensitive = 0
        """,
        (row_id,),
    ).fetchone()
    return dict(row) if row else None


def select_library_item(row_id: int) -> None:
    st.session_state.selected_library_item = row_id


def clear_library_item() -> None:
    st.session_state.selected_library_item = None


def _copy_one_media(row: dict) -> None:
    items = existing_media_items(row)
    if not items:
        st.toast("The original media file is unavailable.", icon=":material/error:")
        return
    try:
        copied = copy_media(item["_path"] for item in items)
        st.toast(
            "Media copied." if copied == 1 else f"{copied} media assets copied.",
            icon=":material/check_circle:",
        )
    except ClipboardError as error:
        clipboard_error(error)


def render_library_pagination(total: int, page_size: int, key_prefix: str) -> None:
    total_pages = max(1, math.ceil(total / page_size))
    st.session_state.page = min(max(1, st.session_state.page), total_pages)
    if total_pages <= 1:
        return
    left, middle, right = st.columns([1, 3, 1], vertical_alignment="center")
    with left:
        if st.button(
            "",
            icon=":material/arrow_back:",
            help="Previous page",
            disabled=st.session_state.page <= 1,
            key=f"{key_prefix}-previous",
            width="stretch",
        ):
            st.session_state.page -= 1
            clear_library_item()
            st.rerun()
    with middle:
        st.caption(f"{st.session_state.page} / {total_pages}")
    with right:
        if st.button(
            "",
            icon=":material/arrow_forward:",
            help="Next page",
            disabled=st.session_state.page >= total_pages,
            key=f"{key_prefix}-next",
            width="stretch",
        ):
            st.session_state.page += 1
            clear_library_item()
            st.rerun()


def render_selected_item(connection, row: dict) -> None:
    title = result_title(row)
    text = mask_sensitive_text(
        row.get("text") or row.get("extracted_text") or ""
    ).strip()
    vision = mask_sensitive_text(row.get("vision_text") or "").strip()
    media_items = existing_media_items(row)
    with st.container(border=True):
        close_col, title_col = st.columns([1, 14], vertical_alignment="center")
        with close_col:
            st.button(
                "",
                icon=":material/close:",
                help="Close preview",
                on_click=clear_library_item,
                key=f"detail-close-{row['id']}",
            )
        with title_col:
            st.markdown(
                '<div class="detail-kicker">Selected item</div>'
                f'<div class="result-title">{html.escape(title)}</div>',
                unsafe_allow_html=True,
            )
        visual, details = st.columns([1.15, 1.85], vertical_alignment="top")
        with visual:
            if media_items:
                first = media_items[0]
                if first.get("media_type") == "image":
                    st.image(str(first["_path"]), width="stretch")
                elif first.get("media_type") == "video":
                    st.video(str(first["_path"]), autoplay=False, loop=True)
                elif first.get("media_type") == "audio":
                    st.audio(str(first["_path"]), autoplay=False)
                else:
                    st.markdown(
                        '<div class="asset-file-title">'
                        + html.escape(first.get("file_name") or first["_path"].name)
                        + "</div>",
                        unsafe_allow_html=True,
                    )
        with details:
            if row.get("is_pinned"):
                st.badge("Pinned", color="orange")
            st.badge(row.get("display_category") or "Uncategorised", color="gray")
            if text:
                st.markdown(
                    '<div class="detail-copy">'
                    + html.escape(clean_preview(text, 900))
                    + "</div>",
                    unsafe_allow_html=True,
                )
            elif vision:
                st.markdown(
                    '<div class="detail-copy">'
                    + html.escape(visual_preview(vision))
                    + "</div>",
                    unsafe_allow_html=True,
                )
            actions = st.columns(3)
            with actions[0]:
                if st.button(
                    "Copy",
                    icon=":material/content_copy:",
                    key=f"detail-copy-{row['id']}",
                    width="stretch",
                ):
                    if media_items:
                        _copy_one_media(row)
                    else:
                        copyable, _ = result_copy_text(row)
                        try:
                            copy_text(copyable)
                            st.toast("Copied.", icon=":material/check_circle:")
                        except ClipboardError as error:
                            clipboard_error(error)
            with actions[1]:
                if st.button(
                    "Saved" if row.get("starred") else "Save",
                    icon=(
                        ":material/star:"
                        if row.get("starred")
                        else ":material/star_outline:"
                    ),
                    key=f"detail-save-{row['id']}",
                    width="stretch",
                ):
                    set_item_state(
                        connection,
                        row["id"],
                        starred=not bool(row.get("starred")),
                    )
                    st.rerun()
            with actions[2]:
                urls = parse_urls(row)
                if urls:
                    st.link_button(
                        "Open link",
                        urls[0],
                        icon=":material/open_in_new:",
                        width="stretch",
                    )
        render_link_actions(row, f"detail-{row['id']}")
        if vision or row.get("extracted_text"):
            with st.expander("Indexed context"):
                if vision:
                    label = (
                        "Video understanding"
                        if row.get("media_type") == "video"
                        else "Image understanding"
                    )
                    st.markdown(f"**{label}**")
                    st.write(clean_preview(vision, 1400))
                if row.get("extracted_text"):
                    st.markdown("**Extracted content**")
                    st.write(
                        clean_preview(
                            mask_sensitive_text(row["extracted_text"]),
                            1400,
                        )
                    )
        with st.expander("Help me find this again"):
            current_category = (
                row.get("user_category")
                or row.get("category")
                or "Uncategorised"
            )
            if current_category not in ORGANISE_TOPICS:
                current_category = "Uncategorised"
            user_category = st.selectbox(
                "Topic",
                ORGANISE_TOPICS,
                index=ORGANISE_TOPICS.index(current_category),
                key=f"detail-category-{row['id']}",
            )
            user_note = st.text_area(
                "Search words, names or a short note",
                value=row.get("note") or "",
                key=f"detail-note-{row['id']}",
                height=88,
                placeholder="Add the words you are likely to search later",
            )
            if st.button(
                "Save changes",
                icon=":material/check:",
                key=f"detail-organise-{row['id']}",
            ):
                set_item_state(
                    connection,
                    row["id"],
                    note=user_note,
                    user_category=user_category,
                )
                st.toast("Saved and searchable.")
                st.rerun()


def render_media_grid(
    rows: list[dict],
    view: str,
    *,
    explain_matches: bool = False,
) -> None:
    assets: list[tuple[dict, dict]] = []
    for row in rows:
        for item in existing_media_items(row):
            if view == "Media" and item.get("media_type") == "image" and not _is_gif_item(item):
                assets.append((row, item))
            elif view == "GIFs" and _is_gif_item(item):
                assets.append((row, item))
            elif (
                view == "Videos"
                and item.get("media_type") == "video"
                and not _is_gif_item(item)
            ):
                assets.append((row, item))
    column_count = 3 if explain_matches else (6 if view == "Media" else 3)
    for row_start in range(0, len(assets), column_count):
        asset_row = assets[row_start : row_start + column_count]
        columns = st.columns(column_count, gap="small")
        for column, (row, item) in zip(columns, asset_row):
            with column:
                if view == "Media":
                    path = item["_path"]
                    thumbnail = gallery_thumbnail(
                        str(path),
                        path.stat().st_mtime_ns,
                    )
                    if thumbnail:
                        st.image(thumbnail, width="stretch")
                    else:
                        st.image(str(path), width="stretch")
                elif item["_path"].suffix.lower() == ".gif":
                    st.image(str(item["_path"]), width="stretch")
                else:
                    st.video(
                        str(item["_path"]),
                        autoplay=False,
                        loop=view == "GIFs",
                    )
                if explain_matches:
                    reasons = row.get("_match_reasons") or []
                    reason = next(
                        (
                            item
                            for item in reasons
                            if item.startswith("Possible match")
                        ),
                        next(iter(reasons), "Matched searchable content"),
                    )
                    st.markdown(
                        '<div class="match-note">'
                        + html.escape(reason)
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                copy_col, open_col = st.columns(2, gap="small")
                with copy_col:
                    if st.button(
                        "",
                        icon=":material/content_copy:",
                        help=f"Copy {view.lower()[:-1] if view.endswith('s') else view.lower()}",
                        key=f"grid-copy-{view}-{row['id']}-{item['id']}",
                        width="stretch",
                    ):
                        _copy_one_media({**row, "media_items": [item]})
                with open_col:
                    st.button(
                        "",
                        icon=":material/open_in_full:",
                        help="Open details",
                        key=f"grid-open-{view}-{row['id']}-{item['id']}",
                        on_click=select_library_item,
                        args=(item.get("id") or row["id"],),
                        width="stretch",
                    )


def render_library_list(rows: list[dict], view: str) -> None:
    for row in rows:
        media_items = existing_media_items(row)
        title = result_title(row)
        text = mask_sensitive_text(row.get("text") or "").strip()
        if view == "Links":
            details = link_details(row)
            if not details:
                continue
            item = details[0]
            left, copy_col, open_col = st.columns([10, 1, 1], vertical_alignment="center")
            with left:
                st.markdown(
                    '<div class="asset-file">'
                    f'<div class="asset-file-title">{html.escape(item["title"])}</div>'
                    f'<div class="asset-file-meta">{html.escape(item["host"])}</div>'
                    "</div>",
                    unsafe_allow_html=True,
                )
            with copy_col:
                if st.button(
                    "",
                    icon=":material/content_copy:",
                    help="Copy link",
                    key=f"list-copy-link-{row['id']}",
                    width="stretch",
                ):
                    try:
                        copy_links([item["url"]])
                        st.toast("Link copied.", icon=":material/check_circle:")
                    except ClipboardError as error:
                        clipboard_error(error)
            with open_col:
                st.link_button(
                    "",
                    item["url"],
                    icon=":material/open_in_new:",
                    help="Open link",
                    width="stretch",
                )
            continue

        left, copy_col, open_col = st.columns([10, 1, 1], vertical_alignment="center")
        with left:
            file_name = (
                media_items[0].get("file_name")
                if media_items
                else row.get("file_name")
            ) or title
            meta = "Voice note" if view == "Voice" else (row.get("mime_type") or "File")
            st.markdown(
                '<div class="asset-file">'
                f'<div class="asset-file-title">{html.escape(file_name)}</div>'
                f'<div class="asset-file-meta">{html.escape(meta)}</div>'
                + (
                    f'<div class="asset-file-meta">{html.escape(clean_preview(text, 160))}</div>'
                    if text
                    else ""
                )
                + "</div>",
                unsafe_allow_html=True,
            )
            if view == "Voice" and media_items:
                st.audio(str(media_items[0]["_path"]), autoplay=False)
        with copy_col:
            if st.button(
                "",
                icon=":material/content_copy:",
                help="Copy file",
                key=f"list-copy-{view}-{row['id']}",
                width="stretch",
            ):
                _copy_one_media(row)
        with open_col:
            st.button(
                "",
                icon=":material/open_in_full:",
                help="Open details",
                key=f"list-open-{view}-{row['id']}",
                on_click=select_library_item,
                args=(row["id"],),
                width="stretch",
            )


def _pasted_image_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def _reset_composer() -> None:
    st.session_state.composer_version = (
        int(st.session_state.get("composer_version", 0)) + 1
    )
    st.session_state.pasted_image = None
    st.session_state.pasted_image_name = ""


def render_delivery_status(config) -> None:
    @st.fragment(run_every=2)
    def status_fragment() -> None:
        status_connection = db(config.db_path)
        rows = recent_outbound(status_connection, 3)
        counts = outbox_counts(status_connection)
        status_connection.close()
        if not rows:
            return

        pending = counts["queued"] + counts["sending"]
        if pending:
            st.markdown(
                '<div class="delivery-status delivery-pending">'
                f'{pending:,} item{"s" if pending != 1 else ""} on the way'
                "</div>",
                unsafe_allow_html=True,
            )
        elif rows[0]["status"] == "sent":
            st.markdown(
                '<div class="delivery-status delivery-sent">'
                "Delivered to Telegram"
                "</div>",
                unsafe_allow_html=True,
            )

        for row in rows:
            if row["status"] == "failed":
                failure, action = st.columns([6, 1], vertical_alignment="center")
                with failure:
                    st.error(
                        "Telegram could not deliver this item. "
                        + clean_preview(row.get("error") or "Unknown error", 180),
                        icon=":material/error:",
                    )
                with action:
                    if st.button(
                        "Retry",
                        icon=":material/refresh:",
                        key=f"retry-outbound-{row['id']}",
                        width="stretch",
                    ):
                        retry_connection = db(config.db_path)
                        retry_outbound(retry_connection, row["id"])
                        retry_connection.close()
                        st.rerun()

    status_fragment()


def render_telegram_composer(config, connection) -> None:
    if "composer_version" not in st.session_state:
        st.session_state.composer_version = 0
    if "pasted_image" not in st.session_state:
        st.session_state.pasted_image = None
    if "pasted_image_name" not in st.session_state:
        st.session_state.pasted_image_name = ""

    version = int(st.session_state.composer_version)
    st.markdown(
        '<div class="composer-title">Send to Telegram</div>'
        '<div class="composer-subtitle">'
        "Write, paste or attach anything here. Telegram remains the durable "
        "source, and the confirmed message comes back into this searchable "
        "library automatically."
        "</div>",
        unsafe_allow_html=True,
    )

    body = st.text_area(
        "Message",
        placeholder="Write or paste text, links, notes or a caption...",
        height=280,
        key=f"telegram-composer-text-{version}",
        label_visibility="collapsed",
    )

    paste_column, upload_column = st.columns(2, vertical_alignment="top")
    with paste_column:
        st.markdown("**Paste from clipboard**")
        pasted = paste_image_button(
            label="Paste image",
            text_color="#ffffff",
            background_color="#177f78",
            hover_background_color="#126b65",
            key=f"telegram-paste-image-{version}",
            errors="ignore",
        )
        if pasted.image_data is not None:
            st.session_state.pasted_image = _pasted_image_bytes(
                pasted.image_data
            )
            st.session_state.pasted_image_name = "pasted-image.png"
        if st.session_state.pasted_image:
            st.image(
                st.session_state.pasted_image,
                caption="Pasted image ready",
                width=240,
            )
            if st.button(
                "Remove",
                icon=":material/close:",
                key=f"remove-pasted-image-{version}",
            ):
                _reset_composer()
                st.rerun()

    with upload_column:
        st.markdown("**Add media or files**")
        uploads = st.file_uploader(
            "Files",
            accept_multiple_files=True,
            key=f"telegram-composer-files-{version}",
            label_visibility="collapsed",
        )

    attachments: list[tuple[str, str | None, bytes]] = []
    if st.session_state.pasted_image:
        attachments.append(
            (
                st.session_state.pasted_image_name or "pasted-image.png",
                "image/png",
                st.session_state.pasted_image,
            )
        )
    for upload in uploads or []:
        attachments.append((upload.name, upload.type, upload.getvalue()))

    send_column, note_column = st.columns([1.4, 5], vertical_alignment="center")
    with send_column:
        send = st.button(
            "Send to Telegram",
            icon=":material/send:",
            type="primary",
            width="stretch",
        )
    with note_column:
        st.caption(
            "Queued safely if Telegram is reconnecting. Text layout and "
            "original files are preserved."
        )

    if send and not body.strip() and not attachments:
        st.warning(
            "Add text or a file before sending.",
            icon=":material/edit_note:",
        )
    elif send:
        enqueue_outbound(
            connection,
            config,
            text=body,
            attachments=attachments,
        )
        _reset_composer()
        st.toast("Sending to Telegram...", icon=":material/send:")
        st.rerun()

    render_delivery_status(config)


def render_feed_card(
    connection,
    row: dict,
    *,
    key_prefix: str,
    explain_match: bool = False,
) -> None:
    media_items = existing_media_items(row)
    text = mask_sensitive_text(row.get("text") or "").strip()
    extracted_text = mask_sensitive_text(row.get("extracted_text") or "").strip()
    vision_text = mask_sensitive_text(row.get("vision_text") or "").strip()
    title = result_title({**row, "text": text})
    category = row.get("display_category") or row.get("category") or "Uncategorised"

    with st.container(border=True):
        heading, save_action, detail_action = st.columns(
            [12, 1, 1],
            vertical_alignment="top",
        )
        with heading:
            if row.get("is_pinned"):
                st.badge("Pinned", color="orange")
            st.badge(category, color="gray")
            if row.get("content_status") not in {None, "ready"}:
                status_label, status_color = index_label(row)
                st.badge(status_label, color=status_color)
            st.markdown(
                f'<div class="result-title">{html.escape(title)}</div>',
                unsafe_allow_html=True,
            )
        with save_action:
            if st.button(
                "",
                icon=(
                    ":material/star:"
                    if row.get("starred")
                    else ":material/star_outline:"
                ),
                help="Remove from saved" if row.get("starred") else "Save",
                key=f"{key_prefix}-save-{row['id']}",
                width="stretch",
            ):
                set_item_state(
                    connection,
                    row["id"],
                    starred=not bool(row.get("starred")),
                )
                st.rerun()
        with detail_action:
            st.button(
                "",
                icon=":material/open_in_full:",
                help="Open details",
                key=f"{key_prefix}-detail-{row['id']}",
                on_click=select_library_item,
                args=(row["id"],),
                width="stretch",
            )

        images = [
            item
            for item in media_items
            if item.get("media_type") == "image" and not _is_gif_item(item)
        ]
        if images:
            columns = st.columns(min(3, len(images)), gap="small")
            for index, item in enumerate(images[:6]):
                with columns[index % len(columns)]:
                    st.image(str(item["_path"]), width="stretch")

        for item in media_items:
            if item.get("media_type") == "video":
                st.video(
                    str(item["_path"]),
                    autoplay=False,
                    loop=_is_gif_item(item),
                )
            elif _is_gif_item(item):
                st.image(str(item["_path"]), width="stretch")
            elif item.get("media_type") == "audio":
                st.audio(str(item["_path"]), autoplay=False)

        primary_copy = text or extracted_text
        if text:
            first_line = next(
                (line.strip() for line in text.splitlines() if line.strip()),
                "",
            )
            if first_line and primary_copy.startswith(first_line):
                primary_copy = primary_copy[len(first_line) :].strip()
        if primary_copy:
            st.markdown(
                '<div class="feed-copy">'
                + html.escape(clean_preview(primary_copy, 760))
                + "</div>",
                unsafe_allow_html=True,
            )
            if len(primary_copy) > 760:
                with st.expander("Read full text"):
                    st.write(primary_copy)
        elif vision_text:
            st.markdown(
                '<div class="feed-visual-note">'
                + html.escape(visual_preview(vision_text))
                + "</div>",
                unsafe_allow_html=True,
            )

        render_copy_actions(row, key_prefix)
        render_link_actions(row, key_prefix)

        if explain_match:
            with st.expander("Why this appeared"):
                for reason in row.get("_match_reasons") or [
                    "Matched the capture's searchable content"
                ]:
                    st.markdown(f"- {reason}")
                if vision_text:
                    st.caption("Visual index")
                    st.write(clean_preview(vision_text, 900))


def render_library_workspace(
    connection,
    config,
    *,
    available_topics: list[str],
    stats,
    pinned_results: list[dict],
) -> None:
    st.markdown(
        '<div class="search-label">Find something</div>',
        unsafe_allow_html=True,
    )
    query = st.text_input(
        "Search",
        placeholder=(
            "Describe what you remember: a person, scene, meme, video, link or idea"
        ),
        key="search_query",
        label_visibility="collapsed",
        on_change=reset_page,
    )

    view = st.segmented_control(
        "Browse",
        LIBRARY_VIEWS,
        key="library_view",
        format_func=lambda option: {
            "Feed": "All",
            "Media": "Images",
        }.get(option, option),
        width="stretch",
        label_visibility="collapsed",
        on_change=reset_page,
    ) or "Feed"

    current_scope = st.session_state.get("scope_filter", "All")
    current_topic = st.session_state.get("topic_filter", "All topics")
    active_filters = int(current_scope != "All") + int(
        current_topic != "All topics"
    )
    filter_label = "Filters" + (f" ({active_filters})" if active_filters else "")
    with st.popover(
        filter_label,
        icon=":material/tune:",
        width="content",
    ):
        scope = st.segmented_control(
            "Show",
            ["All", "Pinned", "Saved"],
            key="scope_filter",
            on_change=reset_page,
            width="stretch",
        ) or "All"
        topic = st.selectbox(
            "Topic",
            available_topics,
            key="topic_filter",
            on_change=reset_page,
        )
    if active_filters:
        filter_parts = []
        if current_scope != "All":
            filter_parts.append(current_scope)
        if current_topic != "All topics":
            filter_parts.append(current_topic)
        st.markdown(
            '<div class="filter-summary">Showing '
            + html.escape(" · ".join(filter_parts))
            + "</div>",
            unsafe_allow_html=True,
        )

    selected = load_library_row(
        connection,
        st.session_state.get("selected_library_item"),
    )
    if selected:
        render_selected_item(connection, selected)

    if view == "Feed":
        inferred_view = None
        if re.search(r"\b(?:gif|reaction gif)\b", query, re.IGNORECASE):
            inferred_view = "GIFs"
        elif re.search(
            r"\b(?:video|clip|screen recording|reel)\b",
            query,
            re.IGNORECASE,
        ):
            inferred_view = "Videos"
        elif re.search(
            r"\b(?:image|photo|picture|meme|screenshot)\b",
            query,
            re.IGNORECASE,
        ):
            inferred_view = "Media"
        requested_kind = {
            "Media": "image",
            "Videos": "video",
            "GIFs": "video",
        }.get(inferred_view, "all")
        search_limit = (
            max(200, st.session_state.feed_limit + 1)
            if query
            else st.session_state.feed_limit + 1
        )
        rows = search(
            connection,
            config,
            query,
            requested_kind,
            search_limit,
            include_sensitive=False,
            category=topic,
            starred_only=scope == "Saved",
            pinned_only=scope == "Pinned",
        )
        if not query:
            rows.sort(
                key=lambda row: (
                    row_datetime(row["date_utc"]),
                    row["message_id"],
                ),
                reverse=True,
            )

        if query and not inferred_view and rows:
            image_matches = sum(row_matches_view(row, "Media") for row in rows)
            video_matches = sum(row_matches_view(row, "Videos") for row in rows)
            if image_matches / len(rows) >= 0.7:
                inferred_view = "Media"
            elif video_matches / len(rows) >= 0.7:
                inferred_view = "Videos"

        if query and inferred_view:
            visual_rows = [
                row for row in rows if row_matches_view(row, inferred_view)
            ][:36]
            label = {
                "Media": "Image matches",
                "Videos": "Video matches",
                "GIFs": "GIF matches",
            }[inferred_view]
            st.markdown(
                '<div class="feed-intro">'
                f"<strong>{label}</strong>"
                f"<span>{len(visual_rows):,} found</span>"
                "</div>",
                unsafe_allow_html=True,
            )
            if not visual_rows:
                st.info(
                    "No confident visual match yet.",
                    icon=":material/image_search:",
                )
                return
            render_media_grid(
                visual_rows,
                inferred_view,
                explain_matches=True,
            )
            return

        home_feed = (
            not query and scope == "All" and topic == "All topics"
        )
        if home_feed and pinned_results:
            pinned_header, pinned_action = st.columns(
                [8, 1],
                vertical_alignment="center",
            )
            with pinned_header:
                st.markdown(
                    '<div class="pinned-section">'
                    '<div class="pinned-title">Pinned</div>'
                    '<div class="section-subtitle">'
                    "The important items from your Telegram chat."
                    "</div></div>",
                    unsafe_allow_html=True,
                )
            with pinned_action:
                st.button(
                    "View all",
                    icon=":material/push_pin:",
                    key="feed-view-pins",
                    on_click=open_pinned,
                )
            pinned_columns = st.columns(min(3, len(pinned_results)))
            for index, pinned in enumerate(pinned_results[:3]):
                with pinned_columns[index]:
                    with st.container(border=True):
                        st.badge(
                            pinned.get("display_category") or "Uncategorised",
                            color="gray",
                        )
                        st.markdown(
                            '<div class="result-title">'
                            + html.escape(result_title(pinned))
                            + "</div>",
                            unsafe_allow_html=True,
                        )
                        st.button(
                            "Open",
                            icon=":material/open_in_full:",
                            key=f"feed-pin-{pinned['id']}",
                            on_click=select_library_item,
                            args=(pinned["id"],),
                            width="stretch",
                        )
            pinned_ids = {row["id"] for row in pinned_results}
            rows = [row for row in rows if row["id"] not in pinned_ids]

        label = "Search results" if query else "Recent captures"
        subtitle = (
            f"{len(rows):,} matches, ordered by relevance."
            if query
            else "Newest first."
        )
        st.markdown(
            '<div class="feed-intro">'
            f"<strong>{html.escape(label)}</strong>"
            f"<span>{html.escape(subtitle)}</span>"
            "</div>",
            unsafe_allow_html=True,
        )

        visible_rows = rows[: st.session_state.feed_limit]
        if not visible_rows:
            if query and config.enable_vision and int(stats["visual_pending"] or 0):
                st.warning(
                    "No confident match yet. Older images and videos are still "
                    "being read in the background.",
                    icon=":material/image_search:",
                )
            else:
                st.info(
                    "Nothing matches yet. Try fewer words or a broader topic.",
                    icon=":material/search:",
                )
            return

        if query:
            for row in visible_rows:
                render_feed_card(
                    connection,
                    row,
                    key_prefix=f"feed-{row['id']}",
                    explain_match=True,
                )
        else:
            feed_columns = st.columns(2, gap="small")
            for index, row in enumerate(visible_rows):
                with feed_columns[index % 2]:
                    render_feed_card(
                        connection,
                        row,
                        key_prefix=f"feed-{row['id']}",
                    )

        if len(rows) > len(visible_rows):
            if st.button(
                "Load older",
                icon=":material/expand_more:",
                key="feed-load-older",
                width="stretch",
            ):
                st.session_state.feed_limit += 12
                st.rerun()
        return

    page_size = GALLERY_PAGE_SIZE if view in {"Media", "Videos", "GIFs"} else LIST_PAGE_SIZE
    offset = (st.session_state.page - 1) * page_size
    if query:
        all_rows = search_library(
            connection,
            config,
            query=query,
            view=view,
            scope=scope,
            topic=topic,
        )
        total = len(all_rows)
        rows = all_rows[offset : offset + page_size]
    else:
        rows, total = browse_library(
            connection,
            view=view,
            scope=scope,
            topic=topic,
            limit=page_size,
            offset=offset,
        )

    st.markdown(
        '<div class="library-heading">'
        f"<strong>{html.escape(view)}</strong>"
        f"<span>{total:,} {'item' if total == 1 else 'items'}</span>"
        "</div>",
        unsafe_allow_html=True,
    )
    if not rows:
        st.info(
            "Nothing matches this view yet.",
            icon=":material/search:",
        )
        return

    if view in {"Media", "Videos", "GIFs"}:
        render_media_grid(rows, view)
    else:
        render_library_list(rows, view)
    render_library_pagination(total, page_size, f"library-{view.lower()}")


config = settings()
connection = db(config.db_path)

if "page" not in st.session_state:
    st.session_state.page = 1
if "scope_filter" not in st.session_state:
    st.session_state.scope_filter = "All"
if st.session_state.scope_filter not in {"All", "Pinned", "Saved"}:
    st.session_state.scope_filter = "All"
if (
    "app_mode" not in st.session_state
    or st.session_state.app_mode not in {"Find & browse", "Send to Telegram"}
):
    st.session_state.app_mode = "Find & browse"
if "library_view" not in st.session_state:
    st.session_state.library_view = "Feed"
if st.session_state.library_view not in LIBRARY_VIEWS:
    st.session_state.library_view = "Feed"
if "feed_limit" not in st.session_state:
    st.session_state.feed_limit = 12
if "selected_library_item" not in st.session_state:
    st.session_state.selected_library_item = None

reconcile_services(config)


@st.fragment(run_every=20)
def keep_background_services_online() -> None:
    reconcile_services(config)


keep_background_services_online()

stats = connection.execute(
    """
    SELECT
        COUNT(DISTINCT CASE
            WHEN duplicate_of_id IS NULL
            THEN COALESCE(capture_id, chat_id || ':' || message_id)
        END)
            AS total,
        SUM(CASE WHEN duplicate_of_id IS NULL THEN 1 ELSE 0 END)
            AS messages,
        SUM(CASE WHEN is_sensitive = 1 THEN 1 ELSE 0 END) AS sensitive,
        COUNT(DISTINCT CASE
            WHEN is_pinned = 1
             AND is_sensitive = 0
             AND duplicate_of_id IS NULL
            THEN COALESCE(capture_id, chat_id || ':' || message_id)
        END) AS pinned,
        SUM(CASE
            WHEN media_path IS NOT NULL AND duplicate_of_id IS NULL
            THEN 1 ELSE 0
        END) AS media,
        SUM(CASE
            WHEN urls_json != '[]' AND duplicate_of_id IS NULL
            THEN 1 ELSE 0
        END) AS links,
        SUM(CASE
            WHEN content_status = 'ready' AND duplicate_of_id IS NULL
            THEN 1 ELSE 0
        END)
            AS indexed,
        SUM(CASE
            WHEN media_path IS NOT NULL
             AND (
                (
                    media_type = 'image'
                    AND (
                        TRIM(vision_text) = ''
                        OR COALESCE(vision_model, '') != ?
                        OR vision_prompt_version < ?
                    )
                )
                OR (
                    media_type = 'video'
                    AND (
                        TRIM(vision_text) = ''
                        OR COALESCE(vision_model, '') != ?
                        OR vision_prompt_version < ?
                    )
                )
             )
             AND duplicate_of_id IS NULL
            THEN 1 ELSE 0
        END)
            AS visual_pending,
        SUM(CASE WHEN duplicate_of_id IS NOT NULL THEN 1 ELSE 0 END)
            AS duplicates,
        MAX(date_utc) AS latest
    FROM messages
    """,
    (
        config.vision_model,
        VISION_PROMPT_VERSION,
        config.vision_model,
        VIDEO_VISION_PROMPT_VERSION,
    ),
).fetchone()
starred_count = connection.execute(
    """
    SELECT COUNT(*) AS n
    FROM item_state s
    JOIN messages m ON m.id = s.message_row_id
    WHERE s.starred = 1 AND m.duplicate_of_id IS NULL
    """
).fetchone()["n"]

bucket_rows = [
    dict(row)
    for row in connection.execute(
        """
        WITH members AS (
            SELECT
                m.id,
                m.message_id,
                m.date_utc,
                m.text,
                m.media_type,
                m.file_name,
                m.link_metadata_json,
                COALESCE(
                    m.capture_id,
                    m.chat_id || ':' || m.message_id
                ) AS capture_key,
                COALESCE(s.user_category, m.category) AS display_category,
                ROW_NUMBER() OVER (
                    PARTITION BY COALESCE(
                        m.capture_id,
                        m.chat_id || ':' || m.message_id
                    )
                    ORDER BY
                        CASE
                            WHEN s.user_category IS NOT NULL THEN 0
                            WHEN m.category NOT IN (
                                'Uncategorised', 'Images & Media',
                                'Documents', 'Links & References'
                            ) THEN 1
                            ELSE 2
                        END,
                        m.capture_position,
                        m.message_id
                ) AS member_rank,
                MAX(m.date_utc) OVER (
                    PARTITION BY COALESCE(
                        m.capture_id,
                        m.chat_id || ':' || m.message_id
                    )
                ) AS capture_date,
                MAX(m.message_id) OVER (
                    PARTITION BY COALESCE(
                        m.capture_id,
                        m.chat_id || ':' || m.message_id
                    )
                ) AS capture_message_id
            FROM messages m
            LEFT JOIN item_state s ON s.message_row_id = m.id
            WHERE m.is_sensitive = 0
              AND m.duplicate_of_id IS NULL
        ),
        classified AS (
            SELECT
                id,
                capture_message_id AS message_id,
                capture_date AS date_utc,
                text,
                media_type,
                file_name,
                link_metadata_json,
                display_category
            FROM members
            WHERE member_rank = 1
        ),
        ranked AS (
            SELECT
                *,
                COUNT(*) OVER (
                    PARTITION BY display_category
                ) AS item_count,
                ROW_NUMBER() OVER (
                    PARTITION BY display_category
                    ORDER BY date_utc DESC, message_id DESC
                ) AS category_rank
            FROM classified
        )
        SELECT *
        FROM ranked
        WHERE category_rank = 1
        ORDER BY
            CASE WHEN display_category = 'Uncategorised' THEN 1 ELSE 0 END,
            item_count DESC,
            date_utc DESC
        """
    ).fetchall()
]
bucket_by_category = {
    row["display_category"]: row
    for row in bucket_rows
}
topic_rows = [
    bucket_by_category[category]
    for category in PRIMARY_TOPIC_ORDER
    if category in bucket_by_category
]
remaining_topic_names = [
    row["display_category"]
    for row in bucket_rows
    if row["display_category"] not in PRIMARY_TOPIC_ORDER
]
available_topics = [
    "All topics",
    *(row["display_category"] for row in topic_rows),
    *remaining_topic_names,
]
if st.session_state.get("topic_filter") not in available_topics:
    st.session_state.topic_filter = "All topics"

pinned_results = search(
    connection,
    config,
    "",
    "all",
    3,
    include_sensitive=False,
    pinned_only=True,
)
pinned_count = int(stats["pinned"] or 0)

sync_snapshot = watcher_snapshot(config)
index_snapshot = indexer_snapshot(config)
indexed_total = int(stats["indexed"] or 0)
message_total = int(stats["messages"] or 0)
sync_online = (
    sync_snapshot["alive"] and sync_snapshot["status"] == "online"
)
index_online = bool(index_snapshot["alive"])

with st.sidebar:
    st.markdown("### System")

    def sync_status_panel(snapshot: dict) -> None:
        if snapshot["alive"] and snapshot["status"] == "online":
            st.markdown(
                '<div class="sync-online">Live sync online</div>',
                unsafe_allow_html=True,
            )
            st.caption(
                "Checked "
                + service_age(snapshot["status_at"])
                + f" · {snapshot['last_count']} caught up"
            )
            if st.button(
                "Pause live sync",
                icon=":material/pause:",
                width="stretch",
            ):
                stop_watcher(config)
                st.rerun()
        elif snapshot["alive"] and snapshot["status"] in {
            "starting",
            "connecting",
        }:
            st.markdown(
                '<div class="sync-offline">Live sync is starting</div>',
                unsafe_allow_html=True,
            )
            st.caption("Telegram is connecting in the background.")
        else:
            label = (
                "Sync needs attention"
                if snapshot["status"] == "error"
                else "Live sync is offline"
            )
            st.markdown(
                f'<div class="sync-offline">{label}</div>',
                unsafe_allow_html=True,
            )
            if snapshot["error"]:
                st.caption("Last attempt could not connect.")
            if st.button(
                "Start live sync",
                icon=":material/sync:",
                width="stretch",
            ):
                start_watcher(config)
                st.rerun()

            if st.button(
                "Catch up once",
                icon=":material/refresh:",
                width="stretch",
            ):
                start_incremental_sync(config)
                st.toast("Background catch-up started.")

    services_need_attention = not sync_online or not index_online
    with st.expander(
        "Background services",
        expanded=services_need_attention,
        icon=":material/sync:",
    ):
        sync_status_panel(sync_snapshot)
        st.divider()

        if index_snapshot["alive"]:
            st.markdown(
                '<div class="sync-online">Content indexer online</div>',
                unsafe_allow_html=True,
            )
            st.progress(
                indexed_total / max(message_total, 1),
                text=(
                    f"{indexed_total:,} of {message_total:,} messages "
                    "fully indexed"
                ),
            )
            if index_snapshot["current"]:
                st.caption("Reading " + index_snapshot["current"])
            st.caption(
                f"{index_snapshot['partial']:,} partial · "
                f"{index_snapshot['failed']:,} unavailable"
            )
            if st.button(
                "Pause content indexing",
                icon=":material/pause:",
                width="stretch",
            ):
                stop_content_indexer(config)
                st.rerun()
        else:
            st.markdown(
                '<div class="sync-offline">Content indexer offline</div>',
                unsafe_allow_html=True,
            )
            st.caption(
                f"{indexed_total:,} full · "
                f"{index_snapshot['partial']:,} partial · "
                f"{index_snapshot['failed']:,} unavailable"
            )
            if st.button(
                "Resume content indexing",
                icon=":material/play_arrow:",
                width="stretch",
            ):
                start_content_indexer(config)
                st.rerun()

    with st.expander(
        "Library health",
        icon=":material/monitoring:",
    ):
        st.markdown(
            f"**{int(stats['total'] or 0):,}** captures  \n"
            f"**{pinned_count:,}** pinned  \n"
            f"**{starred_count:,}** saved  \n"
            f"**{int(stats['media'] or 0):,}** attachments  \n"
            f"**{int(stats['links'] or 0):,}** links"
        )
        st.progress(
            indexed_total / max(message_total, 1),
            text=f"{indexed_total:,} of {message_total:,} searchable",
        )
        st.caption(
            f"{int(stats['sensitive'] or 0):,} sensitive items protected."
        )
        st.caption(
            f"{int(stats['duplicates'] or 0):,} duplicate media items hidden; "
            "originals retained."
        )
        st.caption(
            "Semantic search "
            + ("enabled." if config.enable_embeddings else "is optional.")
        )

page_size = 20

header_left, header_right = st.columns([10, 1], vertical_alignment="center")
with header_left:
    st.markdown(
        '<div class="app-title">Telegram Brain</div>'
        '<div class="app-subtitle">'
        "Find, reuse or send anything from one place."
        "</div>",
        unsafe_allow_html=True,
    )
with header_right:
    if st.button(
        "",
        icon=":material/refresh:",
        width="content",
        help="Reload newly indexed messages",
    ):
        st.rerun()

st.markdown(
    '<div class="status-strip">'
    '<span class="status-item">'
    f'<span class="status-dot{" online" if sync_online else ""}"></span>'
    f'{"Telegram synced" if sync_online else "Sync needs attention"}'
    "</span>"
    '<span class="status-item">'
    f'<span class="status-dot{" online" if index_online else ""}"></span>'
    f'{"Search ready" if index_online else "Search updating"}'
    "</span>"
    f'<span class="status-item">{int(stats["total"] or 0):,} saved items</span>'
    "</div>",
    unsafe_allow_html=True,
)

app_mode = st.segmented_control(
    "Workspace",
    ["Find & browse", "Send to Telegram"],
    key="app_mode",
    width="content",
    label_visibility="collapsed",
)

if app_mode == "Send to Telegram":
    render_telegram_composer(config, connection)
    connection.close()
    st.stop()

render_library_workspace(
    connection,
    config,
    available_topics=available_topics,
    stats=stats,
    pinned_results=pinned_results,
)

warning, warning_at = get_runtime_state(connection, "last_search_warning")
if warning and warning_at:
    if row_datetime(warning_at) > datetime.now(timezone.utc) - timedelta(
        minutes=5
    ):
        st.caption("Search used a local fallback on the last query.")
connection.close()
st.stop()
