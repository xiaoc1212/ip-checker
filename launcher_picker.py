"""A separate process owns Tk's main thread; picking a target never launches it."""
import json
import sys


def choose(kind):
    if kind == 'shortcuts':
        import win32gui, win32con
        from win32com.shell import shell, shellcon
        try:
            value, _, _ = win32gui.GetOpenFileNameW(
                Title='选择快捷方式（可多选）', InitialDir=shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0),
                Filter='快捷方式\0*.lnk;*.url\0\0', MaxFile=65536,
                Flags=win32con.OFN_EXPLORER | win32con.OFN_ALLOWMULTISELECT | win32con.OFN_FILEMUSTEXIST | win32con.OFN_NODEREFERENCELINKS)
            parts = [part for part in value.split('\0') if part]
            from pathlib import Path
            return parts if len(parts) == 1 else [str(Path(parts[0]) / name) for name in parts[1:]]
        except Exception as exc:
            if getattr(exc, 'winerror', None) == 0 or exc.args and exc.args[0] == 0:
                return []
            raise
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        if kind == 'folder':
            return filedialog.askdirectory(parent=root, title='选择项目文件夹')
        return filedialog.askopenfilename(parent=root, title='选择本机程序', filetypes=[('应用程序', '*.exe')])
    finally:
        root.destroy()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    if sys.argv[1:] not in (['exe'], ['folder'], ['shortcuts']):
        raise SystemExit(2)
    print(json.dumps({'files' if sys.argv[1] == 'shortcuts' else 'target': choose(sys.argv[1])}, ensure_ascii=False))
