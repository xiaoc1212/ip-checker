"""Local desktop dashboard; public lookups, no exposed controller or credentials."""
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from network import ROOT, settings, save_settings, run_checks, native_claude, controller, validate_guard, classify_connections, startup_rows
from dns_audit import read_saved_evidence, run_capture, DIRECTORY as DNS_DIRECTORY


class Dashboard:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {'busy': False, 'report': None, 'extra': {}, 'run_id': '', 'expected': settings()['expected_ip']}
        self.monitor = {'state': 'unknown', 'detail': '持续观察尚未完成', 'connections': []}
        self.dns_job = {'busy': False, 'detail': '可独立执行 DNS 专项采样'}

    def start_dns_audit(self):
        with self.lock:
            if self.dns_job['busy'] or self.state['busy']:
                return False
            self.dns_job = {'busy': True, 'detail': '等待 Windows 管理员确认，之后进行 40 秒短时采样'}
        def work():
            try:
                output = DNS_DIRECTORY / ('capture-' + secrets.token_hex(8) + '.json')
                evidence = run_capture(output)
                detail = evidence['capture_verdict']['value']
            except Exception as exc:
                detail = str(exc) if isinstance(exc, RuntimeError) else '采样超时或失败，证据未确认'
            with self.lock:
                self.dns_job = {'busy': False, 'detail': detail}
        threading.Thread(target=work, daemon=True).start()
        return True

    def start(self, expected):
        ipaddress.IPv4Address(expected)
        with self.lock:
            if self.state['busy'] or self.dns_job['busy']: return
            save_settings(expected)
            run_id = secrets.token_hex(8)
            self.state = {'busy': True, 'report': None, 'extra': {}, 'run_id': run_id, 'expected': expected, 'partial': {}, 'started': time.time()}
        def work():
            try:
                def emit(k, v):
                    with self.lock: self.state['partial'][k] = v
                report = run_checks(expected, emit)
                with self.lock:
                    self.state['report'] = report
            except Exception as exc:
                with self.lock: self.state['error'] = type(exc).__name__ + '：检测未完成'
            finally:
                with self.lock: self.state['busy'] = False
        threading.Thread(target=work, daemon=True).start()
        return run_id

    def readiness(self):
        rows = startup_rows(self.state.get('report'), self.state['expected'])
        return rows, not self.state['busy'] and not self.dns_job['busy'] and all(row['state'] == 'pass' for row in rows)

    def monitor_once(self):
        try:
            guard = validate_guard(controller())
            connections = classify_connections(controller(connections=True))
            wrong = any(c['state'] == 'fail' for c in connections)
            monitor = {'checked': time.time(), 'state': 'fail' if wrong else guard['state'],
                       'detail': '发现客户端连接未命中住宅组；仅告警，没有强制断网' if wrong else guard['detail'],
                       'connections': connections}
        except Exception:
            monitor = {'checked': time.time(), 'state': 'unknown', 'detail': '无法读取代理运行状态，保护状态未确认；请检查代理是否仍在运行', 'connections': []}
        with self.lock:
            self.monitor = monitor



def make_handler(dashboard, token, launcher_api):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def send(self, status, data, content_type='application/json; charset=utf-8'):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
            try:
                self.send_response(status); self.send_header('Content-Type', content_type)
                self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass
        def allowed(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}' and self.path.startswith('/' + token + '/')
        def do_GET(self):
            if launcher_api.is_route(self.path): return launcher_api.dispatch(self)
            if not self.allowed(): return self.send(404, {})
            route = self.path.split('?', 1)[0].split('/')[-1]
            if route in ('', 'index.html', 'dashboard.js', 'dashboard.css', 'workbench.js', 'workbench.css', 'workbench-extras.js'):
                path = ROOT / ('dashboard.html' if route in ('', 'index.html') else route)
                return self.send(200, path.read_bytes(), {'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8'}.get(path.suffix, 'text/javascript; charset=utf-8'))
            if route == 'state':
                with dashboard.lock:
                    rows, ready = dashboard.readiness()
                    state = json.loads(json.dumps(dashboard.state))
                    if state.get('report'): state['report'].pop('native', None)
                    state['required'], state['ready'] = rows, ready
                    state['monitor'] = dashboard.monitor
                    state['dns_audit'] = read_saved_evidence()
                    state['dns_audit_job'] = dict(dashboard.dns_job)
                    state['asset_version'] = ':'.join(str((ROOT / name).stat().st_mtime_ns) for name in ('dashboard.js', 'dashboard.html', 'dashboard.css', 'workbench.js', 'workbench.css', 'workbench-extras.js'))
                return self.send(200, state)
            if route == 'connections':
                with dashboard.lock:
                    monitor = json.loads(json.dumps(dashboard.monitor))
                return self.send(200, monitor)
            return self.send(404, {})
        def do_POST(self):
            if launcher_api.is_route(self.path): return launcher_api.dispatch(self)
            if not self.allowed() or self.headers.get('Origin') != f'http://127.0.0.1:{self.server.server_port}': return self.send(403, {})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 12000: return self.send(413, {})
                body = json.loads(self.rfile.read(size))
                route = self.path.split('/')[-1]
                if route == 'check':
                    if dashboard.dns_job['busy']:
                        return self.send(409, {'error': 'DNS 采样期间暂停检测器自身探针，避免混入样本'})
                    run_id = dashboard.start(body['expected'])
                    if not run_id:
                        return self.send(409, {'error': '另一轮检测已在执行，请等待完成'})
                    return self.send(200, {'ok': True, 'run_id': run_id})
                if route == 'dns-audit':
                    ok = dashboard.start_dns_audit()
                    return self.send(200 if ok else 409, {'ok': ok, 'error': '' if ok else '当前已有检测或采样正在执行'})
                if route == 'quit':
                    if dashboard.dns_job['busy']:
                        return self.send(409, {'error': 'DNS 采样正在运行，请等待自动结束后退出'})
                    self.send(200, {'ok': True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if route == 'launch':
                    with dashboard.lock:
                        rows, ready = dashboard.readiness()
                        same_run = body.get('run_id') == dashboard.state['run_id'] and body.get('expected') == dashboard.state['expected']
                        if dashboard.dns_job['busy']:
                            return self.send(409, {'error': '请等待 DNS 采样完成，再进行启动前检测'})
                    if not same_run or not ready or validate_guard(controller())['state'] != 'pass': return self.send(409, {'error': '客户端预检未通过或结果已过期；本工具不会自动修复网络配置'})
                    with dashboard.lock:
                        if not dashboard.readiness()[1]:
                            return self.send(409, {'error': '预检结果已变化或过期，请重新检测'})
                    observed = classify_connections(controller(connections=True))
                    if any(c['state'] == 'fail' for c in observed):
                        return self.send(409, {'error': '已观察到客户端连接路径异常，已停止启动'})
                    app = native_claude()
                    if not app: return self.send(409, {'error': '未发现原生 Claude 客户端，请先安装'})
                    with dashboard.lock:
                        if not dashboard.readiness()[1] or body.get('run_id') != dashboard.state['run_id'] or body.get('expected') != dashboard.state['expected']:
                            return self.send(409, {'error': '启动前结果已改变，请重新检测'})
                    if app['kind'] == 'aumid': os.startfile('shell:AppsFolder\\' + app['target'])
                    else: subprocess.Popen([app['target']])
                    launcher_api.claude_launched(body['run_id'])
                    return self.send(200, {'ok': True})
                return self.send(404, {})
            except Exception: return self.send(400, {'error': '请求未能完成，请重新检测'})
    return Handler


def main():
    from launcher_desktop import acquire_instance, Desktop
    instance = acquire_instance(ROOT)
    if instance is None:
        return
    dashboard = Dashboard()
    token = secrets.token_urlsafe(24)
    import sys
    resume = '--resume' in sys.argv
    port = 0
    if resume:
        from urllib.parse import urlsplit
        previous = json.loads((ROOT / 'reports' / 'desktop-session.json').read_text())
        parsed = urlsplit(previous['url'])
        if parsed.hostname != '127.0.0.1' or not parsed.port:
            raise RuntimeError('本机窗口会话地址无效')
        port, token = parsed.port, parsed.path.strip('/')
    from launcher_store import LauncherStore, LauncherError
    from launcher_service import LauncherService, WindowsLauncher
    from launcher_api import LauncherAPI
    adapter = WindowsLauncher()
    try:
        store = LauncherStore(ROOT / 'launcher-data.json', discover=adapter.discover_defaults)
        launcher_api = LauncherAPI(store, LauncherService(store, adapter), token)
    except (LauncherError, OSError):
        # Optional launcher data must never prevent the existing Claude UI from opening.
        store = LauncherStore.for_recovery(ROOT / 'launcher-data.json')
        launcher_api = LauncherAPI(store, LauncherService(store, adapter), token, unavailable=True)
    server = ThreadingHTTPServer(('127.0.0.1', port), make_handler(dashboard, token, launcher_api))
    url = f'http://127.0.0.1:{server.server_port}/{token}/'
    (ROOT / 'reports').mkdir(exist_ok=True)
    (ROOT / 'reports' / 'desktop-session.json').write_text(json.dumps({'url': url, 'pid': os.getpid()}), encoding='utf-8')
    def exit_desktop():
        if dashboard.dns_job['busy']:
            launcher_api.desktop.info['message'] = 'DNS 采样正在进行，请完成后再退出'
            launcher_api.desktop.wake_async()
            return
        server.shutdown()
    launcher_api.desktop = Desktop(url, exit_desktop)
    launcher_api.desktop.start()
    edge = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
    if not edge.exists(): raise RuntimeError('未找到 Microsoft Edge')
    # The page runs locally in its own desktop-style application window.
    if not resume and '--no-window' not in sys.argv:
        subprocess.Popen([str(edge), '--app=' + url, '--start-maximized'])
    def monitor_loop():
        while True:
            dashboard.monitor_once()
            time.sleep(8)
    threading.Thread(target=monitor_loop, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        if launcher_api.workflows:
            launcher_api.workflows.cancel()
        launcher_api.desktop.stop()
        server.server_close()
        import win32api
        win32api.CloseHandle(instance)


if __name__ == '__main__': main()
