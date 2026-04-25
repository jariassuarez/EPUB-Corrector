# EPUB Corrector (LM Studio)

This tool reads an EPUB, sends text chunks to your local LM Studio model, and writes a corrected EPUB. Every proposed change is shown in the terminal as a side-by-side diff, and you accept or skip each one interactively.

Goal:
- Correct grammar, punctuation, capitalization, and typos.
- Preserve meaning and context.

Important:
- No LLM workflow can guarantee 100% zero-context drift.
- This tool uses conservative safety filters (`--similarity-threshold` and `--max-change-ratio`) to auto-reject edits that look too aggressive.

---

## Install

```bash
uv sync          # or: pip install -e .
```

## LM Studio setup

1. Open LM Studio and start the local server.
2. Load your model.
3. The OpenAI-compatible endpoint is available at `http://127.0.0.1:1234/v1` by default.

---

## Basic usage

**Interactive — pick a book from the `books/` folder:**

```bash
epub-corrector
```

You will see a numbered list of `.epub` files in `./books/` and be prompted to choose one.
The corrected file is saved as `<original-name>_corrected.epub` in the current directory.

**Explicit paths:**

```bash
epub-corrector input.epub output.epub
```

If `output` is omitted, it defaults to `<input-stem>_corrected.epub`.

---

## Interactive review

For every proposed change that passes the safety filters, a side-by-side diff is shown:

```
────────────────────────────────────────────────────────────────────────────────
ORIGINAL                                 │ PROPOSED
────────────────────────────────────────────────────────────────────────────────
He don't know what he was doing here.   │ He didn't know what he was doing here.
────────────────────────────────────────────────────────────────────────────────
chapter01.xhtml
[Enter] Accept  [n] Skip  [Shift+Enter or a] Accept all
```

| Key | Action |
|---|---|
| `Enter` | Accept this change |
| `n` | Skip this change |
| `a` or `Shift+Enter` | Accept this and all remaining changes automatically |
| `Ctrl+C` | Abort |

Deleted text is shown with a red background; inserted text with green.

---

## Change report (`--report`)

After processing, write a CSV record of every accepted and rejected edit:

```bash
epub-corrector --report changes.csv
```

---

## Resume / checkpoint (`--checkpoint`)

For very large EPUBs, save progress after each document so an interrupted run can continue where it left off:

```bash
epub-corrector --checkpoint mybook.ckpt.json
```

Re-run the exact same command after an interruption — already-processed documents are restored from the checkpoint and skipped. Delete the checkpoint file once the corrected EPUB is written successfully.

---

## All options

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

## Recommended settings for long EPUBs

```bash
epub-corrector \
  --similarity-threshold 0.90 \
  --max-change-ratio 0.15 \
  --checkpoint progress.json
```
