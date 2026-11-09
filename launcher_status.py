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
        self.exit = {'state': 'unknown', 'region': '', 'ip': '', 'latency_ms': None, 'checked': 0, 'detail': '尚未查询出口 IP'}
        self.local_cache = None
        self.traffic_sample = None
        self.provider_lock = threading.Lock()
        self.provider_cache = None

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
            traffic = None
            try:
                import psutil  # 网卡吞吐速率并非宽带测速
                counters = psutil.net_io_counters()
                sample = (time.monotonic(), counters.bytes_recv, counters.bytes_sent)
                if self.traffic_sample and sample[0] > self.traffic_sample[0]:
                    delta = sample[0] - self.traffic_sample[0]
                    traffic = {'download_bps': max(0, int((sample[1] - self.traffic_sample[1]) / delta)),
                               'upload_bps': max(0, int((sample[2] - self.traffic_sample[2]) / delta))}
                self.traffic_sample = sample
            except (ImportError, OSError, AttributeError):
                pass
            self.local_cache = {'checked': time.time(), 'clash': clash, 'proxy': proxy, 'port': port, 'traffic': traffic}
            return {**deepcopy(self.local_cache), 'exit': deepcopy(self.exit)}

    def refresh_exit(self):
        # Only this explicit operation sends an external request. GET/local polling never does.
        if not self.refresh_lock.acquire(blocking=False):
            from launcher_store import LauncherError
            raise LauncherError(409, 'refresh_busy', '出口地区正在刷新，请稍候')
        try:
            try:
                response, latency = request_text('https://www.cloudflare.com/cdn-cgi/trace', local_file_port())
                ip, region = parse_trace(response)
                if not re.fullmatch('[A-Z]{2}', region):
                    raise ValueError('missing region')
                result = {'state': 'info', 'region': region, 'ip': ip, 'latency_ms': latency, 'checked': time.time(),
                          'detail': '本机代理到 Cloudflare 的查询出口；不代表各应用出口或 Claude 检测通过'}
            except Exception:
                result = {'state': 'unknown', 'region': '', 'ip': '', 'latency_ms': None, 'checked': time.time(), 'detail': '出口未取得有效结果，请手动重试'}
            with self.lock:
                self.exit = result
            return deepcopy(result)
        finally:
            self.refresh_lock.release()

    def providers(self, force=False):
        """Use local Mihomo named-pipe API, never attempt remote account logins."""
        with self.provider_lock:
            if self.provider_cache and not force and time.time() - self.provider_cache['checked'] < 60:
                return deepcopy(self.provider_cache)
            from network import ROOT, python_exe
            try:
                process = subprocess.run(
                    [python_exe(), str(ROOT / 'controller_helper.py'), '--providers'],
                    capture_output=True, timeout=10, creationflags=HIDDEN)
                if process.returncode:
                    raise RuntimeError('代理提供者读取失败')
                data = __import__('json').loads(process.stdout.decode('utf-8'))
                if not data.get('ok') or not isinstance(data.get('data', {}).get('providers'), dict):
                    raise RuntimeError('无法从 Clash 获取订阅信息')
                result = {'providers': data['data']['providers'], 'checked': time.time(), 'source': 'mihomo', 'warning': ''}
            except (OSError, ValueError, TypeError, RuntimeError, subprocess.TimeoutExpired, UnicodeError, KeyError):
                result = {'providers': {}, 'checked': time.time(), 'source': 'unavailable',
                          'warning': '本次无法读取 Clash 提供者的套餐数据；可手动填写或稍后重试'}
            self.provider_cache = result
            return deepcopy(result)
