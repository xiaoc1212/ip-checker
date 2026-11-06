"""Windows launch adapter. Execution targets are always resolved from LauncherStore."""
import csv
import ctypes
from ctypes import wintypes
import io
import json
import ntpath
import os
from pathlib import Path
import subprocess
import threading
import winreg

from launcher_store import LauncherError, validate_item, local_path, web_url

HIDDEN = subprocess.CREATE_NO_WINDOW


def windows_exe(name):
    # SystemRoot is inherited from the local desktop process, never an API field.
    return str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / name)


def split_arguments(arguments):
    if not arguments:
        return []
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    count = ctypes.c_int()
    pointer = shell.CommandLineToArgvW('launcher.exe ' + arguments, ctypes.byref(count))
    if not pointer:
        raise LauncherError(400, 'invalid_arguments', '无法解析启动参数')
    try:
        return [pointer[index] for index in range(1, count.value)]
    finally:
        kernel.LocalFree(ctypes.cast(pointer, wintypes.HLOCAL))


class WindowsLauncher:
    def pick_target(self, kind):
        from network import python_exe
        result = subprocess.run([python_exe(), str(Path(__file__).with_name('launcher_picker.py')), kind],
                                capture_output=True, timeout=180, creationflags=HIDDEN)
        if result.returncode:
            raise LauncherError(503, 'picker_failed', '无法打开文件选择器，请手动填写本机位置')
        target = json.loads(result.stdout.decode('utf-8-sig'))['target']
        local_path(target, '所选目标')
        if target and not (Path(target).is_file() and Path(target).suffix.lower() == '.exe' if kind == 'exe' else Path(target).is_dir()):
            raise LauncherError(400, 'invalid_target', '请选择有效的本机程序或文件夹')
        return {'target': target, 'cancelled': not bool(target)}

    def reveal(self, item):
        config = item['target_config']
        path = Path(config['target'])
        if item['launch_type'] not in ('exe', 'folder') or not config['target'] or not path.exists():
            raise LauncherError(409, 'target_missing', '尚未配置有效的本机位置，请先编辑项目')
        subprocess.Popen([windows_exe('explorer.exe'), '/select,' + str(path)], shell=False)

    def browser_path(self, browser):
        relative = {'msedge': 'Microsoft/Edge/Application/msedge.exe',
                    'chrome': 'Google/Chrome/Application/chrome.exe'}[browser]
        candidates = [Path(os.environ.get(key, fallback)) / relative for key, fallback in
                      [('ProgramFiles', 'C:/Program Files'), ('ProgramFiles(x86)', 'C:/Program Files (x86)'),
                       ('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))]]
        return next((str(path) for path in candidates if path.is_file()), '')

    def open_web(self, config):
        if config['browser'] == 'default':
            os.startfile(config['target'])
            return
        executable = self.browser_path(config['browser'])
        if not executable:
            raise LauncherError(409, 'browser_missing', '未找到指定浏览器，请安装或改用默认浏览器')
        args = [executable]
        if config['profile']:
            args.append('--profile-directory=' + config['profile'])
        subprocess.Popen([*args, '--new-tab', config['target']], shell=False)

    def running_pids(self, process_name):
        result = subprocess.run([windows_exe('System32/tasklist.exe'), '/FI', 'IMAGENAME eq ' + process_name,
                                 '/FO', 'CSV', '/NH'], capture_output=True, timeout=8, creationflags=HIDDEN)
        if result.returncode:
            raise LauncherError(503, 'process_check_failed', '无法确认客户端是否已运行，请稍后重试')
        return [int(row[1]) for row in csv.reader(io.StringIO(result.stdout.decode('mbcs', errors='replace')))
                if len(row) > 1 and row[0].lower() == process_name.lower() and row[1].isdigit()]

    def activate(self, pids):
        import win32con
        import win32gui
        import win32process
        windows = []
        def visit(hwnd, unused):
            try:
                if win32gui.IsWindowVisible(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] in pids:
                    windows.append(hwnd)
            except Exception:
                pass  # A window may disappear during enumeration.
        try:
            win32gui.EnumWindows(visit, None)
        except Exception:
            return False
        if not windows:
            return False
        hwnd = windows[0]
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            return win32gui.GetForegroundWindow() == hwnd
        except Exception:
            # Windows may deny focus. The already-running application is never duplicated.
            return False

    def launch(self, item):
        kind, config = item['launch_type'], item['target_config']
        target = config['target']
        if kind == 'web':
            self.open_web(config)
        elif kind in ('exe', 'folder'):
            path = Path(target)
            if not (path.is_file() if kind == 'exe' else path.is_dir()):
                raise LauncherError(409, 'target_missing', '目标不存在，请重新定位程序或文件夹',
                                    actions=['relocate', 'official'] if config['official_url'] else ['relocate'],
                                    official_url=config['official_url'])
            if config['working_dir'] and not Path(config['working_dir']).is_dir():
                raise LauncherError(409, 'working_dir_missing', '工作目录不存在，请编辑配置')
            if config['singleton_process']:
                pids = self.running_pids(config['singleton_process'])
                if pids:
                    return {'status': 'activated' if self.activate(pids) else 'already_running'}
            if kind == 'folder':
                os.startfile(str(path))
            else:
                subprocess.Popen([str(path), *split_arguments(config['args'])],
                                 cwd=config['working_dir'] or None, shell=False)
        elif kind == 'uwp':
            if config['singleton_process']:
                pids = self.running_pids(config['singleton_process'])
                if pids:
                    return {'status': 'activated' if self.activate(pids) else 'already_running'}
            subprocess.Popen([windows_exe('explorer.exe'), 'shell:AppsFolder\\' + target], shell=False)
        return {'status': 'opened'}

    def discover_defaults(self):
        local = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
        program = Path(os.environ.get('ProgramFiles', 'C:/Program Files'))
        program86 = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)'))
        known = {
            'antigravity-desktop': [local / 'Programs/Antigravity/Antigravity.exe', program / 'Antigravity/Antigravity.exe'],
            'chatgpt-desktop': [local / 'Programs/ChatGPT/ChatGPT.exe', local / 'ChatGPT/ChatGPT.exe'],
            'steam': [program86 / 'Steam/steam.exe', program / 'Steam/steam.exe'],
            'yy': [program86 / 'duowan/yy/YY.exe', program / 'duowan/yy/YY.exe', Path('D:/yy/YY.exe')],
            'clash-desktop': [program / 'Clash Verge/clash-verge.exe', local / 'Programs/Clash Verge/clash-verge.exe'],
        }
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
                known['steam'].insert(0, Path(winreg.QueryValueEx(key, 'SteamExe')[0]))
        except OSError:
            pass
        for item_id, name in [('steam', 'steam.exe'), ('yy', 'YY.exe'), ('clash-desktop', 'clash-verge.exe'),
                              ('antigravity-desktop', 'Antigravity.exe'), ('chatgpt-desktop', 'ChatGPT.exe')]:
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, 'Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\' + name) as key:
                        known[item_id].insert(0, Path(winreg.QueryValueEx(key, '')[0]))
                except OSError:
                    pass
        discovered = {}
        for item_id, paths in known.items():
            path = next((path for path in paths if path.is_file()), None)
            if path:
                discovered[item_id] = {'launch_type': 'exe', 'target': str(path), 'singleton_process': path.name}
        # Only initial known-app discovery here. The searchable full software library is phase 3.
        script = """
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new();
$items = @(Get-AppxPackage *ChatGPT* | ForEach-Object {
  $p = $_; $m = $p | Get-AppxPackageManifest;
  foreach ($a in $m.Package.Applications.Application) {
    if ([string]$a.Executable -match '(?i)chatgpt\\.exe$') {
      @{target=($p.PackageFamilyName+'!'+$a.Id)}
    }
  }
}); ConvertTo-Json -InputObject $items -Compress
"""
        try:
            result = subprocess.run([windows_exe('System32/WindowsPowerShell/v1.0/powershell.exe'),
                                     '-NoProfile', '-NonInteractive', '-Command', script],
                                    capture_output=True, timeout=15, creationflags=HIDDEN)
            packages = json.loads(result.stdout.decode('utf-8-sig'))
            if isinstance(packages, list) and packages:
                discovered['chatgpt-desktop'] = {'launch_type': 'uwp', 'target': packages[0]['target'],
                                                 'singleton_process': 'ChatGPT.exe'}
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            pass
        return discovered


class LauncherService:
    def __init__(self, store, adapter=None):
        self.store = store
        self.adapter = adapter or WindowsLauncher()
        self.lock = threading.Lock()

    def item_action(self, action, body):
        if not isinstance(body, dict) or set(body) != {'id'}:
            raise LauncherError(400, 'id_only', '该操作仅允许传入启动项 ID')
        item = validate_item(self.store.get('items', body['id']))
        if action == 'reveal':
            self.adapter.reveal(item)
        else:
            config = dict(item['target_config'])
            url = config['official_url'] or (config['target'] if item['launch_type'] == 'web' else '')
            if not url:
                raise LauncherError(409, 'url_missing', '请先在编辑配置中填写官网地址')
            web_url(url)
            self.adapter.open_web({**config, 'target': url, 'browser': 'default', 'profile': ''})
        return {'ok': True}

    def subscription_open(self, body):
        if not isinstance(body, dict) or set(body) != {'id', 'link'} or body['link'] not in ('official_url', 'renew_url', 'admin_url'):
            raise LauncherError(400, 'invalid_link', '请指定订阅 ID 和入口类型')
        sub = self.store.get('subscriptions', body['id'])
        url = web_url(sub[body['link']])
        if not url:
            raise LauncherError(409, 'url_missing', '请先编辑订阅，填写这个入口地址')
        self.adapter.open_web({'target': url, 'browser': 'default', 'profile': ''})
        return {'ok': True}

    def open(self, body, singleton=False):
        if not isinstance(body, dict) or set(body) != {'id'}:
            raise LauncherError(400, 'id_only', '启动请求仅允许传入启动项 ID')
        with self.lock:
            item = validate_item(self.store.get('items', body['id']))
            if singleton and item['launch_type'] == 'exe' and not item['target_config']['singleton_process']:
                from launcher_store import BROWSERS
                name = ntpath.basename(item['target_config']['target'])
                if name.lower() not in BROWSERS:
                    item['target_config']['singleton_process'] = name
            if item['launch_type'] == 'claude_protected':
                # No statistics: opening the inspection panel is not a successful client launch.
                return {'ok': True, 'status': 'requires_check', 'action': 'open_claude_drawer', 'id': item['id']}
            if not item['target_config']['target']:
                raise LauncherError(409, 'target_missing', '请先填写网址或选择本机程序',
                                    official_url=item['target_config']['official_url'], actions=['edit', 'official'])
            try:
                result = self.adapter.launch(item)
            except LauncherError:
                raise
            except (OSError, subprocess.SubprocessError, ImportError) as exc:
                raise LauncherError(503, 'launch_failed', '未能打开目标，请检查安装位置或系统权限') from exc
            try:
                self.store.record_use(item['id'])
            except (OSError, LauncherError):
                # Opening succeeded; do not invite a duplicate launch just because history could not save.
                return {'ok': True, 'id': item['id'], **result, 'history_saved': False,
                        'warning': '已发送打开请求，但使用记录未能保存'}
            return {'ok': True, 'id': item['id'], **result}
