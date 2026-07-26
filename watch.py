import asyncio
import logging
import os

from telethon import events

from tgbrain import (
    client,
    db,
    ingest,
    set_runtime_state,
    settings,
)


logging.basicConfig(level=logging.INFO)


async def catch_up(telegram, config, connection, entity) -> int:
    latest = connection.execute(
        "SELECT COALESCE(MAX(message_id), 0) AS id FROM messages"
    ).fetchone()["id"]
    imported = 0
    async for message in telegram.iter_messages(
        entity,
        reverse=True,
        min_id=latest,
    ):
        if await ingest(telegram, config, connection, message):
            imported += 1
    return imported


async def heartbeat(connection) -> None:
    while True:
        set_runtime_state(connection, "watcher_status", "online")
        set_runtime_state(connection, "watcher_pid", os.getpid())
        await asyncio.sleep(20)


async def main() -> None:
    config = settings(True)
    connection = db(config.db_path)
    telegram = client(config)
    set_runtime_state(connection, "watcher_status", "connecting")
    set_runtime_state(connection, "watcher_pid", os.getpid())

    try:
        await telegram.start(phone=config.phone)
        entity = await telegram.get_entity(config.chat_id)
        imported = await catch_up(telegram, config, connection, entity)
        set_runtime_state(connection, "last_sync_count", imported)
        set_runtime_state(connection, "watcher_status", "online")

        @telegram.on(events.NewMessage(chats=entity))
        @telegram.on(events.MessageEdited(chats=entity))
        async def handler(event):
            try:
                if await ingest(
                    telegram,
                    config,
                    connection,
                    event.message,
                ):
                    print("Indexed message", event.message.id)
            except Exception:
                logging.exception("Ingest failed")

        heartbeat_task = asyncio.create_task(heartbeat(connection))
        print(
            "Watching chat",
            config.chat_id,
            "after importing",
            imported,
            "missed messages",
        )
        try:
            await telegram.run_until_disconnected()
        finally:
            heartbeat_task.cancel()
    except Exception as error:
        set_runtime_state(connection, "watcher_status", "error")
        set_runtime_state(connection, "watcher_error", str(error))
        raise
    finally:
        set_runtime_state(connection, "watcher_status", "offline")
        connection.close()
        await telegram.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
