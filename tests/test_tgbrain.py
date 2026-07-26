import json
import tempfile
import unittest
from pathlib import Path

from tgbrain import (
    Settings,
    db,
    detect_category,
    mask_sensitive_text,
    search,
    sensitive_status,
    set_item_state,
    upsert,
)


class TelegramBrainTests(unittest.TestCase):
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

    def insert(
        self,
        message_id,
        text,
        media_type=None,
        file_name=None,
        date_utc=None,
    ):
        upsert(
            self.connection,
            {
                "chat_id": 777000,
                "message_id": message_id,
                "date_utc": date_utc
                or f"2026-07-{message_id:02d}T12:00:00+00:00",
                "sender_name": "owner",
                "text": text,
                "media_type": media_type,
                "media_path": None,
                "file_name": file_name,
                "mime_type": None,
                "urls_json": json.dumps([]),
                "vision_text": "",
                "indexed_text": " ".join(
                    value for value in (text, file_name) if value
                ),
                "embedding_json": None,
            },
        )

    def test_category_matching_uses_words_not_substrings(self):
        self.assertEqual(
            detect_category("Optimism helps me hold on for longer"),
            "Uncategorised",
        )
        self.assertEqual(
            detect_category("BTC perp trade with a defined entry"),
            "Trading & Markets",
        )
        self.assertEqual(
            detect_category("I am applying for this growth role"),
            "Work & Career",
        )
        self.assertEqual(
            detect_category(
                "I am applying for the marketing role. It is at a crypto "
                "trading company, and I am an active perp trader."
            ),
            "Work & Career",
        )
        self.assertEqual(
            detect_category("CXL expands memory for AI inference compute"),
            "Technology & AI",
        )
        self.assertEqual(
            detect_category("https://example.com/a-useful-reference"),
            "Links & References",
        )
        self.assertEqual(
            detect_category("Order: 12 ply boards and 5 mica sheets"),
            "Personal & Admin",
        )

    def test_credentials_and_transactions_are_sensitive(self):
        credential = "sk-" + ("a" * 32) + "\nDeepSeek API key"
        self.assertTrue(sensitive_status(credential)[0])
        self.assertNotIn("sk-", mask_sensitive_text(credential))
        self.assertTrue(
            sensitive_status(
                "https://app.debridge.com/order?txHash=0x123"
            )[0]
        )
        self.assertTrue(sensitive_status("Axis Bank MPIN: 123456")[0])
        self.assertTrue(sensitive_status("12345678")[0])

    def test_sensitive_items_are_excluded_from_normal_search(self):
        self.insert(1, "A market structure research note")
        self.insert(2, "sk-" + ("b" * 32) + " API key")

        normal = search(
            self.connection,
            self.config,
            "key",
            limit=20,
        )
        protected = search(
            self.connection,
            self.config,
            "key",
            limit=20,
            include_sensitive=True,
        )

        self.assertEqual(normal, [])
        self.assertEqual(len(protected), 1)
        self.assertEqual(protected[0]["message_id"], 2)

    def test_search_and_saved_state(self):
        self.insert(1, "Perp DEX market structure note")
        self.insert(2, "A first draft for my Substack article")

        results = search(
            self.connection,
            self.config,
            "market",
            limit=20,
        )
        self.assertEqual([row["message_id"] for row in results], [1])

        set_item_state(
            self.connection,
            results[0]["id"],
            starred=True,
            note="Use this later",
            user_category="Research & Learning",
        )
        saved = search(
            self.connection,
            self.config,
            "",
            limit=20,
            starred_only=True,
        )
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["note"], "Use this later")
        self.assertEqual(
            saved[0]["display_category"],
            "Research & Learning",
        )

    def test_continuation_inherits_recent_context(self):
        timestamp = "2026-07-20T12:00:00+00:00"
        self.insert(
            10,
            "BTC market structure and perp positioning suggest a durable "
            "trading setup.",
            date_utc=timestamp,
        )
        self.insert(
            11,
            "This is the continuation of the same long note and explains "
            "why patience matters when the original reasoning remains "
            "intact across short-term volatility.",
            date_utc=timestamp,
        )
        row = self.connection.execute(
            "SELECT category FROM messages WHERE message_id = 11"
        ).fetchone()
        self.assertEqual(row["category"], "Trading & Markets")

    def test_newest_tie_is_ordered_by_message_id(self):
        timestamp = "2026-07-20T12:00:00+00:00"
        self.insert(20, "first note", date_utc=timestamp)
        self.insert(21, "second note", date_utc=timestamp)
        rows = search(self.connection, self.config, "", limit=20)
        self.assertEqual(rows[0]["message_id"], 21)


if __name__ == "__main__":
    unittest.main()
