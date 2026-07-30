import argparse
import asyncio

from tgbrain import (
    client,
    db,
    ingest,
    set_runtime_state,
    settings,
    sync_pinned_messages,
)


async def sync_messages(limit: int | None, full: bool) -> int:
    config = settings(True)
    connection = db(config.db_path)
    telegram = client(config)
    imported = 0

    set_runtime_state(connection, "sync_status", "connecting")
    try:
        await telegram.start(phone=config.phone)
        entity = await telegram.get_entity(config.chat_id)
        latest = connection.execute(
            "SELECT COALESCE(MAX(message_id), 0) AS id FROM messages"
        ).fetchone()["id"]
        min_id = 0 if full else latest
        set_runtime_state(connection, "sync_status", "syncing")

        async for message in telegram.iter_messages(
            entity,
            reverse=True,
            limit=limit,
            min_id=min_id,
        ):
            if await ingest(telegram, config, connection, message):
                imported += 1
                if imported % 50 == 0:
                    print("Imported", imported)

        pinned_count = await sync_pinned_messages(
            telegram,
            config,
            connection,
            entity,
        )
        set_runtime_state(connection, "last_sync_count", imported)
        set_runtime_state(connection, "sync_status", "idle")
        print(
            "Done:",
            imported,
            "new messages and",
            pinned_count,
            "current pins",
        )
        return imported
    except Exception as error:
        set_runtime_state(connection, "sync_status", "error")
        set_runtime_state(connection, "sync_error", str(error))
        raise
    finally:
        connection.close()
        await telegram.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--full",
        action="store_true",
        help="Revisit all available messages instead of only new ones.",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Explicit alias for the default incremental sync.",
    )
    args = parser.parse_args()
    asyncio.run(sync_messages(args.limit, args.full))


if __name__ == "__main__":
    main()
