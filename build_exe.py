#!/usr/bin/env python3
"""Build a standalone Windows .exe using PyInstaller.

Run on Windows (or in a Windows VM/Wine) after installing pyinstaller:
    uv sync --extra build
    python build_exe.py

The resulting executable will be in dist/epub-corrector-gui.exe
"""

import subprocess
import sys


def main() -> int:
    # Use semicolon for Windows, colon for Linux (PyInstaller --add-data syntax)
    sep = ";" if sys.platform.startswith("win") else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", "epub-corrector-gui",
        "--hidden-import", "ebooklib",
        "--hidden-import", "ebooklib.epub",
        "--collect-all", "ebooklib",
        "--collect-all", "customtkinter",
        "--add-data", f"src/epub_corrector{sep}epub_corrector",
        "-p", "src",
        "src/gui_entry.py",
    ]
    print("Running:", " ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
