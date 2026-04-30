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


def test_build_messages_glossary_injection():
    cfg = CorrectionConfig(glossary_injection="\n\nTerms: foo, bar")
    msgs = build_messages("hello", config=cfg)
    assert "Terms: foo, bar" in msgs[0]["content"]


def test_build_messages_translate_with_context():
    cfg = CorrectionConfig(translate=True, target_language="French")
    msgs = build_messages("hello", context_texts=["ctx1"], config=cfg)
    assert "[TRANSLATE THIS]" in msgs[1]["content"]
    assert "[CONTEXT]" in msgs[1]["content"]
    assert "Translate ONLY" in msgs[0]["content"]


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


def test_llm_client_build_api_kwargs_no_thinking():
    mock_openai = MagicMock()
    cfg = CorrectionConfig(no_thinking=True, use_schema=False)
    client = LLMClient(mock_openai, "test-model", cfg)
    kwargs = client._build_api_kwargs()
    assert kwargs == {"extra_body": {"thinking": {"type": "disabled"}}}


def test_llm_client_do_single_debug(capsys):
    mock_openai = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "ok"
    mock_openai.chat.completions.create.return_value = mock_response

    cfg = CorrectionConfig(debug=True, max_retries=1, use_schema=False)
    client = LLMClient(mock_openai, "test-model", cfg)
    result = client._do_single("hello", {"messages": []})
    assert result == "ok"
    captured = capsys.readouterr()
    assert "REQUEST PAYLOAD" in captured.out
    assert "MODEL RESPONSE" in captured.out


def test_llm_client_do_single_retry_then_success():
    mock_openai = MagicMock()
    fail_response = MagicMock()
    fail_response.choices = [MagicMock()]
    fail_response.choices[0].message.content = ""
    success_response = MagicMock()
    success_response.choices = [MagicMock()]
    success_response.choices[0].message.content = "ok"
    mock_openai.chat.completions.create.side_effect = [fail_response, success_response]

    cfg = CorrectionConfig(max_retries=2, use_schema=False)
    client = LLMClient(mock_openai, "test-model", cfg)
    result = client._do_single("hello", {"messages": []})
    assert result == "ok"
    assert mock_openai.chat.completions.create.call_count == 2


def test_llm_client_do_single_schema_validation_failure():
    mock_openai = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "not json"
    mock_openai.chat.completions.create.return_value = mock_response

    cfg = CorrectionConfig(use_schema=True, max_retries=1)
    client = LLMClient(mock_openai, "test-model", cfg)
    with pytest.raises(RuntimeError) as exc_info:
        client._do_single("hello", {"messages": []})
    assert "non-JSON output" in str(exc_info.value)


def test_llm_client_request_corrections_parallel():
    mock_openai = MagicMock()
    responses = []
    for i in range(3):
        r = MagicMock()
        r.choices = [MagicMock()]
        r.choices[0].message.content = f'{{"corrected_text": "result{i}"}}'
        responses.append(r)
    mock_openai.chat.completions.create.side_effect = responses

    cfg = CorrectionConfig(max_workers=3, max_retries=1)
    client = LLMClient(mock_openai, "test-model", cfg)
    result = client.request_corrections(["a", "b", "c"])
    assert result == ["result0", "result1", "result2"]


def test_llm_client_request_corrections_with_context():
    mock_openai = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"corrected_text": "fixed"}'
    mock_openai.chat.completions.create.return_value = mock_response

    cfg = CorrectionConfig(max_context=2, max_context_chars=1000, max_retries=1)
    client = LLMClient(mock_openai, "test-model", cfg)
    result = client.request_corrections(["hello"], previous_context=["ctx1", "ctx2", "ctx3"])
    assert result == ["fixed"]
    call_kwargs = mock_openai.chat.completions.create.call_args.kwargs
    messages = call_kwargs["messages"]
    assert "[CONTEXT]" in messages[1]["content"]
