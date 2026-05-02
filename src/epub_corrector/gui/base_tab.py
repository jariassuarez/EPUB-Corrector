from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import tkinter as tk


class BaseTab(ABC):
    """Abstract base class for all notebook tabs."""

    def __init__(self, app: Any) -> None:
        self.app = app

    @abstractmethod
    def title(self) -> str:
        """Return the tab title shown in the notebook."""
        ...

    def build(self, parent: tk.Widget) -> None:
        """Build the tab's UI inside the given frame."""
        return None

    def on_show(self) -> None:
        """Called when this tab becomes active."""
        return None

    def on_hide(self) -> None:
        """Called when this tab is deselected."""
        return None

    def can_start(self) -> bool:
        """Whether the global Start button should be enabled for this tab."""
        return True

    def on_start(self) -> None:
        """Called when the global Start button is pressed while this tab is active."""
        return None

    def on_stop(self) -> None:
        """Called when the global Stop button is pressed."""
        return None
