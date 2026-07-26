from __future__ import annotations

from media_dedupe import backfill_media_duplicates
from tgbrain import db, rebuild_search_index, settings


def main() -> int:
    config = settings()
    connection = db(config.db_path)
    try:
        counts = backfill_media_duplicates(connection)
        rebuild_search_index(connection)
    finally:
        connection.close()
    print(
        "Media dedupe complete: "
        f"{counts['canonical']} originals kept, "
        f"{counts['exact']} exact repeats hidden, "
        f"{counts['visual']} re-encoded repeats hidden, "
        f"{counts['unavailable']} unavailable."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
