import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

import tgbrain
from content_indexing import (
    apply_enrichment,
    enrich_record,
    next_pending,
    pending_count,
)
from media_dedupe import (
    backfill_media_duplicates,
    find_duplicate,
    fingerprint_media,
)
from tgbrain import (
    Settings,
    VIDEO_VISION_PROMPT_VERSION,
    VISION_PROMPT_VERSION,
    build_indexed_text,
    db,
    detect_category,
    is_gif_media,
    mask_sensitive_text,
    search,
    sensitive_status,
    set_item_state,
    sync_pinned_messages,
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
        media_path=None,
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
                "media_path": str(media_path) if media_path else None,
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
        public_test_mnemonic = " ".join(["abandon"] * 11 + ["about"])
        self.assertTrue(sensitive_status(public_test_mnemonic)[0])
        self.assertEqual(
            mask_sensitive_text(public_test_mnemonic),
            "[hidden recovery phrase]",
        )
        self.assertFalse(
            sensitive_status(
                "This ordinary twelve word sentence is not a wallet recovery "
                "phrase at all"
            )[0]
        )
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

    def test_personal_search_note_becomes_searchable(self):
        self.insert(
            3,
            "",
            media_type="image",
            file_name="IMG_003.jpg",
        )
        row = self.connection.execute(
            "SELECT id FROM messages WHERE message_id = 3"
        ).fetchone()
        set_item_state(
            self.connection,
            row["id"],
            note="Jensen Huang reaction meme for an NVIDIA launch",
        )

        results = search(
            self.connection,
            self.config,
            "Jensen Huang",
            limit=20,
        )

        self.assertEqual([item["message_id"] for item in results], [3])
        self.assertIn("Matched your search note", results[0]["_match_reasons"])

    def test_public_figure_role_alias_can_recover_an_unnamed_image(self):
        self.insert(
            4,
            "",
            media_type="image",
            file_name="political-meme.jpg",
        )
        row = dict(
            self.connection.execute(
                "SELECT * FROM messages WHERE message_id = 4"
            ).fetchone()
        )
        description = (
            "Summary: A man in a suit smiles in front of the Chinese flag.\n"
            "People: Unknown\n"
            "Setting: Government event"
        )
        row["vision_text"] = description
        row["indexed_text"] = build_indexed_text(row)
        self.connection.execute(
            """
            UPDATE messages
            SET vision_text = ?, indexed_text = ?
            WHERE id = ?
            """,
            (description, row["indexed_text"], row["id"]),
        )
        self.connection.commit()

        results = search(
            self.connection,
            self.config,
            "Xi Jingping",
            limit=20,
        )

        self.assertEqual([item["message_id"] for item in results], [4])
        self.assertIn(
            "Possible match from a known role or visual context",
            results[0]["_match_reasons"],
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

    def test_pinned_scope_returns_the_entire_capture(self):
        timestamp = "2026-07-20T12:00:00+00:00"
        self.insert(
            32,
            "Pinned thesis with the core reasoning.",
            date_utc=timestamp,
        )
        self.insert(
            33,
            "The chart attached to the same thought.",
            media_type="image",
            file_name="thesis-chart.png",
            date_utc=timestamp,
        )
        self.connection.execute(
            "UPDATE messages SET is_pinned = 1 WHERE message_id = 32"
        )
        self.connection.commit()

        rows = search(
            self.connection,
            self.config,
            "",
            limit=20,
            pinned_only=True,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["is_pinned"], 1)
        self.assertEqual(rows[0]["capture_size"], 2)
        self.assertEqual(rows[0]["message_ids"], [32, 33])

    def test_pinned_sync_clears_stale_pin_flags(self):
        self.insert(34, "An old Telegram pin")
        self.insert(35, "The only current Telegram pin")
        self.connection.execute(
            "UPDATE messages SET is_pinned = 1 WHERE message_id = 34"
        )
        self.connection.commit()

        class FakeTelegram:
            async def iter_messages(self, _entity, **_kwargs):
                yield SimpleNamespace(id=35)

        count = asyncio.run(
            sync_pinned_messages(
                FakeTelegram(),
                self.config,
                self.connection,
                object(),
            )
        )
        states = {
            row["message_id"]: row["is_pinned"]
            for row in self.connection.execute(
                """
                SELECT message_id, is_pinned
                FROM messages
                WHERE message_id IN (34, 35)
                """
            ).fetchall()
        }

        self.assertEqual(count, 1)
        self.assertEqual(states, {34: 0, 35: 1})

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

    def test_visual_reread_reuses_existing_media_extraction(self):
        root = Path(self.temp_dir.name)
        video_path = root / "already-transcribed.mp4"
        video_path.write_bytes(b"test video placeholder")
        self.insert(
            41,
            "",
            media_type="video",
            media_path=video_path,
            file_name=video_path.name,
        )
        self.connection.execute(
            """
            UPDATE messages
            SET extracted_text = ?, enrichment_version = ?,
                content_status = 'ready'
            WHERE message_id = 41
            """,
            ("Existing transcript", tgbrain.ENRICHMENT_VERSION),
        )
        self.connection.commit()
        row = dict(
            self.connection.execute(
                "SELECT * FROM messages WHERE message_id = 41"
            ).fetchone()
        )

        with patch("content_indexing._extract_media") as extractor:
            result = enrich_record(self.config, row)

        extractor.assert_not_called()
        self.assertEqual(result.extracted_text, "Existing transcript")
        self.assertEqual(result.content_status, "ready")

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

    def test_image_description_is_indexed_and_searchable(self):
        root = Path(self.temp_dir.name)
        image_path = root / "jensen-meme.png"
        self._meme_canvas().save(image_path)
        self.insert(
            55,
            "",
            media_type="image",
            media_path=image_path,
            file_name=image_path.name,
        )
        row = dict(
            self.connection.execute(
                "SELECT * FROM messages WHERE message_id = 55"
            ).fetchone()
        )
        vision_config = Settings(
            **{
                **self.config.__dict__,
                "enable_vision": True,
            }
        )
        with (
            patch(
                "content_indexing.extract_with_vision",
                return_value="",
            ),
            patch(
                "content_indexing.describe",
                return_value=(
                    "Meme featuring NVIDIA CEO Jensen Huang in his leather "
                    "jacket, joking about GPU demand."
                ),
            ),
        ):
            apply_enrichment(
                self.connection,
                enrich_record(vision_config, row),
            )

        results = search(
            self.connection,
            self.config,
            "Jensen Huang",
            limit=20,
        )

        self.assertEqual([item["message_id"] for item in results], [55])
        self.assertIn(
            "Matched an AI image description",
            results[0]["_match_reasons"],
        )
        indexed = self.connection.execute(
            """
            SELECT vision_model, vision_prompt_version
            FROM messages WHERE message_id = 55
            """
        ).fetchone()
        self.assertEqual(indexed["vision_model"], vision_config.vision_model)
        self.assertEqual(
            indexed["vision_prompt_version"],
            VISION_PROMPT_VERSION,
        )

    def test_changed_vision_model_queues_existing_images_for_a_reread(self):
        root = Path(self.temp_dir.name)
        image_path = root / "old-description.png"
        self._meme_canvas().save(image_path)
        self.insert(
            57,
            "",
            media_type="image",
            media_path=image_path,
            file_name=image_path.name,
        )
        self.connection.execute(
            """
            UPDATE messages
            SET vision_text = ?, vision_model = ?,
                vision_prompt_version = ?, content_status = 'ready',
                enrichment_version = ?
            WHERE message_id = 57
            """,
            (
                "A generic image description from the old reader.",
                "moondream:1.8b",
                1,
                tgbrain.ENRICHMENT_VERSION,
            ),
        )
        self.connection.commit()

        count = pending_count(
            self.connection,
            require_vision=True,
            vision_model="qwen3-vl:2b-instruct",
        )
        queued = next_pending(
            self.connection,
            limit=5,
            require_vision=True,
            vision_model="qwen3-vl:2b-instruct",
        )

        self.assertEqual(count, 1)
        self.assertEqual([row["message_id"] for row in queued], [57])

    def test_video_scene_description_is_indexed_and_searchable(self):
        root = Path(self.temp_dir.name)
        video_path = root / "launch-demo.mp4"
        video_path.write_bytes(b"test video placeholder")
        self.insert(
            62,
            "",
            media_type="video",
            media_path=video_path,
            file_name=video_path.name,
        )
        row = dict(
            self.connection.execute(
                "SELECT * FROM messages WHERE message_id = 62"
            ).fetchone()
        )
        vision_config = Settings(
            **{**self.config.__dict__, "enable_vision": True}
        )
        with (
            patch("content_indexing._extract_media", return_value=""),
            patch(
                "content_indexing.describe_video",
                return_value=(
                    "Summary: Jensen Huang presents a GPU on stage.\n"
                    "People: Jensen Huang\n"
                    "Visible text: NVIDIA Blackwell\n"
                    "Objects: GPU, stage screen\n"
                    "Setting: Technology keynote\n"
                    "Sequence: He lifts the GPU and addresses the audience.\n"
                    "Meme context: None\n"
                    "Search terms: Jensen Huang keynote NVIDIA GPU Blackwell"
                ),
            ),
        ):
            apply_enrichment(
                self.connection,
                enrich_record(vision_config, row),
            )

        results = search(
            self.connection,
            self.config,
            "Jensen Huang video",
            limit=20,
        )

        self.assertEqual([item["message_id"] for item in results], [62])
        self.assertIn(
            "Matched people, scenes or text seen in a video",
            results[0]["_match_reasons"],
        )
        indexed = self.connection.execute(
            """
            SELECT vision_model, vision_prompt_version, content_status
            FROM messages WHERE message_id = 62
            """
        ).fetchone()
        self.assertEqual(indexed["vision_model"], vision_config.vision_model)
        self.assertEqual(
            indexed["vision_prompt_version"],
            VIDEO_VISION_PROMPT_VERSION,
        )
        self.assertEqual(indexed["content_status"], "ready")

    def test_videos_are_not_all_classified_as_gifs(self):
        self.assertFalse(
            is_gif_media("video", "screen-recording.mp4", "video/mp4")
        )
        self.assertTrue(
            is_gif_media("video", "reaction.gif.mp4", "video/mp4")
        )
        self.assertTrue(is_gif_media("image", "reaction.gif", "image/gif"))

    def test_existing_video_without_visual_index_is_queued(self):
        root = Path(self.temp_dir.name)
        video_path = root / "silent-scene.mp4"
        video_path.write_bytes(b"test video placeholder")
        self.insert(
            63,
            "",
            media_type="video",
            media_path=video_path,
            file_name=video_path.name,
        )
        self.connection.execute(
            """
            UPDATE messages
            SET content_status = 'ready', enrichment_version = ?,
                vision_text = '', vision_model = '',
                vision_prompt_version = 0
            WHERE message_id = 63
            """,
            (tgbrain.ENRICHMENT_VERSION,),
        )
        self.connection.commit()

        count = pending_count(
            self.connection,
            require_vision=True,
            vision_model=self.config.vision_model,
        )
        queued = next_pending(
            self.connection,
            limit=5,
            require_vision=True,
            vision_model=self.config.vision_model,
        )

        self.assertEqual(count, 1)
        self.assertEqual([row["message_id"] for row in queued], [63])

    def test_semantic_search_finds_a_described_image_without_shared_words(self):
        self.insert(58, "", media_type="image", file_name="capture.jpg")
        row = dict(
            self.connection.execute(
                "SELECT * FROM messages WHERE message_id = 58"
            ).fetchone()
        )
        row["vision_text"] = (
            "A national official speaking beside ceremonial flags."
        )
        row["indexed_text"] = build_indexed_text(row)
        self.connection.execute(
            """
            UPDATE messages
            SET vision_text = ?, indexed_text = ?, embedding_json = ?,
                embedding_model = ?
            WHERE id = ?
            """,
            (
                row["vision_text"],
                row["indexed_text"],
                json.dumps([1.0, 0.0]),
                self.config.embed_model,
                row["id"],
            ),
        )
        self.connection.commit()
        semantic_config = Settings(
            **{**self.config.__dict__, "enable_embeddings": True}
        )

        with patch("tgbrain.embed", return_value=[1.0, 0.0]):
            results = search(
                self.connection,
                semantic_config,
                "Asian leader at a government event",
                limit=20,
            )

        self.assertEqual([item["message_id"] for item in results], [58])
        self.assertIn(
            "Related by semantic meaning",
            results[0]["_match_reasons"],
        )

    def test_semantic_name_search_allows_typos_but_rejects_other_people(self):
        records = (
            (59, "People: Donald Trump\nSummary: A political meme."),
            (60, "People: Xi Jinping\nSummary: A formal public event."),
        )
        for message_id, description in records:
            self.insert(
                message_id,
                "",
                media_type="image",
                file_name=f"person-{message_id}.jpg",
            )
            row = dict(
                self.connection.execute(
                    "SELECT * FROM messages WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
            )
            row["vision_text"] = description
            row["indexed_text"] = build_indexed_text(row)
            self.connection.execute(
                """
                UPDATE messages
                SET vision_text = ?, indexed_text = ?, embedding_json = ?,
                    embedding_model = ?
                WHERE id = ?
                """,
                (
                    description,
                    row["indexed_text"],
                    json.dumps([1.0, 0.0]),
                    self.config.embed_model,
                    row["id"],
                ),
            )
        self.connection.commit()
        semantic_config = Settings(
            **{**self.config.__dict__, "enable_embeddings": True}
        )

        with patch("tgbrain.embed", return_value=[1.0, 0.0]):
            results = search(
                self.connection,
                semantic_config,
                "xi jingping meme",
                limit=20,
            )

        self.assertEqual([item["message_id"] for item in results], [60])

    def test_semantic_name_search_rejects_unrelated_text(self):
        self.insert(61, "A generic reaction with no named person")
        row = self.connection.execute(
            "SELECT id FROM messages WHERE message_id = 61"
        ).fetchone()
        self.connection.execute(
            """
            UPDATE messages
            SET embedding_json = ?, embedding_model = ?
            WHERE id = ?
            """,
            (
                json.dumps([1.0, 0.0]),
                self.config.embed_model,
                row["id"],
            ),
        )
        self.connection.commit()
        semantic_config = Settings(
            **{**self.config.__dict__, "enable_embeddings": True}
        )

        with patch("tgbrain.embed", return_value=[1.0, 0.0]):
            results = search(
                self.connection,
                semantic_config,
                "Jensen Huang",
                limit=20,
            )

        self.assertEqual(results, [])

    def test_unhelpful_image_description_is_not_indexed(self):
        root = Path(self.temp_dir.name)
        image_path = root / "unhelpful.png"
        self._meme_canvas().save(image_path)
        self.insert(
            56,
            "",
            media_type="image",
            media_path=image_path,
            file_name=image_path.name,
        )
        row = dict(
            self.connection.execute(
                "SELECT * FROM messages WHERE message_id = 56"
            ).fetchone()
        )
        vision_config = Settings(
            **{
                **self.config.__dict__,
                "enable_vision": True,
            }
        )
        with (
            patch(
                "content_indexing.extract_with_vision",
                return_value="",
            ),
            patch(
                "content_indexing.describe",
                return_value="[0.1, 0.2]",
            ),
        ):
            result = enrich_record(vision_config, row)

        self.assertEqual(result.vision_text, "")
        self.assertEqual(result.content_status, "partial")
        self.assertIn(
            "no useful description",
            result.content_error,
        )

    def test_file_database_uses_wal_for_concurrent_services(self):
        mode = self.connection.execute(
            "PRAGMA journal_mode"
        ).fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

    def test_existing_database_adds_pinned_column_before_index(self):
        self.connection.execute("DROP INDEX idx_pinned")
        self.connection.execute(
            "ALTER TABLE messages DROP COLUMN is_pinned"
        )
        self.connection.commit()
        self.connection.close()
        tgbrain._INITIALIZED_DATABASES.discard(
            str(self.config.db_path.resolve())
        )

        self.connection = db(self.config.db_path)

        columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(messages)"
            ).fetchall()
        }
        indexes = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA index_list(messages)"
            ).fetchall()
        }
        self.assertIn("is_pinned", columns)
        self.assertIn("vision_model", columns)
        self.assertIn("vision_prompt_version", columns)
        self.assertIn("vision_attempted_at", columns)
        self.assertIn("idx_pinned", indexes)

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

    def test_multi_term_fallback_does_not_return_single_term_noise(self):
        self.insert(63, "A generic meme with no named person")

        results = search(
            self.connection,
            self.config,
            "Jensen Huang meme",
            limit=20,
        )

        self.assertEqual(results, [])

    def test_short_name_is_not_matched_inside_unrelated_words(self):
        self.insert(
            64,
            "A maxi meme about existing market proxies",
            media_type="image",
            file_name="unrelated.jpg",
        )

        results = search(
            self.connection,
            self.config,
            "xi jingping meme",
            limit=20,
        )

        self.assertEqual(results, [])

    def _meme_canvas(self, variant: bool = False) -> Image.Image:
        image = Image.new("RGB", (720, 480), "#f2f2ed")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 720, 90), fill="#172a3a")
        draw.rectangle((45, 140, 675, 420), outline="#172a3a", width=8)
        draw.ellipse((90, 185, 250, 345), fill="#27a69a")
        draw.rectangle(
            (330, 190, 620, 250),
            fill="#d84f3f" if variant else "#172a3a",
        )
        draw.rectangle((330, 285, 560, 325), fill="#d8842f")
        if variant:
            draw.rectangle((330, 345, 650, 395), fill="#111111")
        return image

    def test_exact_repeat_keeps_earliest_asset_and_hides_later_copy(self):
        root = Path(self.temp_dir.name)
        original = root / "original.png"
        repeated = root / "repeated.png"
        self._meme_canvas().save(original)
        repeated.write_bytes(original.read_bytes())
        self.insert(
            71,
            "Same meme shared again",
            media_type="image",
            media_path=repeated,
            file_name=repeated.name,
            date_utc="2026-07-02T12:00:00+00:00",
        )
        self.insert(
            70,
            "The original meme",
            media_type="image",
            media_path=original,
            file_name=original.name,
            date_utc="2026-07-01T12:00:00+00:00",
        )

        counts = backfill_media_duplicates(self.connection)
        rows = self.connection.execute(
            """
            SELECT id, message_id, duplicate_of_id, duplicate_reason
            FROM messages
            WHERE message_id IN (70, 71)
            ORDER BY message_id
            """
        ).fetchall()
        visible = search(self.connection, self.config, "", limit=20)

        self.assertEqual(counts["exact"], 1)
        self.assertIsNone(rows[0]["duplicate_of_id"])
        self.assertEqual(rows[1]["duplicate_of_id"], rows[0]["id"])
        self.assertEqual(rows[1]["duplicate_reason"], "exact")
        original_result = next(
            row for row in visible if row["message_id"] == 70
        )
        self.assertEqual(original_result["_duplicate_count"], 1)
        self.assertNotIn(71, [row["message_id"] for row in visible])

    def test_reencoded_image_is_detected_but_changed_meme_is_kept(self):
        root = Path(self.temp_dir.name)
        original = root / "meme-original.jpg"
        reencoded = root / "meme-reencoded.jpg"
        changed = root / "meme-changed.jpg"
        self._meme_canvas().save(original, quality=96)
        self._meme_canvas().save(reencoded, quality=76)
        self._meme_canvas(variant=True).save(changed, quality=88)
        self.insert(
            80,
            "Original visual",
            media_type="image",
            media_path=original,
            file_name=original.name,
            date_utc="2026-07-03T12:00:00+00:00",
        )
        self.insert(
            81,
            "Telegram re-encoded visual",
            media_type="image",
            media_path=reencoded,
            file_name=reencoded.name,
            date_utc="2026-07-04T12:00:00+00:00",
        )
        self.insert(
            82,
            "A genuinely changed meme",
            media_type="image",
            media_path=changed,
            file_name=changed.name,
            date_utc="2026-07-05T12:00:00+00:00",
        )

        counts = backfill_media_duplicates(self.connection)
        rows = {
            row["message_id"]: row
            for row in self.connection.execute(
                """
                SELECT message_id, duplicate_of_id, duplicate_reason
                FROM messages
                WHERE message_id IN (80, 81, 82)
                """
            ).fetchall()
        }

        self.assertEqual(counts["visual"], 1)
        self.assertEqual(rows[81]["duplicate_reason"], "visual")
        self.assertIsNotNone(rows[81]["duplicate_of_id"])
        self.assertIsNone(rows[82]["duplicate_of_id"])

    def test_new_exact_repeat_is_recognised_before_archive_backfill(self):
        root = Path(self.temp_dir.name)
        original = root / "instant-original.png"
        repeated = root / "instant-repeat.png"
        self._meme_canvas().save(original)
        repeated.write_bytes(original.read_bytes())
        fingerprint = fingerprint_media(original, "image")
        upsert(
            self.connection,
            {
                "chat_id": 777000,
                "message_id": 90,
                "date_utc": "2026-07-06T12:00:00+00:00",
                "sender_name": "owner",
                "text": "Canonical meme",
                "media_type": "image",
                "media_path": str(original),
                "file_name": original.name,
                "mime_type": "image/png",
                "urls_json": "[]",
                **fingerprint,
            },
        )

        duplicate = find_duplicate(
            self.connection,
            fingerprint_media(repeated, "image"),
            repeated,
            "image",
            chat_id=777000,
            message_id=91,
        )

        self.assertIsNotNone(duplicate)
        self.assertEqual(duplicate["reason"], "exact")


if __name__ == "__main__":
    unittest.main()
