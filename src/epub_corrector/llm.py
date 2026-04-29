from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from openai import OpenAIError

from .safety import restore_boundary_punctuation

if TYPE_CHECKING:
    from openai import OpenAI

from .config import CorrectionConfig

REWRITE_SYSTEM_PROMPT = (
    "You are a professional fiction editor specializing in translated literature.\n\n"
    "Your goal is to produce clean, natural, fluent English prose that reads as if it were written by a native English author — not translated.\n\n"
    "You MAY:\n"
    "- Restructure awkward or unnatural sentences\n"
    "- Reorder clauses for better flow\n"
    "- Replace unnatural phrasing with idiomatic English equivalents\n"
    "- Correct grammar, punctuation, capitalization, and obvious typos\n\n"
    "You must NEVER:\n"
    "- Change names, places, or any factual information\n"
    "- Add or remove facts, events, or details\n"
    "- Change the meaning, tone, or intent of any sentence or dialogue\n"
    "- Alter numbers, dates, or chronology\n"
    "- Summarize or condense content\n"
    "- Add commentary, notes, or explanations\n\n"
    "Formatting rules:\n"
    "- Preserve all quotation marks, apostrophes, dashes, and ellipsis characters exactly where they appear\n"
    "- Do not alter markup, formatting structure, or whitespace\n"
    "- Output clean prose only — no bolding, no annotations"
    "- Do not remove quotation marks from short standalone lines — these may represent internal thought or narration conventions from the source text."
)

DEFAULT_SYSTEM_PROMPT = (
    "You are a strict grammar and spelling corrector. "
    "Do not remove quotation marks from short standalone lines — these may represent internal thought or narration conventions from the source text."
    "Correct only grammar, punctuation, capitalization, and obvious typos. "
    "Do NOT change numbers, dates, entities, or formatting. "
    "Do NOT under any circumstances change names, places, or any other factual information. "
    "Do not change formatting, markup, or whitespace. "
    "Adopt the tone of a professional fiction editor. Provide clean, flowing prose without bolding, and ensure the grammar follows standard novel-writing conventions. "
    "Do not add or remove facts, style, tone, meaning, entities, numbers, chronology, or dialogue intent. "
    "Do not summarize. Do not paraphrase unless required for grammar. "
    "Preserve ALL quotation marks, apostrophes, dashes, and ellipsis characters exactly where they appear, including at the very start and very end of the text. "
)

TRANSLATE_SYSTEM_PROMPT_TEMPLATE = (
    "You are a professional literary translator.\n\n"
    "Your task is to translate the provided text into {language}. "
    "Preserve all meaning, tone, style, intent, and factual content exactly. "
    "Do NOT change names, places, numbers, dates, or chronology. "
    "Do NOT add or remove facts, events, or details. "
    "Do NOT summarize or condense content. "
    "Do NOT add commentary, notes, or explanations.\n\n"
    "Formatting rules:\n"
    "- Preserve all quotation marks, apostrophes, dashes, and ellipsis characters exactly where they appear\n"
    "- Do not alter markup, formatting structure, or whitespace\n"
    "- Output clean prose only — no bolding, no annotations"
)


def build_messages(
    text: str,
    context_texts: list[str] | None = None,
    config: CorrectionConfig | None = None,
) -> list[dict[str, str]]:
    cfg = config or CorrectionConfig()
    if cfg.translate and cfg.target_language:
        system = TRANSLATE_SYSTEM_PROMPT_TEMPLATE.format(language=cfg.target_language)
    else:
        system = REWRITE_SYSTEM_PROMPT if cfg.rewrite else DEFAULT_SYSTEM_PROMPT

    if cfg.glossary_injection:
        system += cfg.glossary_injection

    marker = "TRANSLATE THIS" if (cfg.translate and cfg.target_language) else "CORRECT THIS"
    if context_texts:
        if cfg.translate and cfg.target_language:
            system += (
                " The user will provide context paragraphs marked [CONTEXT] followed by a target paragraph marked [TRANSLATE THIS]. "
                "Use the context only for reference (names, pronouns, terminology, style). "
                "Translate ONLY the [TRANSLATE THIS] paragraph. "
                "Return ONLY the translated version of the [TRANSLATE THIS] paragraph, with no extra commentary, explanation, or formatting."
            )
        else:
            system += (
                " The user will provide context paragraphs marked [CONTEXT] followed by a target paragraph marked [CORRECT THIS]. "
                "Use the context only for reference (names, pronouns, terminology, style). "
                "Correct ONLY the [CORRECT THIS] paragraph. "
                "Return ONLY the corrected version of the [CORRECT THIS] paragraph, with no extra commentary, explanation, or formatting."
            )
    else:
        system += f" Return ONLY the {marker.lower().replace('_', ' ')} version of the text you are sent, with no extra commentary, explanation, or formatting."

    if cfg.use_schema:
        description = (
            "The translated text with the language changed while preserving all meaning, tone, and style."
            if cfg.translate and cfg.target_language
            else "The corrected text with only grammar, punctuation, capitalization, and typo fixes applied."
        )
        system += (
            " Respond with a JSON object containing a single key 'corrected_text' "
            f"whose value is the {description} string. Do not include any other keys or commentary."
        )
    if cfg.no_thinking:
        system = "/no_think\n\n" + system

    user_parts: list[str] = []
    for ctx in context_texts or []:
        user_parts.append(f"[CONTEXT]\n{ctx}")
    user_parts.append(f"[{marker}]\n{text}")

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def extract_correction(raw: str, use_schema: bool = False) -> str:
    """Strip optional markdown fences, parse JSON schema output when enabled, and return clean corrected text."""
    text = raw.strip()
    fenced = re.search(r"^```(?:\w+)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    if use_schema:
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "corrected_text" in data:
                return str(data["corrected_text"])
        except json.JSONDecodeError:
            pass
    return text


class LLMClient:
    """Wraps the raw OpenAI client with correction-specific request building and retry logic."""

    def __init__(self, client: OpenAI, model: str, config: CorrectionConfig) -> None:
        self.client = client
        self.model = model
        self.config = config

    def _build_api_kwargs(self) -> dict:
        kwargs: dict = {}
        if self.config.no_thinking:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        if self.config.use_schema:
            description = (
                "The translated text with the language changed while preserving all meaning, tone, and style."
                if self.config.translate and self.config.target_language
                else "The corrected text with only grammar, punctuation, capitalization, and typo fixes applied."
            )
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "text_correction",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "corrected_text": {
                                "type": "string",
                                "description": description,
                            }
                        },
                        "required": ["corrected_text"],
                        "additionalProperties": False,
                    },
                },
            }
        return kwargs

    def _do_single(self, text: str, payload: dict) -> str:
        cfg = self.config
        if cfg.debug:
            print("\n--- REQUEST PAYLOAD ---")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            print("--- END REQUEST ---\n")

        last_exc: Exception | None = None
        last_response_content: str | None = None
        for attempt in range(1, cfg.max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=cfg.temperature,
                    messages=payload["messages"],
                    **self._build_api_kwargs(),
                )
                content = response.choices[0].message.content or ""
                last_response_content = content
                if cfg.debug:
                    print(f"\n--- MODEL RESPONSE (attempt {attempt}) ---")
                    print(content)
                    print("--- END RESPONSE ---\n")

                if not content.strip():
                    raise ValueError("Model returned empty output")

                if cfg.use_schema:
                    cleaned = content.strip()
                    fenced = re.search(r"^```(?:\w+)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
                    if fenced:
                        cleaned = fenced.group(1).strip()
                    try:
                        data = json.loads(cleaned)
                        if not isinstance(data, dict) or "corrected_text" not in data:
                            raise ValueError("Model returned JSON without 'corrected_text' key")
                    except json.JSONDecodeError:
                        raise ValueError("Model returned non-JSON output when schema was required") from None

                corrected = extract_correction(content, use_schema=cfg.use_schema)
                corrected = restore_boundary_punctuation(text, corrected)
                return corrected
            except (OSError, OpenAIError, json.JSONDecodeError, ValueError) as exc:
                logging.warning("Model request failed (attempt %d/%d): %s", attempt, cfg.max_retries, exc)
                last_exc = exc
                if attempt < cfg.max_retries:
                    print(f"    Request failed, retrying... ({attempt}/{cfg.max_retries})")
                    continue

                err_parts = [
                    f"Model request failed after {cfg.max_retries} attempts.",
                    f"Last error: {last_exc}",
                ]
                err_parts.append("\n--- REQUEST PAYLOAD ---")
                err_parts.append(json.dumps(payload, ensure_ascii=False, indent=2))
                if last_response_content is not None:
                    err_parts.append("\n--- LAST RESPONSE RECEIVED ---")
                    err_parts.append(last_response_content)
                else:
                    err_parts.append("\n--- NO RESPONSE RECEIVED ---")
                err_parts.append("\n--- END ---")
                raise RuntimeError("\n".join(err_parts)) from last_exc
        raise RuntimeError("Model request failed unexpectedly after retries.")

    def request_corrections(
        self,
        texts: list[str],
        previous_context: list[str] | None = None,
    ) -> list[str]:
        cfg = self.config
        all_prior = list(previous_context or [])
        payloads: list[tuple[str, dict]] = []

        for text in texts:
            context: list[str] = []
            if cfg.max_context > 0 and all_prior:
                candidates = all_prior[-cfg.max_context :] if len(all_prior) > cfg.max_context else list(all_prior)
                total_chars = 0
                for ctx in reversed(candidates):
                    if cfg.max_context_chars > 0 and total_chars + len(ctx) > cfg.max_context_chars:
                        break
                    context.insert(0, ctx)
                    total_chars += len(ctx)

            messages = build_messages(text, context_texts=context or None, config=cfg)
            payload = {
                "model": self.model,
                "temperature": cfg.temperature,
                "messages": messages,
                **self._build_api_kwargs(),
            }
            payloads.append((text, payload))
            all_prior.append(text)

        if cfg.max_workers <= 1:
            return [self._do_single(text, payload) for text, payload in payloads]

        with ThreadPoolExecutor(max_workers=cfg.max_workers) as executor:
            return list(executor.map(lambda tp: self._do_single(tp[0], tp[1]), payloads))
