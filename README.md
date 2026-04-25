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
  --similarity-threshold FLOAT    Auto-reject edits below this similarity (default: 0.88)
  --max-change-ratio FLOAT        Auto-reject edits above this change ratio (default: 0.20)
  --report PATH                   Write CSV change report to PATH
  --checkpoint PATH               Checkpoint file for resume support
  --no-thinking                   Disable reasoning/thinking mode for supported models
  --debug                         Print raw request/response payloads for every model call
  --verbose                       Enable verbose logging
```

---

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
