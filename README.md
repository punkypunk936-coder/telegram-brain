# Telegram Brain

A private, local library for one Telegram chat of your choice, including
Telegram's service chat when you use it as a personal inbox.

## What it does

- Imports the selected chat's existing history and media.
- Catches up quietly and watches for new or edited messages.
- Reads images with local macOS OCR and extracts text from PDFs, Office files
  and common text formats.
- Transcribes voice notes locally with whisper.cpp and fetches public link
  titles and descriptions.
- Combines adjacent message parts and media albums into one capture.
- Shows live-sync health inside the dashboard.
- Opens on a history-driven bucket homepage with the newest classified capture.
- Searches original text, OCR, documents, transcripts, link metadata and
  filenames with SQLite FTS5.
- Explains why each result matched and whether every part has been indexed.
- Organises results by topic, type and date.
- Carries topic context across adjacent parts of the same long capture.
- Lets you star useful items, add private notes and correct a topic.
- Detects credentials and sensitive documents, then hides them from normal results.
- Optionally adds local semantic search and image understanding through Ollama.

The collector is read-only: it does not send, edit, delete or forward Telegram
messages.

## Setup on Mac

### 1. Get Telegram credentials

Sign in at `my.telegram.org`, open **API development tools**, create an
application, and copy the `api_id` and `api_hash`.

### 2. Install

```bash
cd telegram-second-brain
chmod +x setup_mac.sh
./setup_mac.sh
```

Edit `.env`:

```dotenv
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_PHONE=+91...
```

### 3. Select the chat

```bash
source .venv/bin/activate
python list_chats.py
```

Copy the numeric ID of the intended chat into `.env`:

```dotenv
TELEGRAM_CHAT_ID=-1001234567890
```

Use the ID rather than only the title because titles can collide or change.

### 4. Import the existing history

```bash
python sync_history.py --full
```

For only the messages added since the previous import:

```bash
python sync_history.py
```

### 5. Start the app

```bash
chmod +x run.sh
./run.sh
```

Open `http://127.0.0.1:8501`. The dashboard starts the live watcher in the
background, catches up on anything missed while it was closed and progressively
indexes the existing archive.

### 6. Enable local voice transcription

OCR and document extraction work after the normal setup. Voice notes need a
one-time local whisper.cpp build and model download:

```bash
./setup_content_tools.sh
```

The model runs locally with Apple Metal acceleration. No audio is sent to a
hosted transcription service.

## Optional local AI

Install Ollama, then:

```bash
ollama pull embeddinggemma
ollama pull gemma3:4b
```

Enable the models in `.env`:

```dotenv
ENABLE_EMBEDDINGS=true
ENABLE_VISION=true
```

Exact keyword search remains available without Ollama.

## Privacy

Keep these private and never commit them to GitHub:

- `.env`
- `data/telegram_session.session`
- downloaded media
- the SQLite database

The app binds to `127.0.0.1`, so it is not exposed to the local network.
Credential masking is a display safeguard, not encryption: the original message
still exists in Telegram and in the local SQLite archive. Do not use the chat as
a password manager.

Image, document and audio extraction remain on the Mac. Link metadata requires
an HTTP request to the saved public URL; credential-bearing, private-network and
already-protected links are never fetched.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Current limits

- JavaScript-only or login-protected pages may expose only their original URL.
- Animated GIF text and uncommon proprietary document formats remain
  filename-searchable when no safe local extractor is available.
- Private chat deep links are inconsistent, so results show the Telegram message
  ID.
- Semantic search uses a local brute-force vector scan, which is suitable for a
  personal archive of this size.

The prioritised product plan lives in [ROADMAP.md](ROADMAP.md).
