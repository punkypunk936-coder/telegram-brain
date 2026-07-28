from __future__ import annotations

import html
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import streamlit as st

from clipboard_utils import ClipboardError, copy_links, copy_media, copy_text
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
    initial_sidebar_state="auto",
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
            max-width: 1180px;
            padding-top: 3.4rem;
            padding-bottom: 5rem;
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
            font-size: 1.72rem;
            font-weight: 720;
            line-height: 1.15;
            margin: 0;
        }

        .app-subtitle {
            color: inherit;
            font-size: 0.94rem;
            margin: 0.25rem 0 0.8rem;
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
            margin: 0.15rem 0 1rem;
            padding: 0 0 0.75rem;
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

        .bucket-latest {
            min-height: 2.7rem;
            font-size: 0.85rem;
            line-height: 1.35;
            margin: 0.4rem 0 0.65rem;
            overflow-wrap: anywhere;
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
                padding-top: 3.75rem;
                padding-left: 0.9rem;
                padding-right: 0.9rem;
            }

            .app-title {
                font-size: 1.45rem;
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
    "Images": "image",
    "Video": "video",
    "Audio": "audio",
    "PDFs": "pdf",
    "Documents": "document",
}

DATE_WINDOWS = {
    "Any time": None,
    "Last 7 days": 7,
    "Last 30 days": 30,
    "Last 90 days": 90,
    "Last year": 365,
}


def reset_page() -> None:
    st.session_state.page = 1


def open_bucket(category: str) -> None:
    st.session_state.topic_filter = category
    st.session_state.content_filter = "Everything"
    st.session_state.date_filter = "Any time"
    st.session_state.sort_filter = "Newest first"
    st.session_state.scope_filter = "All"
    st.session_state.search_query = ""
    st.session_state.page = 1


def show_homepage() -> None:
    st.session_state.topic_filter = "All topics"
    st.session_state.content_filter = "Everything"
    st.session_state.date_filter = "Any time"
    st.session_state.sort_filter = "Most relevant"
    st.session_state.scope_filter = "All"
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


def format_date(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone().strftime("%d %b %Y, %H:%M")
    except (TypeError, ValueError):
        return str(value)[:19].replace("T", " ")


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
    if scope == "Saved":
        return [row for row in rows if row.get("starred")]
    if scope == "Links":
        return [row for row in rows if parse_urls(row)]
    if scope == "Media":
        return [row for row in rows if row.get("media_path")]
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


config = settings()
connection = db(config.db_path)

if "page" not in st.session_state:
    st.session_state.page = 1
if "scope_filter" not in st.session_state:
    st.session_state.scope_filter = "All"

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
             AND TRIM(vision_text) = ''
             AND duplicate_of_id IS NULL
            THEN 1 ELSE 0
        END)
            AS visual_pending,
        SUM(CASE WHEN duplicate_of_id IS NOT NULL THEN 1 ELSE 0 END)
            AS duplicates,
        MAX(date_utc) AS latest
    FROM messages
    """
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
available_topics = [
    "All topics",
    *(row["display_category"] for row in bucket_rows),
]
if st.session_state.get("topic_filter") not in available_topics:
    st.session_state.topic_filter = "All topics"

latest_results = search(
    connection,
    config,
    "",
    "all",
    1,
    include_sensitive=False,
)
latest_row = latest_results[0] if latest_results else None

sync_snapshot = watcher_snapshot(config)
index_snapshot = indexer_snapshot(config)
indexed_total = int(stats["indexed"] or 0)
message_total = int(stats["messages"] or 0)
sync_online = (
    sync_snapshot["alive"] and sync_snapshot["status"] == "online"
)
index_online = bool(index_snapshot["alive"])

with st.sidebar:
    st.markdown("### Refine")

    content_label = st.selectbox(
        "Content type",
        list(CONTENT_TYPES),
        key="content_filter",
        on_change=reset_page,
    )
    selected_topic = st.selectbox(
        "Topic",
        available_topics,
        key="topic_filter",
        on_change=reset_page,
    )
    date_window = st.selectbox(
        "Date",
        list(DATE_WINDOWS),
        key="date_filter",
        on_change=reset_page,
    )
    sort_order = st.selectbox(
        "Sort",
        ["Most relevant", "Newest first", "Oldest first"],
        key="sort_filter",
        on_change=reset_page,
    )
    page_size = st.select_slider(
        "Results per page",
        options=[10, 20, 30, 50],
        value=20,
        on_change=reset_page,
    )

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

header_left, header_right = st.columns([10, 1], vertical_alignment="center")
with header_left:
    st.markdown(
        '<div class="app-title">Telegram Brain</div>'
        '<div class="app-subtitle">'
        "Find the text, links, documents and media you sent yourself."
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
    f'<span class="status-item">{starred_count:,} saved</span>'
    "</div>",
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="search-label">Find anything</div>',
    unsafe_allow_html=True,
)

q = st.text_input(
    "Search",
    placeholder="Search captions, images, links or filenames",
    key="search_query",
    label_visibility="collapsed",
    on_change=reset_page,
)

scope = st.segmented_control(
    "Scope",
    ["All", "Saved", "Links", "Media"],
    key="scope_filter",
    on_change=reset_page,
    label_visibility="collapsed",
    width="stretch",
)

st.markdown(
    '<div class="library-context">'
    "Search checks captions, extracted document text, voice transcripts, "
    "image understanding, filenames and link metadata."
    "</div>",
    unsafe_allow_html=True,
)

home_mode = (
    not q
    and scope == "All"
    and selected_topic == "All topics"
    and content_label == "Everything"
    and date_window == "Any time"
    and sort_order == "Most relevant"
)

if home_mode:
    if latest_row:
        st.markdown(
            '<div class="section-title">Pick up where you left off</div>'
            '<div class="section-subtitle">'
            "Your newest capture, ready to reuse."
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
                capture_meta = (
                    f"{latest_row.get('capture_size', 1)} Telegram messages · "
                    if latest_row.get("capture_size", 1) > 1
                    else ""
                )
                st.markdown(
                    '<div class="result-meta">'
                    + html.escape(capture_meta)
                    + html.escape(format_date(latest_row["date_utc"]))
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
        "Jump into a part of your archive without writing a query."
        "</div>",
        unsafe_allow_html=True,
    )
    for row_start in range(0, len(bucket_rows), 3):
        bucket_columns = st.columns(3)
        for column, bucket in zip(
            bucket_columns,
            bucket_rows[row_start : row_start + 3],
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
                    st.caption("items · newest " + format_date(bucket["date_utc"]))
                    st.markdown(
                        '<div class="bucket-latest">'
                        + html.escape(clean_preview(bucket_title, 84))
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
)
rows = process_scope(rows, scope)

days = DATE_WINDOWS[date_window]
if days is not None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = [
        row for row in rows if row_datetime(row.get("date_utc")) >= cutoff
    ]

if sort_order == "Newest first" or (not q and sort_order == "Most relevant"):
    rows.sort(
        key=lambda row: (
            row_datetime(row["date_utc"]),
            row["message_id"],
        ),
        reverse=True,
    )
elif sort_order == "Oldest first":
    rows.sort(
        key=lambda row: (
            row_datetime(row["date_utc"]),
            row["message_id"],
        )
    )

result_label = "result" if len(rows) == 1 else "results"
active_filters = []
if selected_topic != "All topics" and selected_topic != active_label:
    active_filters.append(selected_topic)
if content_label != "Everything":
    active_filters.append(content_label)
if date_window != "Any time":
    active_filters.append(date_window)
if sort_order != "Most relevant":
    active_filters.append(sort_order)
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
            "No indexed match yet. The background image reader is still "
            f"analyzing {int(stats['visual_pending']):,} older images; "
            "new images are handled first.",
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
    title = result_title({**row, "text": text})
    category = row.get("display_category") or row.get("category")
    note = row.get("note") or ""

    with st.container(border=True):
        main, action = st.columns([12, 1], vertical_alignment="top")
        with main:
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
            meta_bits = [
                format_date(row.get("date_utc")),
            ]
            if row.get("capture_size", 1) > 1:
                meta_bits.append(
                    f"{row.get('capture_size')} Telegram messages"
                )
            if row.get("sender_name"):
                meta_bits.append(str(row["sender_name"]))
            st.markdown(
                '<div class="result-meta">'
                + " · ".join(html.escape(bit) for bit in meta_bits)
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

        with st.expander("Organise"):
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
