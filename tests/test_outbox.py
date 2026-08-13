import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from outbox import (
    MAX_RETRIES,
    claim_next_outbound,
    deliver_outbound,
    enqueue_outbound,
    recover_interrupted_outbox,
    retry_outbound,
)
from tgbrain import Settings, db


class FakeTelegram:
    def __init__(self, fail=False):
        self.fail = fail
        self.messages = []
        self.files = []
        self.next_id = 100

    def _message(self):
        self.next_id += 1
        return SimpleNamespace(id=self.next_id)

    async def send_message(self, entity, text, **kwargs):
        if self.fail:
            raise ConnectionError("Telegram unavailable")
        self.messages.append((entity, text, kwargs))
        return self._message()

    async def send_file(self, entity, files, **kwargs):
        if self.fail:
            raise ConnectionError("Telegram unavailable")
        paths = files if isinstance(files, list) else [files]
        self.files.append((entity, list(paths), kwargs))
        return [self._message() for _ in paths]


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.config = Settings(
            api_id=1,
            api_hash="test",
            phone=None,
            chat_id=777000,
            session_path=root / "session",
            db_path=root / "archive.sqlite3",
            media_dir=root / "media",
            enable_embeddings=False,
            enable_vision=False,
            ollama_url="http://127.0.0.1:11434",
            embed_model="embeddinggemma",
            vision_model="gemma3:4b",
        )
        self.connection = db(self.config.db_path)

    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def test_enqueue_preserves_text_and_stages_original_file(self):
        text = "Title\n\nExact paragraph https://example.com"
        row_id = enqueue_outbound(
            self.connection,
            self.config,
            text=text,
            attachments=[("meme image.png", "image/png", b"image-bytes")],
            client_token="first",
        )

        row = dict(
            self.connection.execute(
                "SELECT * FROM outbound_queue WHERE id = ?",
                (row_id,),
            ).fetchone()
        )
        files = json.loads(row["files_json"])
        self.assertEqual(row["text"], text)
        self.assertEqual(row["status"], "queued")
        self.assertEqual(Path(files[0]["path"]).read_bytes(), b"image-bytes")

    def test_media_caption_is_sent_and_confirmed_before_cleanup(self):
        row_id = enqueue_outbound(
            self.connection,
            self.config,
            text="A caption with https://example.com",
            attachments=[("meme.png", "image/png", b"image-bytes")],
        )
        row = claim_next_outbound(self.connection)
        telegram = FakeTelegram()

        with patch("outbox.ingest", new=AsyncMock(return_value=True)) as ingest:
            delivered = asyncio.run(
                deliver_outbound(
                    telegram,
                    self.config,
                    self.connection,
                    object(),
                    row,
                )
            )

        saved = self.connection.execute(
            "SELECT * FROM outbound_queue WHERE id = ?",
            (row_id,),
        ).fetchone()
        self.assertTrue(delivered)
        self.assertEqual(saved["status"], "sent")
        self.assertEqual(
            telegram.files[0][2]["caption"],
            "A caption with https://example.com",
        )
        self.assertIsNone(telegram.files[0][2]["parse_mode"])
        self.assertFalse(Path(telegram.files[0][1][0]).exists())
        ingest.assert_awaited_once()

    def test_long_text_is_sent_before_files_without_becoming_a_caption(self):
        body = "A" * 1500
        enqueue_outbound(
            self.connection,
            self.config,
            text=body,
            attachments=[("document.pdf", "application/pdf", b"pdf")],
        )
        row = claim_next_outbound(self.connection)
        telegram = FakeTelegram()

        with patch("outbox.ingest", new=AsyncMock(return_value=True)):
            asyncio.run(
                deliver_outbound(
                    telegram,
                    self.config,
                    self.connection,
                    object(),
                    row,
                )
            )

        self.assertEqual(telegram.messages[0][1], body)
        self.assertIsNone(telegram.files[0][2]["caption"])

    def test_transient_failures_retry_then_surface_a_manual_retry(self):
        row_id = enqueue_outbound(
            self.connection,
            self.config,
            text="Keep this safe while Telegram reconnects.",
        )
        telegram = FakeTelegram(fail=True)
        for _ in range(MAX_RETRIES):
            self.connection.execute(
                "UPDATE outbound_queue SET next_attempt_at = NULL WHERE id = ?",
                (row_id,),
            )
            self.connection.commit()
            row = claim_next_outbound(self.connection)
            self.assertIsNotNone(row)
            asyncio.run(
                deliver_outbound(
                    telegram,
                    self.config,
                    self.connection,
                    object(),
                    row,
                )
            )

        failed = self.connection.execute(
            "SELECT status, attempts FROM outbound_queue WHERE id = ?",
            (row_id,),
        ).fetchone()
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["attempts"], MAX_RETRIES)
        self.assertTrue(retry_outbound(self.connection, row_id))

    def test_restart_recovers_an_interrupted_claim(self):
        enqueue_outbound(self.connection, self.config, text="Resume me")
        claim_next_outbound(self.connection)

        self.assertEqual(recover_interrupted_outbox(self.connection), 1)
        recovered = claim_next_outbound(self.connection)
        self.assertEqual(recovered["text"], "Resume me")

    def test_malformed_attachment_data_is_retried_without_crashing(self):
        row_id = enqueue_outbound(
            self.connection,
            self.config,
            text="Keep the worker alive",
        )
        self.connection.execute(
            "UPDATE outbound_queue SET files_json = ? WHERE id = ?",
            ("not-json", row_id),
        )
        self.connection.commit()

        row = claim_next_outbound(self.connection)
        delivered = asyncio.run(
            deliver_outbound(
                FakeTelegram(),
                self.config,
                self.connection,
                object(),
                row,
            )
        )

        saved = self.connection.execute(
            "SELECT status, attempts, error FROM outbound_queue WHERE id = ?",
            (row_id,),
        ).fetchone()
        self.assertFalse(delivered)
        self.assertEqual(saved["status"], "queued")
        self.assertEqual(saved["attempts"], 1)
        self.assertTrue(saved["error"])


if __name__ == "__main__":
    unittest.main()
