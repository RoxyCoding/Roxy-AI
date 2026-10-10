"""Native path selection for settings; no files are opened or modified."""

import subprocess
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog


def _applescript_text(text: str) -> str:
    return '"' + text.replace(chr(92), chr(92) * 2).replace('"', chr(92) + '"') + '"'


def _select_path_macos(kind: str, path: str) -> str | None:
    """Tk windows must live on the main thread on macOS; the system dialog has no such limit."""
    prompt = "フォルダを選択" if kind == "folder" else "ファイルを選択"
    script = f"choose {'folder' if kind == 'folder' else 'file'} with prompt {_applescript_text(prompt)}"
    if path:
        current = Path(path)
        directory = current if current.is_dir() else current.parent
        if directory.is_dir():
            script += f" default location (POSIX file {_applescript_text(str(directory.resolve()))})"
    if kind == "blend":
        script += ' of type {"blend"}'
    result = subprocess.run(["osascript", "-e", f"POSIX path of ({script})"], capture_output=True, text=True)
    if result.returncode != 0:  # cancelled (error -128) or no GUI session
        if "-128" in result.stderr:
            return None
        raise ValueError("参照ダイアログを開けません。パスを直接入力してください。")
    selected = result.stdout.strip()
    return str(Path(selected)) if selected else None


def select_path(kind: str, path: str = "") -> str | None:
    if sys.platform == "darwin":
        return _select_path_macos(kind, path)
    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        options = {"parent": root, "title": "フォルダを選択" if kind == "folder" else "ファイルを選択"}
        if path:
            current = Path(path)
            directory = current if current.is_dir() else current.parent
            if directory.is_dir():
                options["initialdir"] = str(directory.resolve())
            if current.is_file():
                options["initialfile"] = current.name
        if kind == "folder":
            selected = filedialog.askdirectory(mustexist=True, **options)
        else:
            filters = [("Blenderファイル", "*.blend")] if kind == "blend" else []
            selected = filedialog.askopenfilename(filetypes=[*filters, ("すべてのファイル", "*.*")], **options)
        return str(Path(selected)) if selected else None
    except tk.TclError as error:
        raise ValueError("参照ダイアログを開けません。パスを直接入力してください。") from error
    finally:
        if root is not None:
            root.destroy()
