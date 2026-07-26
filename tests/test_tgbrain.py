import json
import tempfile
import unittest
from pathlib import Path

from tgbrain import (
    Settings,
    build_indexed_text,
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

    def test_adjacent_parts_are_returned_as_one_capture(self):
        timestamp = "2026-07-20T12:00:00+00:00"
        self.insert(
            30,
            "A durable semiconductor thesis with several supporting facts.",
            date_utc=timestamp,
        )
        self.insert(
            31,
            "The second part adds positioning and price-action context.",
            media_type="image",
            file_name="chart.png",
            date_utc=timestamp,
        )

        rows = search(self.connection, self.config, "", limit=20)

        capture = next(row for row in rows if 30 in row["message_ids"])
        self.assertEqual(capture["capture_size"], 2)
        self.assertIn(31, capture["message_ids"])
        self.assertIn("second part", capture["text"])

    def test_extracted_content_survives_a_telegram_resync(self):
        self.insert(
            40,
            "Document caption",
            media_type="pdf",
            file_name="research.pdf",
        )
        row = self.connection.execute(
            "SELECT * FROM messages WHERE message_id = 40"
        ).fetchone()
        record = dict(row)
        record["extracted_text"] = "Rare document phrase quartzsignal"
        record["content_status"] = "ready"
        record["enrichment_version"] = 1
        record["indexed_text"] = build_indexed_text(record)
        self.connection.execute(
            """
            UPDATE messages
            SET extracted_text = ?, content_status = ?,
                enrichment_version = ?, indexed_text = ?
            WHERE id = ?
            """,
            (
                record["extracted_text"],
                record["content_status"],
                record["enrichment_version"],
                record["indexed_text"],
                record["id"],
            ),
        )
        self.connection.commit()

        self.insert(
            40,
            "Document caption",
            media_type="pdf",
            file_name="research.pdf",
        )
        refreshed = self.connection.execute(
            """
            SELECT extracted_text, content_status
            FROM messages
            WHERE message_id = 40
            """
        ).fetchone()
        self.assertEqual(
            refreshed["extracted_text"],
            "Rare document phrase quartzsignal",
        )
        self.assertEqual(refreshed["content_status"], "ready")

    def test_search_explains_document_text_match(self):
        self.insert(
            50,
            "A saved PDF",
            media_type="pdf",
            file_name="memo.pdf",
        )
        row = dict(
            self.connection.execute(
                "SELECT * FROM messages WHERE message_id = 50"
            ).fetchone()
        )
        row["extracted_text"] = "The uncommon marker is auroracircuit."
        row["content_status"] = "ready"
        row["indexed_text"] = build_indexed_text(row)
        self.connection.execute(
            """
            UPDATE messages
            SET extracted_text = ?, content_status = ?, indexed_text = ?
            WHERE id = ?
            """,
            (
                row["extracted_text"],
                row["content_status"],
                row["indexed_text"],
                row["id"],
            ),
        )
        self.connection.commit()

        results = search(
            self.connection,
            self.config,
            "auroracircuit",
            limit=20,
        )

        self.assertEqual(len(results), 1)
        self.assertIn(
            "Matched text extracted from a document",
            results[0]["_match_reasons"],
        )

    def test_multi_term_search_prefers_complete_match(self):
        self.insert(60, "Social status can be measured in followers")
        self.insert(61, "A separate note about social media")
        self.insert(62, "Another note discussing follower growth")

        results = search(
            self.connection,
            self.config,
            "social status followers",
            limit=20,
        )

        self.assertEqual([row["message_id"] for row in results], [60])


if __name__ == "__main__":
    unittest.main()
