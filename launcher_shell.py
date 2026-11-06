"""Isolated Windows COM parser. A faulty shell extension cannot take down the dashboard."""
import json
import re
import sys


def parse(paths):
    import pythoncom
    from win32com.client import Dispatch
    pythoncom.CoInitialize()
    shell = namespace = shortcut = folder = entry = None
    result = {}
    try:
        shell = Dispatch('WScript.Shell')
        for path in paths:
            try:
                shortcut = shell.CreateShortcut(path)
                target, args, working, icon = shortcut.TargetPath, shortcut.Arguments, shortcut.WorkingDirectory, shortcut.IconLocation or ''
                if not target or target.lower().endswith('explorer.exe') and 'shell:appsfolder' in args.lower():
                    from pathlib import Path
                    namespace = namespace or Dispatch('Shell.Application')
                    folder = namespace.NameSpace(str(Path(path).parent))
                    entry = folder.ParseName(Path(path).name)
                    aumid = entry.ExtendedProperty('System.AppUserModel.ID')
                    if aumid and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*![A-Za-z0-9._-]+', aumid):
                        target, args, working = 'shell:AppsFolder\\' + aumid, '', ''
                    elif 'shell:appsfolder\\' in args.lower():
                        target, args, working = args[args.lower().index('shell:appsfolder\\'):].strip('"'), '', ''
                result[path] = {'target': target, 'args': args, 'working_dir': working, 'icon': icon}
            except Exception:
                try:
                    import pylnk3
                    link = pylnk3.parse(path)
                    result[path] = {'target': link.path, 'args': link.arguments or '', 'working_dir': link.work_dir or '', 'icon': link.icon or ''}
                except Exception:
                    result[path] = {'error': True}
            finally:
                shortcut = folder = entry = None
        return result
    finally:
        shell = namespace = shortcut = folder = entry = None
        pythoncom.CoUninitialize()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    paths = json.loads(sys.stdin.buffer.read().decode('utf-8'))
    if not isinstance(paths, list) or len(paths) > 8000 or any(not isinstance(p, str) for p in paths):
        raise SystemExit(2)
    print(json.dumps(parse(paths), ensure_ascii=False))
