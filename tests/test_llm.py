from unittest.mock import MagicMock

import pytest

from epub_corrector.config import CorrectionConfig
from epub_corrector.llm import LLMClient, build_messages, extract_correction


def test_build_messages_default():
    cfg = CorrectionConfig()
    msgs = build_messages("hello", config=cfg)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "CORRECT THIS" in msgs[1]["content"]
    assert "hello" in msgs[1]["content"]


def test_build_messages_with_context():
    cfg = CorrectionConfig()
    msgs = build_messages("hello", context_texts=["ctx1", "ctx2"], config=cfg)
    assert "[CONTEXT]" in msgs[1]["content"]
    assert "ctx1" in msgs[1]["content"]
    assert "ctx2" in msgs[1]["content"]


def test_build_messages_translate():
    cfg = CorrectionConfig(translate=True, target_language="French")
    msgs = build_messages("hello", config=cfg)
    assert "TRANSLATE THIS" in msgs[1]["content"]
    assert "French" in msgs[0]["content"]


def test_build_messages_no_thinking():
    cfg = CorrectionConfig(no_thinking=True)
    msgs = build_messages("hello", config=cfg)
    assert msgs[0]["content"].startswith("/no_think")


def test_extract_correction_plain():
    assert extract_correction("hello world") == "hello world"


def test_extract_correction_fenced():
    assert extract_correction("```\nhello\n```") == "hello"
    assert extract_correction("```json\nhello\n```") == "hello"


def test_extract_correction_json_schema():
    assert extract_correction('{"corrected_text": "hello"}', use_schema=True) == "hello"


def test_extract_correction_json_schema_missing_key():
    assert extract_correction('{"other": "hello"}', use_schema=True) == '{"other": "hello"}'


def test_llm_client_request_corrections_single():
    mock_openai = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"corrected_text": "fixed text"}'
    mock_openai.chat.completions.create.return_value = mock_response

    cfg = CorrectionConfig(max_retries=1)
    client = LLMClient(mock_openai, "test-model", cfg)
    result = client.request_corrections(["hello"])
    assert result == ["fixed text"]
    mock_openai.chat.completions.create.assert_called_once()


def test_llm_client_request_corrections_empty_content():
    mock_openai = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "   "
    mock_openai.chat.completions.create.return_value = mock_response

    cfg = CorrectionConfig(max_retries=1)
    client = LLMClient(mock_openai, "test-model", cfg)
    with pytest.raises(RuntimeError):
        client.request_corrections(["hello"])


def test_llm_client_uses_schema():
    mock_openai = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"corrected_text": "ok"}'
    mock_openai.chat.completions.create.return_value = mock_response

    cfg = CorrectionConfig(use_schema=True, max_retries=1)
    client = LLMClient(mock_openai, "test-model", cfg)
    result = client.request_corrections(["hello"])
    assert result == ["ok"]
    call_kwargs = mock_openai.chat.completions.create.call_args.kwargs
    assert "response_format" in call_kwargs
