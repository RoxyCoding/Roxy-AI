"""Native path selection for settings; no files are opened or modified."""

from pathlib import Path
import tkinter as tk
from tkinter import filedialog


def select_path(kind: str, path: str = "") -> str | None:
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
