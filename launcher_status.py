"""Independent launcher indicators; never grants Claude readiness."""
from copy import deepcopy
import csv
import io
import re
import socket
import subprocess
import threading
import time

from launcher_service import HIDDEN, windows_exe
from network import local_file_port, parse_trace, request_text


class LauncherStatus:
    def __init__(self):
        self.lock = threading.Lock()
        self.refresh_lock = threading.Lock()
        self.exit = {'state': 'unknown', 'region': '', 'checked': 0, 'detail': '尚未查询出口地区'}
        self.local_cache = None

    def local(self, force=False):
        with self.lock:
            if not force and self.local_cache and time.time() - self.local_cache['checked'] < 5:
                return {**deepcopy(self.local_cache), 'exit': deepcopy(self.exit)}
            port = local_file_port()
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.7):
                    proxy = 'connected'
            except OSError:
                proxy = 'disconnected'
            try:
                process = subprocess.run([windows_exe('System32/tasklist.exe'), '/FO', 'CSV', '/NH'],
                                         capture_output=True, timeout=5, creationflags=HIDDEN)
                if process.returncode:
                    raise OSError('tasklist failed')
                names = {row[0].lower() for row in csv.reader(io.StringIO(process.stdout.decode('mbcs', errors='replace'))) if row}
                clash = 'running' if names & {'clash-verge.exe', 'mihomo.exe', 'clash.exe', 'clash-meta.exe'} else 'stopped'
            except (OSError, subprocess.TimeoutExpired):
                clash = 'unknown'
            self.local_cache = {'checked': time.time(), 'clash': clash, 'proxy': proxy, 'port': port}
            return {**deepcopy(self.local_cache), 'exit': deepcopy(self.exit)}

    def refresh_exit(self):
        # Only this explicit operation sends an external request. GET/local polling never does.
        if not self.refresh_lock.acquire(blocking=False):
            from launcher_store import LauncherError
            raise LauncherError(409, 'refresh_busy', '出口地区正在刷新，请稍候')
        try:
            try:
                response, _ = request_text('https://www.cloudflare.com/cdn-cgi/trace', local_file_port())
                _, region = parse_trace(response)
                if not re.fullmatch('[A-Z]{2}', region):
                    raise ValueError('missing region')
                result = {'state': 'info', 'region': region, 'checked': time.time(),
                          'detail': '本机代理到 Cloudflare 的查询出口；不代表各应用出口或 Claude 检测通过'}
            except Exception:
                result = {'state': 'unknown', 'region': '', 'checked': time.time(), 'detail': '出口地区未取得有效结果，请手动重试'}
            with self.lock:
                self.exit = result
            return deepcopy(result)
        finally:
            self.refresh_lock.release()
