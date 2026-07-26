from __future__ import annotations

import html
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import streamlit as st

from service_control import (
    start_incremental_sync,
    start_watcher,
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
            max-width: 1280px;
            padding-top: 4rem;
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
            font-size: 1.8rem;
            font-weight: 720;
            line-height: 1.15;
            margin: 0;
        }

        .app-subtitle {
            color: inherit;
            font-size: 0.94rem;
            margin: 0.25rem 0 1.25rem;
            opacity: 0.68;
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

        .metric-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 1rem;
            margin: 0.95rem 0 1.1rem;
        }

        .metric-item {
            border-top: 1px solid var(--brain-line);
            min-width: 0;
            padding-top: 0.65rem;
        }

        .metric-label {
            font-size: 0.78rem;
            opacity: 0.72;
        }

        .metric-value {
            font-size: 1.35rem;
            font-weight: 620;
            margin-top: 0.18rem;
        }

        .section-title {
            font-size: 1rem;
            font-weight: 680;
            margin: 1.2rem 0 0.7rem;
        }

        .latest-preview {
            font-size: 0.94rem;
            line-height: 1.55;
            margin-top: 0.45rem;
            opacity: 0.84;
        }

        .bucket-count {
            font-size: 1.45rem;
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

        @media (max-width: 720px) {
            .block-container {
                padding-top: 3.75rem;
                padding-left: 0.9rem;
                padding-right: 0.9rem;
            }

            .app-title {
                font-size: 1.45rem;
            }

            .metric-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 0.65rem 1rem;
                margin-bottom: 0.75rem;
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
            host = urlparse(first_line).netloc.replace("www.", "")
            return f"Link from {host}" if host else "Saved link"
        return clean_preview(first_line, 100)
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

if "service_bootstrapped" not in st.session_state:
    if os.getenv("AUTO_START_WATCHER", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        start_watcher(config)
    st.session_state.service_bootstrapped = True

stats = connection.execute(
    """
    SELECT
        COUNT(*) AS total,
        SUM(CASE WHEN is_sensitive = 1 THEN 1 ELSE 0 END) AS sensitive,
        SUM(CASE WHEN media_path IS NOT NULL THEN 1 ELSE 0 END) AS media,
        SUM(CASE WHEN urls_json != '[]' THEN 1 ELSE 0 END) AS links,
        MAX(date_utc) AS latest
    FROM messages
    """
).fetchone()
starred_count = connection.execute(
    "SELECT COUNT(*) AS n FROM item_state WHERE starred = 1"
).fetchone()["n"]

bucket_rows = [
    dict(row)
    for row in connection.execute(
        """
        WITH classified AS (
            SELECT
                m.id,
                m.message_id,
                m.date_utc,
                m.text,
                m.media_type,
                m.file_name,
                COALESCE(s.user_category, m.category) AS display_category
            FROM messages m
            LEFT JOIN item_state s ON s.message_row_id = m.id
            WHERE m.is_sensitive = 0
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

latest_row_result = connection.execute(
    """
    SELECT
        m.*,
        COALESCE(s.starred, 0) AS starred,
        COALESCE(s.note, '') AS note,
        s.user_category,
        COALESCE(s.user_category, m.category) AS display_category
    FROM messages m
    LEFT JOIN item_state s ON s.message_row_id = m.id
    WHERE m.is_sensitive = 0
    ORDER BY m.date_utc DESC, m.message_id DESC
    LIMIT 1
    """
).fetchone()
latest_row = dict(latest_row_result) if latest_row_result else None
latest_capture_rows = []
if latest_row:
    latest_capture_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT
                m.*,
                COALESCE(s.user_category, m.category) AS display_category
            FROM messages m
            LEFT JOIN item_state s ON s.message_row_id = m.id
            WHERE m.is_sensitive = 0
              AND m.date_utc = ?
              AND COALESCE(s.user_category, m.category) = ?
            ORDER BY m.message_id
            """,
            (
                latest_row["date_utc"],
                latest_row["display_category"],
            ),
        ).fetchall()
    ]

with st.sidebar:
    st.markdown("### Library")

    def sync_status_panel() -> None:
        snapshot = watcher_snapshot(config)
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

    sync_status_panel()

    st.divider()

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

    st.divider()
    st.caption(
        f"{stats['sensitive'] or 0} sensitive item(s) protected and hidden."
    )
    st.caption(
        "Semantic search "
        + ("enabled." if config.enable_embeddings else "is optional.")
    )

header_left, header_right = st.columns([4, 1], vertical_alignment="bottom")
with header_left:
    st.markdown(
        '<div class="app-title">Telegram Brain</div>'
        '<div class="app-subtitle">'
        "Search the things you sent yourself. Save the useful ones."
        "</div>",
        unsafe_allow_html=True,
    )
with header_right:
    if st.button(
        "Refresh",
        icon=":material/refresh:",
        width="stretch",
        help="Reload newly indexed messages",
    ):
        st.rerun()

q = st.text_input(
    "Search",
    placeholder="Search your archive",
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
    """
    <div class="metric-grid">
        <div class="metric-item">
            <div class="metric-label">Messages</div>
            <div class="metric-value">{messages:,}</div>
        </div>
        <div class="metric-item">
            <div class="metric-label">Saved</div>
            <div class="metric-value">{saved:,}</div>
        </div>
        <div class="metric-item">
            <div class="metric-label">Attachments</div>
            <div class="metric-value">{media:,}</div>
        </div>
        <div class="metric-item">
            <div class="metric-label">Links</div>
            <div class="metric-value">{links:,}</div>
        </div>
    </div>
    """.format(
        messages=stats["total"],
        saved=starred_count,
        media=stats["media"] or 0,
        links=stats["links"] or 0,
    ),
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
            '<div class="section-title">Latest capture</div>',
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
            capture_rows = latest_capture_rows or [latest_row]
            latest_text = "\n\n".join(
                mask_sensitive_text(row.get("text") or "").strip()
                for row in capture_rows
                if (row.get("text") or "").strip()
            )
            title_row = capture_rows[0]
            title_text = mask_sensitive_text(
                title_row.get("text") or ""
            ).strip()
            latest_title = result_title(
                {**title_row, "text": title_text}
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
                    f"{len(capture_rows)} linked messages · "
                    if len(capture_rows) > 1
                    else ""
                )
                st.markdown(
                    '<div class="result-meta">'
                    + "Added to "
                    + html.escape(latest_category)
                    + " · "
                    + html.escape(format_date(latest_row["date_utc"]))
                    + " · "
                    + html.escape(capture_meta)
                    + "latest message "
                    + html.escape(str(latest_row["message_id"]))
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
                    width="stretch",
                )

    st.markdown(
        '<div class="section-title">Buckets</div>',
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

active_label = selected_topic
if q:
    active_label = f'Search: "{clean_preview(q, 50)}"'
elif scope != "All":
    active_label = scope

library_header, home_action = st.columns(
    [4, 1],
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
        width="stretch",
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
st.caption(f"{len(rows):,} {result_label}")

if not rows:
    st.info(
        "Nothing matches these filters yet. Try a broader search or another "
        "topic.",
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
    media_path = row.get("media_path")
    media = Path(media_path) if media_path else None
    media_exists = bool(media and media.exists())
    media_type = row.get("media_type")
    text = mask_sensitive_text(row.get("text") or "").strip()
    title = result_title({**row, "text": text})
    urls = parse_urls(row)
    category = row.get("display_category") or row.get("category")
    note = row.get("note") or ""

    with st.container(border=True):
        main, action = st.columns([12, 1], vertical_alignment="top")
        with main:
            st.badge(category, color="gray")
            st.markdown(
                f'<div class="result-title">{html.escape(title)}</div>',
                unsafe_allow_html=True,
            )
            meta_bits = [
                format_date(row.get("date_utc")),
                f"message {row.get('message_id')}",
            ]
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
                width="stretch",
            ):
                set_item_state(
                    connection,
                    row["id"],
                    starred=not bool(row.get("starred")),
                )
                st.rerun()

        if media_exists and media_type == "image":
            media_col, text_col = st.columns(
                [1, 2.2],
                vertical_alignment="top",
            )
            with media_col:
                st.image(str(media), width="stretch")
            with text_col:
                if text:
                    st.write(clean_preview(text))
                else:
                    st.caption("Image saved without a caption.")
        else:
            if text:
                st.write(clean_preview(text))
            elif media_exists:
                st.caption(media.name)
            else:
                st.caption("No preview available.")

        if len(re.sub(r"\s+", " ", text)) > 520:
            with st.expander("Read full text"):
                st.write(text)

        if media_exists and media_type == "video":
            with st.expander("Play video"):
                st.video(str(media))
        elif media_exists and media_type == "audio":
            st.audio(str(media))

        if urls or media_exists or note:
            utility_columns = st.columns(
                max(1, min(3, len(urls[:2]) + int(media_exists) + int(bool(note))))
            )
            utility_index = 0
            for url in urls[:2]:
                host = urlparse(url).netloc.replace("www.", "") or "link"
                with utility_columns[utility_index]:
                    st.link_button(
                        host,
                        url,
                        icon=":material/open_in_new:",
                        width="stretch",
                    )
                utility_index += 1
            if media_exists:
                with utility_columns[utility_index]:
                    try:
                        st.download_button(
                            "Local copy",
                            data=media.read_bytes(),
                            file_name=media.name,
                            mime=row.get("mime_type")
                            or "application/octet-stream",
                            icon=":material/download:",
                            key=f"download-{row['id']}",
                            width="stretch",
                        )
                    except OSError:
                        st.caption("Local file unavailable.")
                utility_index += 1
            if note and utility_index < len(utility_columns):
                with utility_columns[utility_index]:
                    st.caption(f"Note: {clean_preview(note, 120)}")

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
