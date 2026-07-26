# Product Roadmap

Telegram Brain should turn a private send-to-self chat into dependable working
memory. The product is successful when a saved thought, document, image or link
can be found again quickly, understood in context and acted on without manual
filing.

## First: Make every saved item retrievable

### 1. Full-content indexing

Extract text from PDFs and documents, OCR images, transcribe voice notes and
store useful link metadata. Search quality cannot exceed what the archive can
read. The current archive has 359 attachments and 240 links, while attachment
content extraction is not yet enabled.

### 2. Unified captures

Treat adjacent messages, captions and media sent as one thought as a single
capture throughout search and the library. Telegram encourages splitting one
idea across several messages; presenting those fragments separately loses the
author's intended context.

### 3. Retrieval confidence

Show why a result matched, which parts were indexed and whether an attachment
still needs processing. Trust grows when the user can tell the difference
between "not found" and "not read yet."

## Later: Turn retrieval into a useful working loop

### 4. Review inbox

Create a fast queue for uncategorised, low-confidence and newly captured items,
with one-click corrections and bulk actions. Automatic filing should reduce
work, while important mistakes remain easy to fix.

### 5. Hybrid search and related memories

Combine exact FTS matches with local embeddings, then surface genuinely related
captures. Semantic ranking becomes valuable only after documents and media are
represented in the searchable corpus.

### 6. Resurfacing

Add lightweight daily or weekly recall, project views and "what did I save
about this?" briefs. This closes the loop between collecting information and
using it.

### 7. Reliability and recovery

Add login-time services, encrypted backups, import/export and visible integrity
checks. The watcher already catches up after downtime; this phase makes the
system durable enough to become long-term memory.

## Last: Add leverage without weakening trust

### 8. Generative workflows

Draft notes, outlines and summaries from selected source material, always with
links back to the underlying captures. Generation belongs after retrieval so it
can be grounded and inspectable.

### 9. Cloud and collaboration

Consider encrypted multi-device access or shared spaces only when there is a
real need. Local-first storage is currently a product advantage because the
archive contains credentials, personal documents and private context.

### 10. Heavy infrastructure

Reserve a dedicated vector database, fine-tuned models and autonomous agents
for evidence that the archive has outgrown SQLite and local models. At the
current personal-library scale they would add complexity before adding value.
