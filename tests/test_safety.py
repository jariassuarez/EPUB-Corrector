from epub_corrector.safety import change_is_safe, restore_boundary_punctuation


def test_identical_text_is_safe():
    assert change_is_safe("hello", "hello", 0.5, 0.5) is True


def test_empty_proposed_is_unsafe():
    assert change_is_safe("hello", "", 0.5, 0.5) is False


def test_similarity_below_threshold():
    assert change_is_safe("hello world", "completely different text", 0.99, 1.0) is False


def test_change_ratio_above_max():
    assert change_is_safe("hello", "goodbye", 0.0, 0.1) is False


def test_safe_change_within_bounds():
    assert change_is_safe("hello world", "hello worlds", 0.5, 0.5) is True


def test_restore_leading_quote():
    assert restore_boundary_punctuation('"hello', "hello") == '"hello'


def test_restore_trailing_quote():
    assert restore_boundary_punctuation('hello"', "hello") == 'hello"'


def test_restore_both_quotes():
    assert restore_boundary_punctuation('"hello"', "hello") == '"hello"'


def test_restore_dash():
    assert restore_boundary_punctuation("-hello", "hello") == "-hello"


def test_no_change_when_punctuation_preserved():
    assert restore_boundary_punctuation('"hello"', '"hello"') == '"hello"'
