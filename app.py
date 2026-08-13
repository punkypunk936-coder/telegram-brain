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
    VISION_PROMPT_VERSION,
    db,
    get_runtime_state,
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

CONTENT_TYPES = {
    "Everything": "all",
    "Text only": "text",
    "Links": "all",
    "Images": "image",
    "Video": "video",
    "Audio": "audio",
    "PDFs": "pdf",
    "Documents": "document",
}

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

TOPIC_DESCRIPTIONS = {
    "Trading & Markets": "Setups, theses, market notes and charts",
    "Technology & AI": "Models, chips, tools and technical research",
    "Work & Career": "Applications, professional notes and work history",
    "Writing": "Drafts, passages and ideas worth developing",
    "Research & Learning": "Reading notes, explainers and useful references",
    "Ideas & Building": "Product ideas, experiments and things to make",
    "Memes & Culture": "Memes, reactions and internet culture",
    "Personal & Admin": "Personal reminders, plans and life admin",
}

def reset_page() -> None:
    st.session_state.page = 1
    st.session_state.selected_library_item = None


def open_bucket(category: str) -> None:
    st.session_state.topic_filter = category
    st.session_state.content_filter = "Everything"
    st.session_state.scope_filter = "All"
    st.session_state.search_query = ""
    st.session_state.page = 1


def show_homepage() -> None:
    st.session_state.topic_filter = "All topics"
    st.session_state.content_filter = "Everything"
    st.session_state.scope_filter = "All"
    st.session_state.search_query = ""
    st.session_state.page = 1


def open_pinned() -> None:
    st.session_state.topic_filter = "All topics"
    st.session_state.content_filter = "Everything"
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


def process_scope(rows: list[dict], scope: str) -> list[dict]:
    if scope == "Pinned":
        return [row for row in rows if row.get("is_pinned")]
    if scope == "Saved":
        return [row for row in rows if row.get("starred")]
    if scope == "Links":
        return [row for row in rows if parse_urls(row)]
    if scope == "Media":
        return [row for row in rows if row.get("media_path")]
    return rows


def process_content_filter(rows: list[dict], label: str) -> list[dict]:
    if label == "Links":
        return [row for row in rows if parse_urls(row)]
    return rows


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

    st.markdown(
        '<div class="action-label">Use this item</div>',
        unsafe_allow_html=True,
    )
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


LIBRARY_VIEWS = ("Media", "Files", "Links", "Voice", "GIFs")
GALLERY_PAGE_SIZE = 36
LIST_PAGE_SIZE = 18


def _is_gif_item(item: dict) -> bool:
    path = Path(str(item.get("path") or item.get("media_path") or ""))
    mime = str(item.get("mime_type") or "").lower()
    media_type = str(item.get("media_type") or "").lower()
    return media_type == "video" or mime == "image/gif" or path.suffix.lower() == ".gif"


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
            (m.media_type = 'video'
             OR LOWER(COALESCE(m.file_name, '')) LIKE '%.gif'
             OR LOWER(COALESCE(m.mime_type, '')) = 'image/gif')
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
                    st.markdown("**Image understanding**")
                    st.write(clean_preview(vision, 1400))
                if row.get("extracted_text"):
                    st.markdown("**Extracted content**")
                    st.write(
                        clean_preview(
                            mask_sensitive_text(row["extracted_text"]),
                            1400,
                        )
                    )


def render_media_grid(rows: list[dict], view: str) -> None:
    assets: list[tuple[dict, dict]] = []
    for row in rows:
        for item in existing_media_items(row):
            if view == "Media" and item.get("media_type") == "image" and not _is_gif_item(item):
                assets.append((row, item))
            elif view == "GIFs" and _is_gif_item(item):
                assets.append((row, item))
    column_count = 6 if view == "Media" else 3
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
                else:
                    st.video(str(item["_path"]), autoplay=False, loop=True)
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


config = settings()
connection = db(config.db_path)

if "page" not in st.session_state:
    st.session_state.page = 1
if "scope_filter" not in st.session_state:
    st.session_state.scope_filter = "All"
if st.session_state.scope_filter not in {"All", "Pinned", "Saved"}:
    st.session_state.scope_filter = "All"
if "content_filter" not in st.session_state:
    st.session_state.content_filter = "Everything"
if st.session_state.content_filter not in CONTENT_TYPES:
    st.session_state.content_filter = "Everything"
if "app_mode" not in st.session_state:
    st.session_state.app_mode = "Library"

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
            WHEN media_type = 'image'
             AND media_path IS NOT NULL
             AND (
                TRIM(vision_text) = ''
                OR COALESCE(vision_model, '') != ?
                OR vision_prompt_version < ?
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
    (config.vision_model, VISION_PROMPT_VERSION),
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

review_count = int(
    bucket_by_category.get("Uncategorised", {}).get("item_count", 0)
)

latest_results = search(
    connection,
    config,
    "",
    "all",
    1,
    include_sensitive=False,
)
latest_row = latest_results[0] if latest_results else None
pinned_results = search(
    connection,
    config,
    "",
    "all",
    3,
    include_sensitive=False,
    pinned_only=True,
)
review_results = search(
    connection,
    config,
    "",
    "all",
    3,
    include_sensitive=False,
    category="Uncategorised",
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
        "Send anything to Telegram, then find it again without digging."
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
    f'{"Sync live" if sync_online else "Sync needs attention"}'
    "</span>"
    '<span class="status-item">'
    f'<span class="status-dot{" online" if index_online else ""}"></span>'
    f'{"Indexer live" if index_online else "Indexer paused"}'
    "</span>"
    f'<span class="status-item">{int(stats["total"] or 0):,} captures</span>'
    f'<span class="status-item">{pinned_count:,} pinned</span>'
    f'<span class="status-item">{starred_count:,} saved</span>'
    "</div>",
    unsafe_allow_html=True,
)

app_mode = st.segmented_control(
    "Workspace",
    ["Library", "Send to Telegram"],
    key="app_mode",
    width="content",
    label_visibility="collapsed",
)

if app_mode == "Send to Telegram":
    render_telegram_composer(config, connection)
    connection.close()
    st.stop()

st.markdown(
    '<div class="search-label">Find anything</div>',
    unsafe_allow_html=True,
)

q = st.text_input(
    "Search",
    placeholder="Describe a person, scene, meme or text you remember",
    key="search_query",
    label_visibility="collapsed",
    on_change=reset_page,
)

scope_column, topic_column, format_column = st.columns(
    [1.5, 2.2, 1.2],
    vertical_alignment="bottom",
)
with scope_column:
    scope = st.segmented_control(
        "Show",
        ["All", "Pinned", "Saved"],
        key="scope_filter",
        on_change=reset_page,
        width="stretch",
    )
with topic_column:
    selected_topic = st.selectbox(
        "Topic",
        available_topics,
        key="topic_filter",
        on_change=reset_page,
    )
with format_column:
    content_label = st.selectbox(
        "Format",
        list(CONTENT_TYPES),
        key="content_filter",
        on_change=reset_page,
    )

scope = scope or "All"

st.markdown(
    '<div class="library-context">'
    "Search checks image content, captions, documents, voice notes, "
    "filenames and links. Combine topic and format filters when needed."
    "</div>",
    unsafe_allow_html=True,
)

home_mode = (
    not q
    and scope == "All"
    and selected_topic == "All topics"
    and content_label == "Everything"
)

if home_mode:
    if review_count:
        st.markdown(
            '<div class="section-title">Review inbox</div>'
            '<div class="section-subtitle">'
            "Give unclassified captures a useful home as the library grows."
            "</div>",
            unsafe_allow_html=True,
        )
        with st.container(border=True):
            review_metric, review_details, review_action = st.columns(
                [1.2, 5, 1.3],
                vertical_alignment="center",
            )
            with review_metric:
                st.markdown(
                    f'<div class="review-count">{review_count:,}</div>'
                    '<div class="result-meta">need a topic</div>',
                    unsafe_allow_html=True,
                )
            with review_details:
                classified_count = max(
                    int(stats["total"] or 0) - review_count,
                    0,
                )
                st.markdown(
                    '<div class="review-copy">'
                    f'<strong>{classified_count:,} captures are already '
                    "organised.</strong> Review the remainder when convenient; "
                    "the original content stays searchable either way."
                    "</div>",
                    unsafe_allow_html=True,
                )
                if review_results:
                    review_titles = " · ".join(
                        clean_preview(result_title(row), 48)
                        for row in review_results
                    )
                    st.caption("Up next: " + review_titles)
            with review_action:
                st.button(
                    "Review now",
                    icon=":material/inbox:",
                    on_click=open_bucket,
                    args=("Uncategorised",),
                    width="stretch",
                )

    if pinned_results:
        pinned_header, pinned_action = st.columns(
            [8, 1],
            vertical_alignment="center",
        )
        with pinned_header:
            st.markdown(
                '<div class="pinned-section">'
                '<div class="pinned-title">Pinned essentials</div>'
                '<div class="section-subtitle">'
                "Important Telegram pins, kept close at hand."
                "</div></div>",
                unsafe_allow_html=True,
            )
        with pinned_action:
            st.button(
                "View all",
                icon=":material/push_pin:",
                on_click=open_pinned,
                width="content",
            )

        pinned_columns = st.columns(len(pinned_results))
        for column, pinned_row in zip(pinned_columns, pinned_results):
            pinned_text = mask_sensitive_text(
                pinned_row.get("text")
                or pinned_row.get("extracted_text")
                or ""
            ).strip()
            pinned_category = (
                pinned_row.get("display_category") or "Uncategorised"
            )
            with column:
                with st.container(border=True):
                    st.badge("Pinned", color="orange")
                    st.badge(pinned_category, color="gray")
                    st.markdown(
                        '<div class="result-title">'
                        + html.escape(result_title(pinned_row))
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        '<div class="pinned-preview">'
                        + html.escape(
                            clean_preview(
                                pinned_text or "Media saved without a caption.",
                                150,
                            )
                        )
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                    render_copy_actions(
                        pinned_row,
                        f"pinned-{pinned_row['id']}",
                    )

    if latest_row:
        st.markdown(
            '<div class="section-title">Pick up where you left off</div>'
            '<div class="section-subtitle">'
            "Ready to reuse without digging through Telegram."
            "</div>",
            unsafe_allow_html=True,
        )
        with st.container(border=True):
            latest_main, latest_action = st.columns(
                [5, 1],
                vertical_alignment="top",
            )
            latest_category = (
                latest_row.get("display_category") or "Uncategorised"
            )
            latest_text = mask_sensitive_text(
                latest_row.get("text") or ""
            ).strip()
            title_text = latest_text
            latest_title = result_title(
                {**latest_row, "text": title_text}
            )
            latest_preview = latest_text
            first_line = next(
                (
                    line.strip()
                    for line in title_text.splitlines()
                    if line.strip()
                ),
                "",
            )
            if first_line and latest_preview.startswith(first_line):
                latest_preview = latest_preview[len(first_line) :].strip()
            with latest_main:
                st.badge(latest_category, color="gray")
                st.markdown(
                    '<div class="result-title">'
                    + html.escape(latest_title)
                    + "</div>",
                    unsafe_allow_html=True,
                )
                if latest_row.get("capture_size", 1) > 1:
                    st.markdown(
                        '<div class="result-meta">'
                        + html.escape(
                            f"{latest_row.get('capture_size')} linked messages"
                        )
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                if latest_preview:
                    display_preview = re.sub(
                        r"(?m)^#{1,6}\s+",
                        "",
                        latest_preview,
                    )
                    st.markdown(
                        '<div class="latest-preview">'
                        + html.escape(clean_preview(display_preview, 260))
                        + "</div>",
                        unsafe_allow_html=True,
                    )
            with latest_action:
                st.button(
                    "Open",
                    icon=":material/arrow_forward:",
                    key="open-latest-bucket",
                    on_click=open_bucket,
                    args=(latest_category,),
                    width="content",
                )
            render_copy_actions(
                latest_row,
                f"latest-{latest_row['id']}",
            )
            render_link_actions(
                latest_row,
                f"latest-{latest_row['id']}",
            )

    st.markdown(
        '<div class="section-title">Browse by topic</div>'
        '<div class="section-subtitle">'
        "Your archive is grouped by what each capture is about, not only "
        "by file type."
        "</div>",
        unsafe_allow_html=True,
    )
    for row_start in range(0, len(topic_rows), 4):
        bucket_columns = st.columns(4)
        for column, bucket in zip(
            bucket_columns,
            topic_rows[row_start : row_start + 4],
        ):
            category = bucket["display_category"]
            bucket_title = result_title(bucket)
            with column:
                with st.container(border=True):
                    st.badge(category, color="gray")
                    st.markdown(
                        f'<div class="bucket-count">'
                        f'{int(bucket["item_count"]):,}'
                        "</div>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        '<div class="bucket-description">'
                        + html.escape(
                            TOPIC_DESCRIPTIONS.get(
                                category,
                                "Related captures from your archive",
                            )
                        )
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        '<div class="bucket-latest">'
                        + "Latest: "
                        + html.escape(clean_preview(bucket_title, 68))
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                    st.button(
                        "Open bucket",
                        icon=":material/arrow_forward:",
                        key=f"open-bucket-{category}",
                        on_click=open_bucket,
                        args=(category,),
                        width="stretch",
                    )

    connection.close()
    st.stop()

active_label = "Library"
if q:
    active_label = f'Results for "{clean_preview(q, 50)}"'
elif scope != "All":
    active_label = scope
elif selected_topic == "Uncategorised":
    active_label = "Review inbox"
elif selected_topic != "All topics":
    active_label = selected_topic
elif content_label != "Everything":
    active_label = content_label

library_header, home_action = st.columns(
    [8, 1],
    vertical_alignment="center",
)
with library_header:
    st.markdown(
        f'<div class="section-title">{html.escape(active_label)}</div>',
        unsafe_allow_html=True,
    )
with home_action:
    st.button(
        "Home",
        icon=":material/home:",
        on_click=show_homepage,
        width="content",
    )

rows = search(
    connection,
    config,
    q,
    CONTENT_TYPES[content_label],
    max(stats["total"], 1),
    include_sensitive=False,
    category=selected_topic,
    starred_only=scope == "Saved",
    pinned_only=scope == "Pinned",
)
if selected_topic != "All topics":
    rows = [
        row
        for row in rows
        if (
            row.get("display_category")
            or row.get("category")
            or "Uncategorised"
        )
        == selected_topic
    ]
rows = process_scope(rows, scope)
rows = process_content_filter(rows, content_label)

if not q:
    rows.sort(
        key=lambda row: (
            row_datetime(row["date_utc"]),
            row["message_id"],
        ),
        reverse=True,
    )

result_label = "result" if len(rows) == 1 else "results"
active_filters = []
if (
    selected_topic not in {"All topics", "Uncategorised"}
    and selected_topic != active_label
):
    active_filters.append(selected_topic)
if content_label != "Everything":
    active_filters.append(content_label)
filter_context = (
    " · " + " · ".join(active_filters)
    if active_filters
    else ""
)
st.markdown(
    '<div class="result-count">'
    + f"{len(rows):,} {result_label}"
    + html.escape(filter_context)
    + "</div>",
    unsafe_allow_html=True,
)

if not rows:
    if q and config.enable_vision and int(stats["visual_pending"] or 0):
        st.warning(
            "No confident match yet. The upgraded image reader is still "
            f"re-reading {int(stats['visual_pending']):,} older images in "
            "the background; new images are handled first.",
            icon=":material/image_search:",
        )
    else:
        st.info(
            "Nothing matches these filters yet. Try a broader search or "
            "another topic.",
            icon=":material/search:",
        )
    connection.close()
    st.stop()

total_pages = max(1, math.ceil(len(rows) / page_size))
st.session_state.page = min(st.session_state.page, total_pages)
start = (st.session_state.page - 1) * page_size
end = min(start + page_size, len(rows))
page_rows = rows[start:end]

if total_pages > 1:
    nav_left, nav_middle, nav_right = st.columns([1, 2, 1])
    with nav_left:
        if st.button(
            "Previous",
            icon=":material/arrow_back:",
            disabled=st.session_state.page <= 1,
            width="stretch",
        ):
            st.session_state.page -= 1
            st.rerun()
    with nav_middle:
        st.caption(
            f"Page {st.session_state.page} of {total_pages} · "
            f"showing {start + 1}–{end}"
        )
    with nav_right:
        if st.button(
            "Next",
            icon=":material/arrow_forward:",
            disabled=st.session_state.page >= total_pages,
            width="stretch",
        ):
            st.session_state.page += 1
            st.rerun()

for row in page_rows:
    existing_media = existing_media_items(row)
    media_exists = bool(existing_media)
    media = existing_media[0]["_path"] if existing_media else None
    text = mask_sensitive_text(row.get("text") or "").strip()
    extracted_text = mask_sensitive_text(
        row.get("extracted_text") or ""
    ).strip()
    vision_text = mask_sensitive_text(
        row.get("vision_text") or ""
    ).strip()
    title = result_title({**row, "text": text})
    category = row.get("display_category") or row.get("category")
    note = row.get("note") or ""

    with st.container(border=True):
        main, action = st.columns([12, 1], vertical_alignment="top")
        with main:
            if row.get("is_pinned"):
                st.badge("Pinned in Telegram", color="orange")
            st.badge(category, color="gray")
            status_label, status_color = index_label(row)
            if row.get("content_status") != "ready":
                st.badge(status_label, color=status_color)
            if row.get("_duplicate_count"):
                st.badge(
                    f"{row['_duplicate_count']} repeats avoided",
                    color="gray",
                )
            st.markdown(
                f'<div class="result-title">{html.escape(title)}</div>',
                unsafe_allow_html=True,
            )
            if row.get("capture_size", 1) > 1:
                st.markdown(
                    '<div class="result-meta">'
                    + html.escape(
                        f"{row.get('capture_size')} linked messages"
                    )
                    + "</div>",
                    unsafe_allow_html=True,
                )
        with action:
            if st.button(
                "",
                icon=(
                    ":material/star:"
                    if row.get("starred")
                    else ":material/star_outline:"
                ),
                help=(
                    "Remove from saved"
                    if row.get("starred")
                    else "Save for later"
                ),
                key=f"star-{row['id']}",
                width="content",
            ):
                set_item_state(
                    connection,
                    row["id"],
                    starred=not bool(row.get("starred")),
                )
                st.rerun()

        images = [
            item
            for item in existing_media
            if item.get("media_type") == "image"
        ]
        if len(images) == 1:
            media_col, text_col = st.columns(
                [1, 2.2],
                vertical_alignment="top",
            )
            with media_col:
                st.image(str(images[0]["_path"]), width="stretch")
            with text_col:
                if text:
                    st.write(clean_preview(text))
                elif extracted_text:
                    st.write(clean_preview(extracted_text))
                elif vision_text:
                    st.write(visual_preview(vision_text))
                else:
                    st.caption("Image saved without a caption.")
        elif images:
            image_columns = st.columns(min(3, len(images)))
            for index, item in enumerate(images[:6]):
                with image_columns[index % len(image_columns)]:
                    st.image(str(item["_path"]), width="stretch")
            if text:
                st.write(clean_preview(text))
            elif extracted_text:
                st.write(clean_preview(extracted_text))
            elif vision_text:
                st.write(visual_preview(vision_text))
        else:
            if text:
                st.write(clean_preview(text))
            elif extracted_text:
                st.write(clean_preview(extracted_text))
            elif media_exists:
                st.caption(media.name)
            else:
                st.caption("No preview available.")

        if len(re.sub(r"\s+", " ", text)) > 520:
            with st.expander("Read full text"):
                st.write(text)

        playable = [
            item
            for item in existing_media
            if item.get("media_type") in {"video", "audio"}
        ]
        for item in playable:
            if item.get("media_type") == "video":
                with st.expander(
                    "Play " + (item.get("file_name") or "video")
                ):
                    st.video(str(item["_path"]))
            else:
                st.audio(str(item["_path"]))

        render_copy_actions(row, f"result-{row['id']}")
        render_link_actions(row, f"result-{row['id']}")
        if note:
            st.caption(f"Note: {clean_preview(note, 120)}")

        with st.expander("Why it matched"):
            if row.get("is_pinned"):
                st.markdown("- Pinned in the source Telegram chat")
            for reason in row.get("_match_reasons") or [
                "Shown by the current library filters"
            ]:
                st.markdown(f"- {reason}")
            if row.get("content_status") == "ready":
                st.caption(
                    "All available text, files, media and links in this "
                    "capture have been checked."
                )
            elif row.get("content_status") == "indexing":
                st.caption(
                    "This capture is still being read in the background. "
                    "Search coverage will improve automatically."
                )
            else:
                st.caption(
                    "Some content could not be read. The original Telegram "
                    "text, filenames and URLs remain searchable."
                )
                if row.get("content_error"):
                    st.caption(clean_preview(row["content_error"], 280))
            if extracted_text:
                st.markdown("**Indexed content preview**")
                st.write(clean_preview(extracted_text, 900))
            if vision_text:
                st.markdown("**What the image reader saw**")
                st.write(clean_preview(vision_text, 900))

        with st.expander(
            (
                "Choose a topic"
                if selected_topic == "Uncategorised"
                else "Organise"
            ),
            expanded=selected_topic == "Uncategorised",
        ):
            editor_left, editor_right = st.columns([1, 2])
            with editor_left:
                category_options = ORGANISE_TOPICS
                current_category = (
                    row.get("user_category")
                    or row.get("category")
                    or "Uncategorised"
                )
                if current_category not in category_options:
                    current_category = "Uncategorised"
                user_category = st.selectbox(
                    "Topic",
                    category_options,
                    index=category_options.index(current_category),
                    key=f"category-{row['id']}",
                )
            with editor_right:
                user_note = st.text_area(
                    "Private note",
                    value=note,
                    placeholder="Why this matters, what to do next…",
                    key=f"note-{row['id']}",
                    height=96,
                )
            if st.button(
                "Save changes",
                icon=":material/check:",
                key=f"save-{row['id']}",
            ):
                set_item_state(
                    connection,
                    row["id"],
                    note=user_note,
                    user_category=user_category,
                )
                st.toast("Saved.")
                st.rerun()

if total_pages > 1:
    st.divider()
    bottom_left, bottom_middle, bottom_right = st.columns([1, 2, 1])
    with bottom_left:
        if st.button(
            "Previous",
            icon=":material/arrow_back:",
            disabled=st.session_state.page <= 1,
            key="previous-bottom",
            width="stretch",
        ):
            st.session_state.page -= 1
            st.rerun()
    with bottom_middle:
        st.caption(
            f"Showing {start + 1}–{end} of {len(rows):,} results"
        )
    with bottom_right:
        if st.button(
            "Next",
            icon=":material/arrow_forward:",
            disabled=st.session_state.page >= total_pages,
            key="next-bottom",
            width="stretch",
        ):
            st.session_state.page += 1
            st.rerun()

warning, warning_at = get_runtime_state(connection, "last_search_warning")
if warning and warning_at:
    if row_datetime(warning_at) > datetime.now(timezone.utc) - timedelta(
        minutes=5
    ):
        st.caption("Search used a local fallback on the last query.")

connection.close()
