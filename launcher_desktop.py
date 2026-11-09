"""Native tray and Alt+Q. No keyboard hook, administrator rights, or extra service required."""
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import subprocess
import threading
import time


def acquire_instance(root):
    import win32event, win32api, winerror
    key = hashlib.sha256(str(Path(root).resolve()).lower().encode()).hexdigest()[:20]
    handle = win32event.CreateMutex(None, False, 'Local\\IPDesk.backend.' + key)
    if win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS:
        return handle
    try:
        import json, requests
        from urllib.parse import urlsplit
        data = json.loads((Path(root) / 'reports' / 'desktop-session.json').read_text())
        parsed = urlsplit(data['url'])
        if parsed.hostname == '127.0.0.1' and parsed.port:
            session = requests.Session(); session.trust_env = False
            try:
                session.post(data['url'] + 'api/desktop/wake', json={},
                             headers={'Origin': 'http://127.0.0.1:' + str(parsed.port)}, timeout=4)
            finally:
                session.close()
    except Exception:
        pass
    win32api.CloseHandle(handle)
    return None


class Desktop:
    def __init__(self, url, on_exit):
        self.url, self.on_exit = url, on_exit
        self.lock = threading.RLock()
        self.wake_lock = threading.Lock()
        self.hwnd = None
        self.thread = None
        self.ready = threading.Event()
        self.last_open = 0
        self.info = {'tray': False, 'hotkey': False, 'shortcut': 'Alt + Q', 'wake_count': 0, 'message': ''}

    def snapshot(self):
        with self.lock:
            return dict(self.info)

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name='workbench-tray')
        self.thread.start(); self.ready.wait(3)

    def _run(self):
        import win32gui, win32api, win32con
        callback = win32con.WM_USER + 39
        taskbar_created = win32gui.RegisterWindowMessage('TaskbarCreated')
        def add_icon():
            handle = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
            win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, (self.hwnd, 1, win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
                                                       callback, handle, '朱玉坤工作台 · Alt+Q'))
            self.info['tray'] = True
        def handler(hwnd, message, wparam, lparam):
            if message == callback:
                if lparam in (win32con.WM_LBUTTONUP, win32con.WM_LBUTTONDBLCLK):
                    self.wake_async()
                elif lparam == win32con.WM_RBUTTONUP:
                    menu = win32gui.CreatePopupMenu()
                    win32gui.AppendMenu(menu, win32con.MF_STRING, 1, '显示工作台（Alt+Q）')
                    win32gui.AppendMenu(menu, win32con.MF_STRING, 2, '退出工作台')
                    try:
                        win32gui.SetForegroundWindow(hwnd)
                        x, y = win32gui.GetCursorPos()
                        choice = win32gui.TrackPopupMenu(menu, win32con.TPM_RETURNCMD | win32con.TPM_RIGHTBUTTON, x, y, 0, hwnd, None)
                        if choice == 1: self.wake_async()
                        elif choice == 2: threading.Thread(target=self.on_exit, daemon=True).start()
                    finally:
                        win32gui.DestroyMenu(menu)
                return 0
            if message == win32con.WM_HOTKEY:
                self.wake_async(); return 0
            if message == taskbar_created:
                add_icon(); return 0
            if message == win32con.WM_CLOSE:
                win32gui.DestroyWindow(hwnd); return 0
            if message == win32con.WM_DESTROY:
                win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (hwnd, 1))
                ctypes.windll.user32.UnregisterHotKey(hwnd, 1)
                self.info.update(tray=False, hotkey=False)
                win32gui.PostQuitMessage(0); return 0
            return win32gui.DefWindowProc(hwnd, message, wparam, lparam)
        try:
            name = 'IPDesk.Tray.' + hashlib.sha256(self.url.encode()).hexdigest()[:12]
            window_class = win32gui.WNDCLASS(); window_class.hInstance = win32api.GetModuleHandle(None)
            window_class.lpszClassName = name; window_class.lpfnWndProc = handler
            win32gui.RegisterClass(window_class)
            self.hwnd = win32gui.CreateWindow(name, 'IPDesk background', 0, 0, 0, 0, 0, 0, 0, window_class.hInstance, None)
            add_icon()
            user = ctypes.WinDLL('user32', use_last_error=True)
            user.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            user.RegisterHotKey.restype = wintypes.BOOL
            self.info['hotkey'] = bool(user.RegisterHotKey(self.hwnd, 1, 0x4001, ord('Q')))
            if not self.info['hotkey']:
                self.info['message'] = 'Alt+Q 已被占用，仍可通过托盘显示工作台'
            self.ready.set()
            win32gui.PumpMessages()
        except Exception:
            self.info['message'] = '托盘或热键未能启用，仍可使用桌面快捷方式'
            self.ready.set()

    def wake_async(self):
        threading.Thread(target=self.wake, daemon=True).start()

    @staticmethod
    def app_windows(url=None):
        import win32gui, win32process
        import pythoncom
        from win32com.propsys import propsys, pscon
        from urllib.parse import urlsplit
        pythoncom.CoInitialize()
        token = urlsplit(url).path.strip('/') if url else ''
        windows = []
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        def visit(hwnd, unused):
            handle = None
            try:
                if not (win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == '朱玉坤工作台'
                        and win32gui.GetClassName(hwnd).startswith('Chrome_WidgetWin')):
                    return
                pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                handle = kernel.OpenProcess(0x1000, False, pid)
                name, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
                if handle and kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)) and Path(name.value).name.lower() == 'msedge.exe':
                    if token:
                        properties = propsys.SHGetPropertyStoreForWindow(hwnd)
                        app_id = properties.GetValue(pscon.PKEY_AppUserModel_ID).GetValue() or ''
                        properties = None
                        # Edge shortens long URL components in its AppUserModelID, retaining both ends.
                        if token[:4] not in app_id or token[-12:] not in app_id:
                            return
                    windows.append(hwnd)
            except Exception:
                pass
            finally:
                if handle:
                    kernel.CloseHandle(handle)
        try:
            win32gui.EnumWindows(visit, None)
            return windows
        finally:
            pythoncom.CoUninitialize()

    def wake(self):
        if not self.wake_lock.acquire(blocking=False):
            return {'ok': True}
        try:
            import win32gui, win32con
            windows = self.app_windows(self.url)
            if windows:
                try:
                    win32gui.ShowWindow(windows[0], win32con.SW_RESTORE)
                    win32gui.SetForegroundWindow(windows[0])
                except Exception:
                    win32gui.FlashWindow(windows[0], True)
            else:
                edge = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
                if time.monotonic() - self.last_open > 3:
                    subprocess.Popen([str(edge), '--app=' + self.url, '--start-maximized'], creationflags=subprocess.CREATE_NO_WINDOW)
                    self.last_open = time.monotonic()
            with self.lock:
                self.info['wake_count'] += 1
            return {'ok': True}
        finally:
            self.wake_lock.release()

    def stop(self):
        if self.hwnd:
            import win32gui, win32con
            try: win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception: pass
        if self.thread:
            self.thread.join(timeout=2)
