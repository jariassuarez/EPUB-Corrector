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

If `output` is omitted, it defaults to `<input-stem>_corrected.epub`.

---

## Server Setup (LM Studio, etc.)

1. Open LM Studio (or your preferred local LLM server).
2. Start the local server.
3. Load your model.
4. The OpenAI-compatible endpoint is available at `http://127.0.0.1:1234/v1` by default.

> The GUI can automatically fetch available models from the `/models` endpoint.

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

For very large EPUBs, save progress after each document so an interrupted run can continue where it left off:

```bash
epub-corrector --checkpoint mybook.ckpt.json
```

Re-run the exact same command after an interruption — already-processed documents are restored from the checkpoint and skipped. Delete the checkpoint file once the corrected EPUB is written successfully.

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
  output                          Output EPUB. Omit to auto-name as <input>_corrected.epub

options:
  --model MODEL                   Model name loaded in LM Studio (default: local-model)
  --base-url URL                  LM Studio base URL (default: http://127.0.0.1:1234/v1)
  --api-key KEY                   API key — LM Studio accepts any value (default: lm-studio)
  --temperature FLOAT             Generation temperature (default: 0.0)
  --max-segments-per-request N    Max text segments per model call (default: 60)
  --max-chars-per-request N       Max characters per model call (default: 6000)
  --max-context N                 Number of previous segments to include as context (default: 0)
  --max-context-chars N           Maximum total characters of context per request (default: 3000)
  --conserve-context              Preserve context across documents/chapters instead of resetting per file
  --similarity-threshold FLOAT    Auto-reject edits below this similarity (default: 0.88)
  --max-change-ratio FLOAT        Auto-reject edits above this change ratio (default: 0.20)
  --report PATH                   Write CSV change report to PATH
  --checkpoint PATH               Checkpoint file for resume support
  --no-thinking                   Disable reasoning/thinking mode for supported models
  --schema                        Use structured JSON output to isolate corrected text from model commentary/reasoning
  --debug                         Print raw request/response payloads for every model call
  --verbose                       Enable verbose logging
```

---

## Structured Output (`--schema`)

Some models (e.g., Gemma 4, DeepSeek-R1, or any model configured with reasoning/thinking) emit internal reasoning or commentary before the actual corrected text. This can leak into the EPUB and corrupt the output.

Enable `--schema` to force the model to return a JSON object with a single `corrected_text` field, which the tool then extracts automatically:

```bash
epub-corrector input.epub output.epub --schema
```

When `--schema` is enabled:
- The API receives `response_format={"type": "json_object"}`
- The system prompt is extended to instruct the model to return only `{"corrected_text": "..."}`
- The tool parses the JSON response and extracts the text, discarding any surrounding commentary

Recommended for models that yap, think out loud, or include `<think>` blocks in their output.

## Recommended Settings for Long EPUBs

```bash
epub-corrector \
  --similarity-threshold 0.90 \
  --max-change-ratio 0.15 \
  --checkpoint progress.json
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
| `google/gemma-4-e4b` | Yes | No | Works extremely well and fast, requires more VRAM |
