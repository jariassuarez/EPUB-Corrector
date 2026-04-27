# EPUB Corrector

Correct grammar, punctuation, capitalization, and typos in EPUB files using a local LLM via an OpenAI-compatible API (e.g., LM Studio, Ollama, or any other local server).

The tool sends text chunks from the EPUB to the model, then presents every proposed change for your review. Accepted edits are written back to a corrected EPUB file.

> **Important:** No LLM workflow can guarantee 100% zero-context drift. Conservative safety filters (`--similarity-threshold` and `--max-change-ratio`) auto-reject edits that look too aggressive.

---

## Install

```bash
uv sync          # or: pip install -e .
```

Requires Python >= 3.14.

---

## Quick Start

### GUI mode

Launch the graphical interface:

```bash
epub-corrector-gui
```

The GUI lets you:
- Browse for input/output EPUB files
- Connect to your local LLM server and **refresh the model list**
- Adjust all processing options visually
- Toggle **auto-accept all** changes
- Review diffs side-by-side with color highlighting
- Stop processing mid-run

### CLI mode

**Interactive — pick a book from the `books/` folder:**

```bash
epub-corrector
```

**Explicit paths:**

```bash
epub-corrector input.epub output.epub
```

If `output` is omitted, it defaults to `output/<input-basename>`.  
If `--checkpoint` is omitted, it defaults to `checkpoints/<input-stem>.json`.

**Example with only an input path:**

```bash
epub-corrector "books/My Book.epub"
# Output  → output/My Book.epub
# Checkpoint → checkpoints/My Book.json
```

---

## Server Setup (LM Studio, etc.)

1. Open LM Studio (or your preferred local LLM server).
2. Start the local server.
3. Load your model.
4. The OpenAI-compatible endpoint is available at `http://127.0.0.1:1234/v1` by default.

> The GUI can automatically fetch available models from the `/models` endpoint.

---

## Environment Variables (`.env`)

Instead of passing `--base-url`, `--api-key`, and `--model` every time, you can set them in a `.env` file in the project root. CLI arguments always override `.env` values.

Copy the example file and edit it:

```bash
cp .env.example .env
```

Supported variables:

| Variable | Description | Default |
|---|---|---|
| `EPUB_CORRECTOR_BASE_URL` | OpenAI-compatible API endpoint | `http://127.0.0.1:1234/v1` |
| `EPUB_CORRECTOR_API_KEY` | API key | `lm-studio` |
| `EPUB_CORRECTOR_MODEL` | Model name | `local-model` |

Example `.env`:

```bash
EPUB_CORRECTOR_BASE_URL=http://192.168.1.100:1234/v1
EPUB_CORRECTOR_API_KEY=my-secret-key
EPUB_CORRECTOR_MODEL=mistralai/ministral-3-3b
```

---

## Interactive Review

### Terminal (CLI)

For every proposed change that passes the safety filters, a side-by-side diff is shown:

```
────────────────────────────────────────────────────────────────────────────────
ORIGINAL                                 │ PROPOSED
────────────────────────────────────────────────────────────────────────────────
He don't know what he was doing here.   │ He didn't know what he was doing here.
────────────────────────────────────────────────────────────────────────────────
chapter01.xhtml
[Enter] Accept  [n] Skip  [r] Retry  [a] Accept all  [p] Pause auto-accept
```

| Key | Action |
|---|---|
| `Enter` | Accept this change |
| `n` | Skip this change |
| `r` | Retry the batch (if the model produced a bad result) |
| `a` | Accept this and all remaining changes automatically |
| `p` | Pause auto-accept and resume manual review |
| `Ctrl+C` | Abort |

Deleted text is shown with a red background; inserted text with green.

### GUI

The review panel shows:
- **Original** and **Proposed** text side-by-side with color-coded diffs
- Document name being processed
- Buttons: **Accept (Enter)**, **Skip (n)**, **Retry (r)**, **Accept All (a)**
- Keyboard shortcuts work when the review panel is focused

---

## Change Report (`--report`)

After processing, write a CSV record of every accepted and rejected edit:

```bash
epub-corrector --report changes.csv
```

---

## Resume / Checkpoint (`--checkpoint`)

Progress is automatically checkpointed after each document. By default, the checkpoint is saved to `checkpoints/<input-stem>.json`. You can override the path explicitly:

```bash
epub-corrector --checkpoint mybook.ckpt.json
```

When using `--translate`, the language is appended to the auto-defaulted checkpoint name (e.g. `checkpoints/My Book_Spanish.json`).

Re-run the exact same command after an interruption — already-processed documents are restored from the checkpoint and skipped. Delete the checkpoint file once the corrected EPUB is written successfully.

---

## Processing a Range of Documents (`--from` / `--to`)

Process only a specific range of HTML files (chapters) within the EPUB, using 1-based indices:

```bash
# Process only documents 5 through 10
epub-corrector input.epub output.epub --from 5 --to 10

# Process from document 3 to the end
epub-corrector input.epub output.epub --from 3

# Process only the first 5 documents
epub-corrector input.epub output.epub --to 5
```

Documents outside the range are skipped entirely — they are not written to the output EPUB and are not added to context. Both flags are optional and can be used independently.

---

## Context and Cross-Chapter Memory

By default, each HTML file (chapter) in the EPUB is processed in isolation. The model sees only the text in the current batch, plus any earlier paragraphs from the **same** chapter. This keeps memory usage low, but the model has no memory of what happened in previous chapters.

### `--max-context`

Sets how many previous **segments** (individual text nodes/paragraphs) are sent as context for each correction. The model receives these segments marked `[CONTEXT]` and corrects only the `[CORRECT THIS]` segment.

```bash
epub-corrector input.epub output.epub --max-context 50
```

### `--max-context-chars`

Hard cap on the total character budget for context in a single request. Even if `--max-context` allows 50 segments, the tool will stop adding context once this character limit is reached.

```bash
epub-corrector input.epub output.epub --max-context 100 --max-context-chars 8000
```

### `--conserve-context`

Without this flag, context is reset to empty at the start of every chapter. With `--conserve-context`, the last segments from the previous chapter are carried forward into the next one, giving the model a continuous memory of the book.

```bash
epub-corrector input.epub output.epub --conserve-context --max-context 100 --max-context-chars 80000
```

> **Resuming from checkpoint:** When using `--checkpoint` together with `--conserve-context`, already-processed documents from the checkpoint are parsed and their segments are added to the conserved context, so continuity is maintained even after an interrupted run.

### Picking the right budget for your model

| Model context | Recommended starting point |
|---|---|
| 8K tokens | `--max-context 10 --max-context-chars 2000` |
| 32K tokens | `--max-context 40 --max-context-chars 8000` |
| 128K tokens | `--max-context 150 --max-context-chars 40000` |
| 250K tokens | `--max-context 200 --max-context-chars 80000` |

A rough rule of thumb is ~4 English characters per token. Leave some headroom for the system prompt, the target batch, and the model's response.

---

## All CLI Options

```
epub-corrector [input] [output] [options]

positional arguments:
  input                           Input EPUB. Omit to pick from ./books/
  output                          Output EPUB. Omit to default to output/<input-basename>

options:
  --model MODEL                   Model name loaded in LM Studio (default: local-model)
  --base-url URL                  LM Studio base URL (default: http://127.0.0.1:1234/v1)
  --api-key KEY                   API key — LM Studio accepts any value (default: lm-studio)
  --temperature FLOAT             Generation temperature (default: 0.0)
  --max-segments-per-request N    Max text segments per model call (default: 1)
  --max-chars-per-request N       Max characters per model call (default: 6000)
  --max-context N                 Number of previous segments to include as context (default: 0)
  --max-context-chars N           Maximum total characters of context per request (default: 3000)
  --conserve-context              Preserve context across documents/chapters instead of resetting per file
  --similarity-threshold FLOAT    Auto-reject edits below this similarity (default: 0.88)
  --max-change-ratio FLOAT        Auto-reject edits above this change ratio (default: 0.20)
  --max-workers N                 Maximum concurrent model requests per batch (default: 1)
  --report PATH                   Write CSV change report to PATH
  --checkpoint PATH               Checkpoint file for resume support. Defaults to checkpoints/<input-stem>.json
  --no-thinking                   Disable reasoning/thinking mode for supported models
  --no-schema                     Disable structured JSON output (enabled by default)
  --rewrite [MODE]                Use the fiction-editor rewrite prompt instead of the strict grammar-only prompt. Pass 'aggressive' to disable safety filters.
  --translate LANGUAGE            Translate the book into the specified language. Automatically sets --similarity-threshold 0.0 and --max-change-ratio 1.0
  --from N                        Start processing from the Nth HTML document (1-based). Documents before N are skipped.
  --to N                          Stop after the Nth HTML document (1-based, inclusive). Documents after N are skipped.
  --glossary [INPUT_FILE]         Extract a glossary of proper nouns and special terms (standalone — does not correct). Saved to glossaries/<stem>_glossary.json
  --glossary-context-length N     Characters per chunk sent to the LLM during glossary extraction (default: 20000)
  --summarize-glossary PATH       Send a glossary JSON to the LLM to remove meaningless entries. Overwrites the file in-place
  --input-glossary PATH           Glossary JSON to inject into the system prompt so the model preserves those terms exactly
  --debug                         Print raw request/response payloads for every model call
  --verbose                       Enable verbose logging
```

---

## Translation Mode (`--translate`)

Translate the entire EPUB into any language while preserving all meaning, tone, style, names, places, numbers, dates, and formatting.

```bash
epub-corrector input.epub output.epub --translate Spanish
```

When `--translate` is used:
- A literary-translator system prompt is sent to the model
- `--similarity-threshold` is automatically set to `0.0`
- `--max-change-ratio` is automatically set to `1.0`
- The model receives `[TRANSLATE THIS]` markers instead of `[CORRECT THIS]`
- Auto-defaulted output and checkpoint filenames append `_{language}` (spaces become underscores)

> **Tip:** Translation works best with `--max-segments-per-request 1` (the default) so each paragraph is translated individually with full model attention.

**Translation example with auto-named files:**

```bash
epub-corrector "books/My Book.epub" --translate "Spanish"
# Output  → output/My Book_Spanish.epub
# Checkpoint → checkpoints/My Book_Spanish.json
```

## Rewrite Mode (`--rewrite`)

By default the tool uses a **strict grammar-only** prompt: the model may only fix grammar, punctuation, capitalization, and obvious typos. Sentence structure and phrasing must stay as close to the original as possible.

Enable `--rewrite` to switch to a **fiction-editor** prompt aimed at translated literature. The model is explicitly allowed to:

- Restructure awkward or unnatural sentences
- Reorder clauses for better flow
- Replace unnatural phrasing with idiomatic English equivalents

It must still never change names, places, facts, numbers, dates, or meaning.

```bash
epub-corrector input.epub output.epub --rewrite
```

> **Important:** Rewrite mode produces much more aggressive edits than grammar-only mode. The default safety filters (`--similarity-threshold 0.88` and `--max-change-ratio 0.20`) are calibrated for conservative corrections and will auto-reject most valid rewrites. You should loosen them significantly when using `--rewrite`. See [Safety Filters](#safety-filters) below.

### Aggressive rewrite (`--rewrite aggressive`)

Pass `aggressive` to `--rewrite` to automatically disable all safety filters. This sets `--similarity-threshold 0` and `--max-change-ratio 1.0`, so every edit is presented for review instead of being auto-rejected.

```bash
epub-corrector input.epub output.epub --rewrite aggressive
```

## Safety Filters

Two filters auto-reject edits before they ever reach review:

| Filter | Default | What it does |
|---|---|---|
| `--similarity-threshold` | `0.88` | Rejects edits whose SequenceMatcher ratio is below this value. `1.0` = identical. |
| `--max-change-ratio` | `0.20` | Rejects edits whose change ratio (`1.0 - similarity`) exceeds this value. |

### Recommended thresholds for rewrite mode

Because `--rewrite` intentionally restructures sentences, valid edits often score far below the defaults. A good starting point:

```bash
epub-corrector input.epub output.epub --rewrite --similarity-threshold 0.60 --max-change-ratio 0.50
```

If you find too many edits are still being auto-rejected, lower `--similarity-threshold` further (e.g., `0.50`) or raise `--max-change-ratio` (e.g., `0.70`). If you prefer to review every change manually without auto-rejection, set `--similarity-threshold 0.0 --max-change-ratio 1.0`.

## Structured Output (enabled by default, `--no-schema` to disable)

Structured JSON output is **on by default**. The tool forces the model to return a JSON object with a single `corrected_text` field, which it then extracts automatically. This prevents internal reasoning or commentary from leaking into the EPUB output — a common problem with models that think out loud or emit `<think>` blocks.

When structured output is active:
- The API receives `response_format={"type": "json_schema"}` with a strict schema
- The system prompt instructs the model to return only `{"corrected_text": "..."}`
- The tool parses the JSON response and extracts the text, discarding any surrounding commentary

To disable (e.g., for models that don't support structured output):

```bash
epub-corrector input.epub output.epub --no-schema
```

The glossary extraction feature uses its own separate JSON schema with five array fields (`names`, `places`, `organizations`, `terms`, `other`) to guarantee a well-formed response.

## Recommended Settings for Long EPUBs

```bash
epub-corrector \
  --similarity-threshold 0.90 \
  --max-change-ratio 0.15 \
  --checkpoint progress.json
```

---

## Glossary Extraction (`--glossary`)

Build a glossary of important terms from your EPUB before correcting it. The glossary mode scans the entire book and asks the model to identify names, places, organizations, special terms, and any other words that have specific capitalization or spelling that must be preserved — including informal nicknames and epithets like "Alluring Castle" or "the Iron Giant".

```bash
epub-corrector --glossary "books/My Book.epub"
# Output: glossaries/My Book_glossary.json
```

You can also omit the path if you specify the input EPUB as the positional argument:

```bash
epub-corrector "books/My Book.epub" --glossary
```

The output is a JSON file saved to the `glossaries/` folder:

```json
{
  "names":         ["Elara", "Kael"],
  "places":        ["Alluring Castle", "Ashenvale City"],
  "organizations": ["Iron Council"],
  "terms":         ["Aetherbinding"],
  "other":         ["The Reckoning"]
}
```

By default each chunk sent to the model is 20 000 characters. Increase it for faster extraction on long books, decrease it if your model has a small context window:

```bash
epub-corrector --glossary "books/My Book.epub" --glossary-context-length 40000
```

The JSON file can be edited by hand before use — add entries, remove false positives, or fix canonical spellings.

A dedicated JSON schema (`glossary_extraction`) is used for the extraction request, so the model always returns a valid, well-structured response.

## Glossary Summarization (`--summarize-glossary`)

Automated extraction can produce noisy results — website navigation artifacts, raw HTML tags, generic level numbers, real-world brand names, and stat-dump strings. Use `--summarize-glossary` to send an existing glossary to the LLM and ask it to remove the noise, keeping only entries that are genuinely meaningful for consistency.

```bash
epub-corrector --summarize-glossary "glossaries/My Book_glossary.json"
```

The file is overwritten in-place. The command prints a before/after term count so you can see what was removed:

```
Summarizing glossary: glossaries/My Book_glossary.json (142 terms)
Done. 142 → 98 terms (44 removed). Saved to glossaries/My Book_glossary.json
```

Typical entries removed:
- Website artifacts: `< report chapter >`
- HTML/format strings: `html`
- Generic sequential labels: `Level 0`, `Level 1`, …
- Out-of-place real-world products: `iPhone 12`
- Stat-dump strings: `Strength 5, Agility 3, Endurance 4`
- Cross-category duplicates (kept only in the most specific category)

The recommended workflow is:

```bash
# Step 1: extract
epub-corrector --glossary "books/My Book.epub"

# Step 2: summarize (remove noise)
epub-corrector --summarize-glossary "glossaries/My Book_glossary.json"

# Step 3: review / edit the cleaned glossary by hand if needed

# Step 4: correct with the glossary active
epub-corrector "books/My Book.epub" output/corrected.epub \
  --input-glossary "glossaries/My Book_glossary.json"
```

## Glossary-Guided Correction (`--input-glossary`)

Pass a glossary file to any correction run to tell the model exactly which terms must be preserved and how they should be capitalized:

```bash
# Step 1: extract
epub-corrector --glossary "books/My Book.epub"

# Step 2: review / edit glossaries/My Book_glossary.json as needed

# Step 3: correct with the glossary active
epub-corrector "books/My Book.epub" output/corrected.epub \
  --input-glossary "glossaries/My Book_glossary.json"
```

Works with all modes — grammar correction, rewrite, and translation:

```bash
epub-corrector "books/My Book.epub" output/spanish.epub \
  --translate Spanish \
  --input-glossary "glossaries/My Book_glossary.json"
```

The glossary terms are injected into the system prompt as:

```
The following terms appear in this text and must be preserved exactly as written
(including capitalization and spelling):
Names: Elara, Kael
Places: Alluring Castle, Ashenvale City
...
```

---

## Building a Standalone Executable

A PyInstaller build script is included for creating a single-file Windows executable of the GUI:

```bash
uv sync --extra build
python build_exe.py
```

The resulting executable will be at `dist/epub-corrector-gui.exe`.

---

## Models Tested

| Model | Structured Output | Thinking | Notes |
|---|---|---|---|
| `mistralai/ministral-3-3b` | No | No | Works very well, useful for PCs with not very powerful GPUs |
| `google/gemma-4-e4b` | Yes | No | Works extremely well and fast, requires more VRAM. Spanish was not so good, made some errors |
| `ministral-3-14b-instruct-2512` | Yes | No | Works extremely well, better than gemma 4. Tried Spanish and English, both had amazing results |
