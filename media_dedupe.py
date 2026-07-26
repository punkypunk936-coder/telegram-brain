from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageChops, ImageOps, ImageStat


FINGERPRINT_VERSION = 1
VISUAL_HAMMING_LIMIT = 4
VISUAL_MEAN_LIMIT = 2.4
VISUAL_CHANGED_LIMIT = 0.012
ASPECT_RELATIVE_TOLERANCE = 0.015


def _rgb_image(path: Path) -> Image.Image:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)
        if image.mode in {"RGBA", "LA"} or (
            image.mode == "P" and "transparency" in image.info
        ):
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", rgba.size, "white")
            image = Image.alpha_composite(background, rgba).convert("RGB")
        else:
            image = image.convert("RGB")
        return image.copy()


def _difference_hash(image: Image.Image) -> str:
    sample = image.convert("L").resize(
        (9, 8),
        Image.Resampling.LANCZOS,
    )
    pixels = list(sample.getdata())
    bits = 0
    for row in range(8):
        for column in range(8):
            left = pixels[row * 9 + column]
            right = pixels[row * 9 + column + 1]
            bits = (bits << 1) | int(left > right)
    return f"{bits:016x}"


def hamming_distance(left: str, right: str) -> int:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except (TypeError, ValueError):
        return 64


def fingerprint_media(path: str | Path, media_type: str | None) -> dict:
    resolved = Path(path)
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    result = {
        "media_sha256": digest.hexdigest(),
        "visual_hash": None,
        "media_aspect": None,
        "fingerprint_version": FINGERPRINT_VERSION,
    }
    if media_type != "image":
        return result

    try:
        image = _rgb_image(resolved)
        width, height = image.size
        if width and height:
            result["visual_hash"] = _difference_hash(image)
            result["media_aspect"] = width / height
    except (OSError, ValueError):
        pass
    return result


def visual_distance(left_path: str | Path, right_path: str | Path) -> float:
    left = _rgb_image(Path(left_path)).resize(
        (128, 128),
        Image.Resampling.LANCZOS,
    ).convert("L")
    right = _rgb_image(Path(right_path)).resize(
        (128, 128),
        Image.Resampling.LANCZOS,
    ).convert("L")
    difference = ImageChops.difference(left, right)
    mean = float(ImageStat.Stat(difference).mean[0])
    changed = sum(value > 12 for value in difference.getdata()) / (
        128 * 128
    )
    return max(
        mean / VISUAL_MEAN_LIMIT,
        changed / VISUAL_CHANGED_LIMIT,
    )


def _aspect_matches(left: float | None, right: float | None) -> bool:
    if not left or not right:
        return False
    return abs(left - right) / max(left, right) <= ASPECT_RELATIVE_TOLERANCE


def find_duplicate(
    connection,
    fingerprint: dict,
    media_path: str | Path,
    media_type: str | None,
    *,
    chat_id: int | None = None,
    message_id: int | None = None,
) -> dict | None:
    excluded_chat_id = chat_id if chat_id is not None else -(2**63)
    excluded_message_id = message_id if message_id is not None else -1
    sha256 = fingerprint.get("media_sha256")
    if sha256:
        exact = connection.execute(
            """
            SELECT id, media_path, date_utc, message_id
            FROM messages
            WHERE media_sha256 = ?
              AND duplicate_of_id IS NULL
              AND media_path IS NOT NULL
              AND NOT (chat_id = ? AND message_id = ?)
            ORDER BY date_utc, message_id, id
            LIMIT 1
            """,
            (sha256, excluded_chat_id, excluded_message_id),
        ).fetchone()
        if exact:
            return {
                "id": int(exact["id"]),
                "media_path": exact["media_path"],
                "reason": "exact",
                "distance": 0.0,
            }

    visual_hash = fingerprint.get("visual_hash")
    aspect = fingerprint.get("media_aspect")
    if media_type != "image" or not visual_hash or not aspect:
        return None

    candidates = connection.execute(
        """
        SELECT id, media_path, visual_hash, media_aspect,
               date_utc, message_id
        FROM messages
        WHERE media_type = 'image'
          AND duplicate_of_id IS NULL
          AND visual_hash IS NOT NULL
          AND media_aspect IS NOT NULL
          AND media_path IS NOT NULL
          AND NOT (chat_id = ? AND message_id = ?)
        ORDER BY date_utc, message_id, id
        """,
        (excluded_chat_id, excluded_message_id),
    ).fetchall()
    for candidate in candidates:
        if not _aspect_matches(aspect, candidate["media_aspect"]):
            continue
        if (
            hamming_distance(visual_hash, candidate["visual_hash"])
            > VISUAL_HAMMING_LIMIT
        ):
            continue
        candidate_path = Path(candidate["media_path"])
        if not candidate_path.exists():
            continue
        try:
            distance = visual_distance(media_path, candidate_path)
        except (OSError, ValueError):
            continue
        if distance <= 1:
            return {
                "id": int(candidate["id"]),
                "media_path": candidate["media_path"],
                "reason": "visual",
                "distance": distance,
            }
    return None


def backfill_media_duplicates(connection) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT id, media_path, media_type, date_utc, message_id
        FROM messages
        WHERE media_path IS NOT NULL
        ORDER BY date_utc, message_id, id
        """
    ).fetchall()
    exact_canonicals: dict[str, dict] = {}
    visual_canonicals: list[dict] = []
    counts = {
        "scanned": 0,
        "canonical": 0,
        "exact": 0,
        "visual": 0,
        "unavailable": 0,
    }

    connection.execute(
        """
        UPDATE messages
        SET duplicate_of_id = NULL, duplicate_reason = NULL,
            duplicate_distance = NULL
        """
    )
    for row in rows:
        path = Path(row["media_path"])
        if not path.exists():
            counts["unavailable"] += 1
            continue
        try:
            fingerprint = fingerprint_media(path, row["media_type"])
        except OSError:
            counts["unavailable"] += 1
            continue

        counts["scanned"] += 1
        duplicate = exact_canonicals.get(fingerprint["media_sha256"])
        reason = "exact" if duplicate else None
        distance = 0.0 if duplicate else None

        if not duplicate and fingerprint.get("visual_hash"):
            for candidate in visual_canonicals:
                if not _aspect_matches(
                    fingerprint.get("media_aspect"),
                    candidate.get("media_aspect"),
                ):
                    continue
                if (
                    hamming_distance(
                        fingerprint["visual_hash"],
                        candidate["visual_hash"],
                    )
                    > VISUAL_HAMMING_LIMIT
                ):
                    continue
                try:
                    candidate_distance = visual_distance(
                        path,
                        candidate["media_path"],
                    )
                except (OSError, ValueError):
                    continue
                if candidate_distance <= 1:
                    duplicate = candidate
                    reason = "visual"
                    distance = candidate_distance
                    break

        connection.execute(
            """
            UPDATE messages
            SET media_sha256 = ?, visual_hash = ?, media_aspect = ?,
                fingerprint_version = ?, duplicate_of_id = ?,
                duplicate_reason = ?, duplicate_distance = ?
            WHERE id = ?
            """,
            (
                fingerprint["media_sha256"],
                fingerprint.get("visual_hash"),
                fingerprint.get("media_aspect"),
                FINGERPRINT_VERSION,
                duplicate["id"] if duplicate else None,
                reason,
                distance,
                row["id"],
            ),
        )
        if duplicate:
            counts[reason] += 1
            continue

        canonical = {
            "id": int(row["id"]),
            "media_path": str(path),
            **fingerprint,
        }
        exact_canonicals[fingerprint["media_sha256"]] = canonical
        if fingerprint.get("visual_hash"):
            visual_canonicals.append(canonical)
        counts["canonical"] += 1

    connection.commit()
    return counts
