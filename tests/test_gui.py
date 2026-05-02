import threading
import tkinter as tk
from unittest.mock import MagicMock, patch

import pytest

from epub_corrector.gui.app import EpubCorrectorApp
from epub_corrector.gui.base_tab import BaseTab
from epub_corrector.gui.batch_tab import BatchCorrectionTab
from epub_corrector.gui.debug_tab import DebugTab
from epub_corrector.gui.log_handler import GuiLogHandler, TeeStream, install_tee_stream
from epub_corrector.gui.review_bridge import GuiReview
from epub_corrector.gui.review_panel import ReviewPanel
from epub_corrector.gui.simple_tab import SimpleCorrectionTab
from epub_corrector.gui.summary_tab import SummaryTab
from epub_corrector.gui.utils import fetch_models
from epub_corrector.gui.widgets import (
    CheckboxBar,
    FilePickerRow,
    OptionsGrid,
    ScrollableFrame,
    ServerConfigFrame,
)
from epub_corrector.gui.worker import WorkerController


@pytest.fixture
def root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


class TestGuiLogHandler:
    def test_emit_and_get_records(self):
        handler = GuiLogHandler()
        import logging

        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        handler.emit(record)
        records = handler.get_records()
        assert len(records) == 1
        assert records[0].msg == "msg"

    def test_get_records_empty(self):
        handler = GuiLogHandler()
        assert handler.get_records() == []

    def test_clear(self):
        handler = GuiLogHandler()
        import logging

        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        handler.emit(record)
        handler.clear()
        assert handler.get_records() == []


class TestTeeStream:
    def test_write(self):
        original = MagicMock()
        callback = MagicMock()
        tee = TeeStream(original, callback)
        tee.write("hello")
        original.write.assert_called_once_with("hello")
        callback.assert_called_once_with("hello")

    def test_write_empty(self):
        original = MagicMock()
        callback = MagicMock()
        tee = TeeStream(original, callback)
        tee.write("")
        original.write.assert_called_once_with("")
        callback.assert_not_called()

    def test_flush(self):
        original = MagicMock()
        tee = TeeStream(original, MagicMock())
        tee.flush()
        original.flush.assert_called_once()

    def test_getattr(self):
        original = MagicMock()
        original.encoding = "utf-8"
        tee = TeeStream(original, MagicMock())
        assert tee.encoding == "utf-8"


class TestInstallTeeStream:
    def test_install(self):
        callback = MagicMock()
        import sys

        old_stdout = sys.stdout
        try:
            tee = install_tee_stream(callback)
            assert sys.stdout is tee
        finally:
            sys.stdout = old_stdout


class TestGuiReview:
    def test_ask_returns_accept(self):
        stop_event = threading.Event()
        review = GuiReview(stop_event)

        def responder():
            review.response_queue.put("accept")

        threading.Thread(target=responder, daemon=True).start()
        result = review.ask("orig", "prop", "doc")
        assert result == "accept"

    def test_ask_raises_stop_processing(self):
        stop_event = threading.Event()
        review = GuiReview(stop_event)
        stop_event.set()

        with pytest.raises(Exception):
            review.ask("orig", "prop", "doc")

    def test_poll_returns_none(self):
        stop_event = threading.Event()
        review = GuiReview(stop_event)
        assert review.poll() is None


class TestReviewPanel:
    def test_initial_state(self, root):
        callback = MagicMock()
        panel = ReviewPanel(root, on_action=callback)
        assert panel.is_pending() is False

    def test_show_review(self, root):
        callback = MagicMock()
        panel = ReviewPanel(root, on_action=callback)
        panel.show_review("hello", "world", "doc.xhtml")
        assert panel.is_pending() is True

    def test_clear_review(self, root):
        callback = MagicMock()
        panel = ReviewPanel(root, on_action=callback)
        panel.show_review("hello", "world", "doc.xhtml")
        panel.clear_review()
        assert panel.is_pending() is False

    def test_handle_key_accept(self, root):
        callback = MagicMock()
        panel = ReviewPanel(root, on_action=callback)
        panel.show_review("hello", "world", "doc.xhtml")

        event = MagicMock()
        event.keysym = "Return"
        assert panel.handle_key(event) is True
        callback.assert_called_once_with("accept")

    def test_handle_key_reject(self, root):
        callback = MagicMock()
        panel = ReviewPanel(root, on_action=callback)
        panel.show_review("hello", "world", "doc.xhtml")

        event = MagicMock()
        event.keysym = "n"
        assert panel.handle_key(event) is True
        callback.assert_called_once_with("reject")

    def test_handle_key_retry(self, root):
        callback = MagicMock()
        panel = ReviewPanel(root, on_action=callback)
        panel.show_review("hello", "world", "doc.xhtml")

        event = MagicMock()
        event.keysym = "r"
        assert panel.handle_key(event) is True
        callback.assert_called_once_with("retry")

    def test_handle_key_accept_all(self, root):
        callback = MagicMock()
        panel = ReviewPanel(root, on_action=callback)
        panel.show_review("hello", "world", "doc.xhtml")

        event = MagicMock()
        event.keysym = "a"
        assert panel.handle_key(event) is True
        callback.assert_called_once_with("accept_all")

    def test_handle_key_escape(self, root):
        callback = MagicMock()
        panel = ReviewPanel(root, on_action=callback)
        panel.show_review("hello", "world", "doc.xhtml")

        event = MagicMock()
        event.keysym = "Escape"
        assert panel.handle_key(event) is True
        callback.assert_called_once_with("reject")

    def test_handle_key_no_pending(self, root):
        callback = MagicMock()
        panel = ReviewPanel(root, on_action=callback)
        event = MagicMock()
        event.keysym = "Return"
        assert panel.handle_key(event) is False

    def test_handle_key_unknown(self, root):
        callback = MagicMock()
        panel = ReviewPanel(root, on_action=callback)
        panel.show_review("hello", "world", "doc.xhtml")

        event = MagicMock()
        event.keysym = "x"
        assert panel.handle_key(event) is False

    def test_fill_diff(self, root):
        callback = MagicMock()
        panel = ReviewPanel(root, on_action=callback)
        widget = tk.Text(root)
        panel._fill_diff(widget, "hello world", "hello worlds", is_original=True)
        content = widget.get("1.0", tk.END)
        assert "hello" in content


class TestBaseTab:
    def test_abstract_title(self):
        with pytest.raises(TypeError):
            BaseTab(MagicMock())

    def test_concrete_tab(self, root):
        class ConcreteTab(BaseTab):
            def title(self):
                return "Test"

        app = MagicMock()
        tab = ConcreteTab(app)
        assert tab.title() == "Test"
        assert tab.can_start() is True
        assert tab.on_start() is None
        assert tab.on_stop() is None
        assert tab.on_show() is None
        assert tab.on_hide() is None
        frame = tk.Frame(root)
        assert tab.build(frame) is None


class TestWorkerController:
    def test_is_running_false(self, root):
        wc = WorkerController(root)
        assert wc.is_running() is False

    def test_start_and_stop(self, root):
        wc = WorkerController(root)
        event = threading.Event()

        def target():
            event.wait(0.5)

        wc.start(target)
        assert wc.is_running() is True
        event.set()
        import time

        time.sleep(0.1)
        wc.stop()

    def test_get_stop_check(self, root):
        wc = WorkerController(root)
        check = wc.get_stop_check()
        assert check() is False
        wc.stop()
        assert check() is True


class TestFilePickerRow:
    def test_creation(self, root):
        var = tk.StringVar()
        row = FilePickerRow(root, "Label", var, row=0)
        assert row.variable is var

    def test_set_state(self, root):
        var = tk.StringVar()
        row = FilePickerRow(root, "Label", var, row=0)
        row.set_state(tk.DISABLED)


class TestServerConfigFrame:
    def test_creation(self, root):
        frame = ServerConfigFrame(root)
        assert frame.get_config()["base_url"] == "http://127.0.0.1:1234/v1"

    def test_toggle_key_visibility(self, root):
        frame = ServerConfigFrame(root)
        frame.show_key_var.set(True)
        frame._toggle_key_visibility()
        assert frame.api_key_entry.cget("show") == ""
        frame.show_key_var.set(False)
        frame._toggle_key_visibility()
        assert frame.api_key_entry.cget("show") == "*"

    @patch("epub_corrector.gui.widgets.fetch_models")
    def test_refresh_models_success(self, mock_fetch, root):
        mock_fetch.return_value = ["model1", "model2"]
        frame = ServerConfigFrame(root)
        frame._refresh_models()
        assert frame.model_var.get() == "model1"

    @patch("epub_corrector.gui.widgets.fetch_models")
    def test_refresh_models_error(self, mock_fetch, root):
        mock_fetch.side_effect = RuntimeError("fail")
        frame = ServerConfigFrame(root)
        with patch("tkinter.messagebox.showerror"):
            frame._refresh_models()


class TestOptionsGrid:
    def test_get(self, root):
        var = tk.DoubleVar(value=1.5)
        grid = OptionsGrid(root, [("Test", "Test", var)])
        assert grid.get("Test", float) == 1.5

    def test_get_invalid(self, root):
        var = tk.DoubleVar(value="not_a_number")
        grid = OptionsGrid(root, [("Test", "Test", var)])
        with pytest.raises(ValueError):
            grid.get("Test", float)


class TestCheckboxBar:
    def test_creation(self, root):
        var = tk.BooleanVar(value=True)
        bar = CheckboxBar(root, [("Check", var)])
        assert bar.vars["Check"] is var


class TestScrollableFrame:
    def test_creation(self, root):
        sf = ScrollableFrame(root)
        assert sf.inner is not None


class TestDebugTab:
    def test_creation(self, root):
        tab = DebugTab(MagicMock())
        frame = tk.Frame(root)
        tab.build(frame)
        assert tab.title() == "Debug"

    def test_append(self, root):
        tab = DebugTab(MagicMock())
        frame = tk.Frame(root)
        tab.build(frame)
        tab.append("test log\n")
        content = tab.log_text.get("1.0", tk.END)
        assert "test log" in content

    def test_clear(self, root):
        app = MagicMock()
        app.log_handler = GuiLogHandler()
        tab = DebugTab(app)
        frame = tk.Frame(root)
        tab.build(frame)
        tab.append("test")
        tab._clear()
        content = tab.log_text.get("1.0", tk.END).strip()
        assert content == ""

    def test_on_level_change(self, root):
        tab = DebugTab(MagicMock())
        frame = tk.Frame(root)
        tab.build(frame)
        tab.level_var.set("ERROR")
        tab._on_level_change("ERROR")
        import logging

        assert logging.getLogger().level == logging.ERROR


class TestSimpleCorrectionTab:
    def test_creation(self, root):
        app = MagicMock()
        app.worker = WorkerController(root)
        tab = SimpleCorrectionTab(app)
        frame = tk.Frame(root)
        tab.build(frame)
        assert tab.title() == "Simple Correction"

    def test_on_auto_accept_toggle(self, root):
        app = MagicMock()
        app.worker = WorkerController(root)
        tab = SimpleCorrectionTab(app)
        frame = tk.Frame(root)
        tab.build(frame)
        tab.auto_accept_var.set(True)
        assert tab.review_state.auto_accept is True


class TestBatchCorrectionTab:
    def test_creation(self, root):
        app = MagicMock()
        app.worker = WorkerController(root)
        tab = BatchCorrectionTab(app)
        frame = tk.Frame(root)
        tab.build(frame)
        assert tab.title() == "Batch Correction"

    def test_on_auto_accept_toggle(self, root):
        app = MagicMock()
        app.worker = WorkerController(root)
        tab = BatchCorrectionTab(app)
        frame = tk.Frame(root)
        tab.build(frame)
        tab.auto_accept_var.set(True)
        assert tab.review_state.auto_accept is True


class TestTranslateTab:
    def test_creation(self, root):
        from epub_corrector.gui.translate_tab import TranslateTab

        app = MagicMock()
        app.worker = WorkerController(root)
        tab = TranslateTab(app)
        frame = tk.Frame(root)
        tab.build(frame)
        assert tab.title() == "Translate"


class TestEpubCorrectorApp:
    def test_creation(self, root):
        app = EpubCorrectorApp(root)
        assert app.root is root
        assert len(app.tabs) == 5

    def test_current_tab(self, root):
        app = EpubCorrectorApp(root)
        tab = app._current_tab()
        assert tab is app.tabs[0]

    def test_update_start_button_running(self, root):
        app = EpubCorrectorApp(root)
        import threading
        app.worker._thread = MagicMock(spec=threading.Thread)
        app.worker._thread.is_alive.return_value = True
        app._update_start_button()
        assert str(app.start_btn.cget("state")) == "disabled"
        assert str(app.stop_btn.cget("state")) == "normal"

    def test_set_running(self, root):
        app = EpubCorrectorApp(root)
        app.set_running(True)
        assert app.status_label.cget("text") == "Running..."
        app.set_running(False)
        assert app.status_label.cget("text") == "Ready"

    def test_on_review_action_accept_all(self, root):
        app = EpubCorrectorApp(root)
        app._on_review_action("accept_all")
        assert app.worker.review.response_queue.get(timeout=0.1) == "accept_all"

    def test_on_review_action_other(self, root):
        app = EpubCorrectorApp(root)
        app._on_review_action("reject")
        assert app.worker.review.response_queue.get(timeout=0.1) == "reject"

    def test_on_stdout_write(self, root):
        app = EpubCorrectorApp(root)
        app._on_stdout_write("test message")
        records = app.log_handler.get_records()
        assert len(records) >= 1
        assert records[-1].msg == "test message"

    def test_on_stdout_write_empty(self, root):
        app = EpubCorrectorApp(root)
        app._on_stdout_write("   ")
        # Should not add empty records


class TestSummaryTab:
    def test_creation(self, root):
        app = MagicMock()
        app.worker = WorkerController(root)
        tab = SummaryTab(app)
        frame = tk.Frame(root)
        tab.build(frame)
        assert tab.title() == "Summary"
        assert tab.can_start() is False

    def test_format_time(self):
        assert SummaryTab._format_time(45.5) == "45m"
        assert SummaryTab._format_time(125.0) == "2h 5m"
        assert SummaryTab._format_time(0.0) == "0m"


class TestFetchModels:
    @patch("epub_corrector.gui.utils.urlopen")
    def test_fetch_models_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"data": [{"id": "model1"}]}'
        mock_urlopen.return_value.__enter__.return_value = mock_response
        result = fetch_models("http://localhost:1234/v1")
        assert result == ["model1"]

    @patch("epub_corrector.gui.utils.urlopen")
    def test_fetch_models_error(self, mock_urlopen):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("connection failed")
        with pytest.raises(RuntimeError, match="Failed to fetch models"):
            fetch_models("http://localhost:1234/v1")
