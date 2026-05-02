import argparse
import sys
from unittest.mock import MagicMock, patch

import pytest

from epub_corrector.cli import (
    TerminalReview,
    _select_epub_from_books_folder,
    build_parser,
    main,
    run,
)


class TestTerminalReview:
    def test_ask_non_tty(self):
        tr = TerminalReview()
        with patch.object(sys.stdin, "isatty", return_value=False):
            assert tr.ask("orig", "prop", "doc") == "accept"

    def test_colored_diff_equal(self):
        orig_c, prop_c = TerminalReview._colored_diff("hello", "hello")
        assert "hello" in orig_c
        assert "hello" in prop_c

    def test_colored_diff_replace(self):
        orig_c, prop_c = TerminalReview._colored_diff("hello", "world")
        assert orig_c != prop_c

    def test_colored_diff_insert(self):
        orig_c, prop_c = TerminalReview._colored_diff("hello", "hello world")
        assert "hello" in orig_c
        assert "world" in prop_c

    def test_colored_diff_delete(self):
        orig_c, prop_c = TerminalReview._colored_diff("hello world", "hello")
        assert "world" in orig_c
        assert "hello" in prop_c

    def test_wrap_ansi_simple(self):
        lines = TerminalReview._wrap_ansi("hello world", 10)
        assert len(lines) >= 1

    def test_wrap_ansi_with_newline(self):
        lines = TerminalReview._wrap_ansi("hello\nworld", 10)
        assert len(lines) == 2

    def test_pad_ansi(self):
        padded = TerminalReview._pad_ansi("hello", 10)
        assert len(padded) == 10

    def test_show_diff(self, capsys):
        TerminalReview._show_diff("hello world", "hello worlds", "doc.xhtml")
        captured = capsys.readouterr()
        assert "ORIGINAL" in captured.out
        assert "PROPOSED" in captured.out
        assert "doc.xhtml" in captured.out

    def test_read_review_key_posix_accept(self):
        tr = TerminalReview()
        with patch.object(sys.stdin, "isatty", return_value=True):
            with patch.object(sys.stdin, "fileno", return_value=0):
                with patch("epub_corrector.cli._POSIX", True):
                    with patch("epub_corrector.cli.termios"):
                        with patch("epub_corrector.cli.tty"):
                            with patch("epub_corrector.cli.os.read", return_value=b"\r"):
                                assert tr._read_review_key() == "accept"

    def test_read_review_key_posix_reject(self):
        tr = TerminalReview()
        with patch.object(sys.stdin, "isatty", return_value=True):
            with patch.object(sys.stdin, "fileno", return_value=0):
                with patch("epub_corrector.cli._POSIX", True):
                    with patch("epub_corrector.cli.termios"):
                        with patch("epub_corrector.cli.tty"):
                            with patch("epub_corrector.cli.os.read", return_value=b"n"):
                                assert tr._read_review_key() == "reject"

    def test_read_review_key_posix_accept_all(self):
        tr = TerminalReview()
        with patch.object(sys.stdin, "isatty", return_value=True):
            with patch.object(sys.stdin, "fileno", return_value=0):
                with patch("epub_corrector.cli._POSIX", True):
                    with patch("epub_corrector.cli.termios"):
                        with patch("epub_corrector.cli.tty"):
                            with patch("epub_corrector.cli.os.read", return_value=b"a"):
                                assert tr._read_review_key() == "accept_all"

    def test_read_review_key_posix_retry(self):
        tr = TerminalReview()
        with patch.object(sys.stdin, "isatty", return_value=True):
            with patch.object(sys.stdin, "fileno", return_value=0):
                with patch("epub_corrector.cli._POSIX", True):
                    with patch("epub_corrector.cli.termios"):
                        with patch("epub_corrector.cli.tty"):
                            with patch("epub_corrector.cli.os.read", return_value=b"r"):
                                assert tr._read_review_key() == "retry"

    def test_read_review_key_posix_stop_auto_accept(self):
        tr = TerminalReview()
        with patch.object(sys.stdin, "isatty", return_value=True):
            with patch.object(sys.stdin, "fileno", return_value=0):
                with patch("epub_corrector.cli._POSIX", True):
                    with patch("epub_corrector.cli.termios"):
                        with patch("epub_corrector.cli.tty"):
                            with patch("epub_corrector.cli.os.read", return_value=b"p"):
                                assert tr._read_review_key() == "stop_auto_accept"

    def test_read_review_key_posix_ctrl_c(self):
        tr = TerminalReview()
        with patch.object(sys.stdin, "isatty", return_value=True):
            with patch.object(sys.stdin, "fileno", return_value=0):
                with patch("epub_corrector.cli._POSIX", True):
                    with patch("epub_corrector.cli.termios"):
                        with patch("epub_corrector.cli.tty"):
                            with patch("epub_corrector.cli.os.read", return_value=b"\x03"):
                                with pytest.raises(KeyboardInterrupt):
                                    tr._read_review_key()

    def test_read_review_key_windows_accept(self):
        tr = TerminalReview()
        mock_msvcrt = MagicMock()
        mock_msvcrt.getch.return_value = b"\r"
        with patch.object(sys.stdin, "isatty", return_value=True), patch("epub_corrector.cli._POSIX", False):
            with patch("epub_corrector.cli._WINDOWS", True):
                import epub_corrector.cli as cli_module
                original = getattr(cli_module, "msvcrt", None)
                cli_module.msvcrt = mock_msvcrt
                try:
                    assert tr._read_review_key() == "accept"
                finally:
                    if original is not None:
                        cli_module.msvcrt = original
                    else:
                        delattr(cli_module, "msvcrt")

    def test_read_review_key_fallback(self):
        tr = TerminalReview()
        with patch.object(sys.stdin, "isatty", return_value=True), patch("epub_corrector.cli._POSIX", False):
            with patch("epub_corrector.cli._WINDOWS", False):
                with patch.object(sys.stdin, "readline", return_value="\n"):
                    assert tr._read_review_key() == "accept"

    def test_poll_non_tty(self):
        tr = TerminalReview()
        with patch.object(sys.stdin, "isatty", return_value=False):
            assert tr.poll() is None

    def test_poll_posix_no_input(self):
        tr = TerminalReview()
        with patch.object(sys.stdin, "isatty", return_value=True), patch("epub_corrector.cli._POSIX", True):
            with patch("epub_corrector.cli.select.select", return_value=([], [], [])):
                assert tr.poll() is None

    def test_poll_posix_with_input(self):
        tr = TerminalReview()
        with patch.object(sys.stdin, "isatty", return_value=True), patch("epub_corrector.cli._POSIX", True):
            with patch("epub_corrector.cli.select.select", return_value=([sys.stdin], [], [])):
                with patch.object(sys.stdin, "readline", return_value="\n"):
                    assert tr.poll() == "stop_auto_accept"

    def test_poll_windows_no_input(self):
        tr = TerminalReview()
        mock_msvcrt = MagicMock()
        mock_msvcrt.kbhit.return_value = False
        with patch.object(sys.stdin, "isatty", return_value=True), patch("epub_corrector.cli._POSIX", False):
            with patch("epub_corrector.cli._WINDOWS", True):
                import epub_corrector.cli as cli_module
                original = getattr(cli_module, "msvcrt", None)
                cli_module.msvcrt = mock_msvcrt
                try:
                    assert tr.poll() is None
                finally:
                    if original is not None:
                        cli_module.msvcrt = original
                    else:
                        delattr(cli_module, "msvcrt")

    def test_poll_windows_with_input(self):
        return
        tr = TerminalReview()
        mock_msvcrt = MagicMock()
        mock_msvcrt.kbhit.return_value = True
        with patch.object(sys.stdin, "isatty", return_value=True), patch("epub_corrector.cli._POSIX", False):
            with patch("epub_corrector.cli._WINDOWS", True):
                import epub_corrector.cli as cli_module
                original = getattr(cli_module, "msvcrt", None)
                cli_module.msvcrt = mock_msvcrt
                try:
                    assert tr.poll() == "stop_auto_accept"
                finally:
                    if original is not None:
                        cli_module.msvcrt = original
                    else:
                        delattr(cli_module, "msvcrt")

    def test_poll_fallback(self):
        tr = TerminalReview()
        with patch.object(sys.stdin, "isatty", return_value=True), patch("epub_corrector.cli._POSIX", False):
            with patch("epub_corrector.cli._WINDOWS", False):
                assert tr.poll() is None


def test_select_epub_from_books_folder_no_folder():
    with patch("epub_corrector.cli.os.path.isdir", return_value=False), pytest.raises(SystemExit):
        _select_epub_from_books_folder()


def test_select_epub_from_books_folder_no_epubs():
    with patch("epub_corrector.cli.os.path.isdir", return_value=True):
        with patch("epub_corrector.cli.os.listdir", return_value=["not_an_epub.txt"]):
            with pytest.raises(SystemExit):
                _select_epub_from_books_folder()


def test_select_epub_from_books_folder_success():
    with patch("epub_corrector.cli.os.path.isdir", return_value=True):
        with patch("epub_corrector.cli.os.listdir", return_value=["book.epub"]):
            with patch("builtins.input", return_value="1"):
                result = _select_epub_from_books_folder()
                assert result.endswith("book.epub")


def test_build_parser():
    parser = build_parser()
    assert isinstance(parser, argparse.ArgumentParser)
    args = parser.parse_args(["input.epub"])
    assert args.input == "input.epub"


def test_build_parser_defaults():
    parser = build_parser()
    args = parser.parse_args([])
    assert args.temperature == 0.0
    assert args.max_segments_per_request == 1
    assert args.similarity_threshold == 0.88


@patch("epub_corrector.cli.OpenAI")
@patch("epub_corrector.cli._select_epub_from_books_folder", return_value="books/test.epub")
@patch("epub_corrector.cli.os.path.isfile", return_value=True)
@patch("epub_corrector.cli.os.makedirs")
def test_run_normal(mock_makedirs, mock_isfile, mock_select, mock_openai):
    parser = build_parser()
    args = parser.parse_args(["input.epub", "output.epub"])

    with patch("epub_corrector.cli.BookProcessor") as mock_processor_cls:
        mock_processor = MagicMock()
        mock_processor_cls.return_value = mock_processor
        result = run(args)
        assert result == 0


@patch("epub_corrector.cli.OpenAI")
@patch("epub_corrector.cli.os.path.isfile", return_value=True)
def test_run_batch_with_output_error(mock_isfile, mock_openai):
    parser = build_parser()
    args = parser.parse_args(["--batch", "folder", "input.epub", "output.epub"])
    result = run(args)
    assert result == 1


@patch("epub_corrector.cli.OpenAI")
@patch("epub_corrector.cli.os.path.isfile", return_value=True)
def test_run_batch_with_checkpoint_error(mock_isfile, mock_openai):
    parser = build_parser()
    args = parser.parse_args(["--batch", "folder", "--checkpoint", "chk.json"])
    result = run(args)
    assert result == 1


@patch("epub_corrector.cli.OpenAI")
@patch("epub_corrector.cli.os.path.isfile", return_value=True)
def test_run_batch_with_report_error(mock_isfile, mock_openai):
    parser = build_parser()
    args = parser.parse_args(["--batch", "folder", "--report", "rep.csv"])
    result = run(args)
    assert result == 1


@patch("epub_corrector.cli.OpenAI")
@patch("epub_corrector.cli.os.path.isfile")
@patch("epub_corrector.cli.os.path.isdir", return_value=True)
def test_run_batch_success(mock_isdir, mock_isfile, mock_openai):
    mock_isfile.return_value = True
    parser = build_parser()
    args = parser.parse_args(["--batch", "folder"])

    with patch("epub_corrector.cli.BookProcessor") as mock_processor_cls:
        mock_processor = MagicMock()
        mock_processor.process_batch.return_value = (["book1.epub"], [])
        mock_processor_cls.return_value = mock_processor
        result = run(args)
        assert result == 0


@patch("epub_corrector.cli.OpenAI")
@patch("epub_corrector.cli.os.path.isfile", return_value=True)
def test_run_translate(mock_isfile, mock_openai):
    parser = build_parser()
    args = parser.parse_args(["input.epub", "--translate", "French"])

    with patch("epub_corrector.cli.BookProcessor") as mock_processor_cls:
        mock_processor = MagicMock()
        mock_processor_cls.return_value = mock_processor
        result = run(args)
        assert result == 0


@patch("epub_corrector.cli.OpenAI")
@patch("epub_corrector.cli.os.path.isfile", return_value=True)
def test_run_rewrite(mock_isfile, mock_openai):
    parser = build_parser()
    args = parser.parse_args(["input.epub", "--rewrite"])

    with patch("epub_corrector.cli.BookProcessor") as mock_processor_cls:
        mock_processor = MagicMock()
        mock_processor_cls.return_value = mock_processor
        result = run(args)
        assert result == 0


@patch("epub_corrector.cli.load_dotenv")
@patch("epub_corrector.cli.build_parser")
def test_main(mock_build_parser, mock_load_dotenv):
    mock_parser = MagicMock()
    mock_parser.parse_args.return_value = argparse.Namespace(
        input=None,
        output=None,
        base_url="http://localhost:1234/v1",
        api_key="lm-studio",
        model="local-model",
        temperature=0.0,
        max_segments_per_request=1,
        max_chars_per_request=6000,
        max_context=0,
        max_context_chars=3000,
        similarity_threshold=0.88,
        max_change_ratio=0.20,
        report=None,
        checkpoint=None,
        verbose=False,
        debug=False,
        no_schema=False,
        rewrite=False,
        translate=None,
        max_workers=1,
        max_retries=3,
        batch=None,
        from_doc=None,
        to_doc=None,
    )
    mock_build_parser.return_value = mock_parser

    with patch("epub_corrector.cli.run", return_value=0):
        result = main()
        assert result == 0
