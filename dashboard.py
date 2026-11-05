"""Local desktop dashboard; public lookups, no exposed controller or credentials."""
import concurrent.futures
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from network import ROOT, settings, save_settings, run_checks, gate, request_text, native_claude, controller, validate_guard, classify_connections, local_file_port, geo_probe


def risk(ip, port):
    try:
        text, _ = request_text('https://ip.net.coffee/api/iprisk/' + ip, port)
        data = json.loads(text)
        scope = 'address'
        if data.get('ip') != ip:
            try:
                network = ipaddress.ip_network(data.get('cidr', ''), strict=True)
                applicable = network.version == 4 and network.prefixlen >= 24 and ipaddress.ip_address(ip) in network and ipaddress.ip_address(data.get('ip', '')) in network
            except ValueError:
                applicable = False
            if not applicable:
                return {'state': 'unknown', 'reason': '评分源返回其他 IP，且未提供适用网段', 'returned_ip': data.get('ip')}
            scope = 'network'
        score = data.get('trust_score')
        valid = type(score) in (int, float) and 0 <= score <= 100
        good = valid and score >= 80 and data.get('isResidential') is True and all(data.get(k) is False for k in ['is_datacenter', 'is_vpn', 'is_proxy', 'is_tor', 'is_crawler', 'is_abuser'])
        return {'state': 'pass' if good else 'fail' if valid else 'unknown', 'data': data, 'scope': scope,
                'reason': ('来源 ip.net.coffee · 网段画像 ' + data['cidr'] + ' · 样本 ' + data['ip']) if scope == 'network' else '来源 ip.net.coffee，仅代表该来源判断'}
    except Exception:
        return {'state': 'unknown', 'reason': '风险画像查询失败，未补默认分数'}


def domestic(port):
    try:
        import re
        text, ms = request_text('https://myip.ipip.net/', port)
        match = re.search(r'当前\s*IP[：:]\s*(\d+\.\d+\.\d+\.\d+)', text)
        ip = str(ipaddress.IPv4Address(match.group(1)))
        return {'state': 'info', 'ip': ip, 'detail': text.strip()[:200], 'ms': ms}
    except Exception:
        return {'state': 'unknown', 'detail': '国内参照查询未确认，不用普通代理出口替代'}


def dns_probe(port):
    token = secrets.token_hex(12)
    try:
        # Resolve fresh names through the system path; an HTTP image is not needed.
        import socket
        for i in (1, 2):
            try: socket.getaddrinfo(f'{token}-{i}.d.ip.net.coffee', 443)
            except OSError: pass
        time.sleep(1.2)
        return dns_result(token, port)
    except Exception:
        return {'state': 'unknown', 'reason': '系统 DNS 探针未完成', 'servers': []}


def dns_result(token, port):
    # The caller owns this run's token. Browser and system probes use distinct tokens.
    try:
        text, _ = request_text('https://ip.net.coffee/api/dns/result/' + token, port)
        servers = json.loads(text).get('dns_servers', [])
        if not servers: return {'state': 'unknown', 'reason': '探针未返回解析器，无法确认 DNS 出口', 'servers': []}
        resolved = []
        for server in servers:
            address = server if isinstance(server, str) else server.get('ip', '')
            ipaddress.ip_address(address)
            try:
                geo_text, _ = request_text('https://ip.net.coffee/api/geoip/' + address, port)
                geo = json.loads(geo_text)
            except Exception:
                geo = {}
            resolved.append({'ip': address, 'country_code': geo.get('country_code', '').upper(), 'isp': geo.get('isp', '')})
        leaked = any(s['country_code'] == 'CN' for s in resolved)
        known = all(s['country_code'] for s in resolved)
        return {'state': 'fail' if leaked else 'pass' if known else 'unknown', 'servers': resolved,
                'reason': '检测到中国大陆解析器，按网站口径提示可能泄露' if leaked else '本次探针未发现中国大陆解析器' if known else '解析器 IP 已取得，部分归属未确认'}
    except Exception:
        return {'state': 'unknown', 'reason': 'DNS 探针查询失败，未判定为通过', 'servers': []}


def browser_results(body):
    """Validate browser measurements without substituting a Python request."""
    values = {}
    for key in ('claude', 'general', 'domestic', 'anthropic'):
        raw = body.get(key) or {}
        value = {'state': 'unknown', 'reason': '浏览器未取得结果（网络、跨域或浏览器策略）'}
        if raw.get('ip'):
            address = ipaddress.ip_address(raw['ip'])
            if not address.is_global:
                raise ValueError('出口必须为公网地址')
            value = {'state': 'info', 'ip': str(address), 'country': str(raw.get('country', ''))[:8]}
            ms = raw.get('ms')
            if type(ms) in (int, float) and 0 <= ms <= 120000:
                value['ms'] = round(ms)
        elif key == 'anthropic' and raw.get('reachable') is True:
            value = {'state': 'info', 'reason': '浏览器请求已完成（不读取跨域 HTTP 状态）'}
        values[key] = value
    return values


def webrtc_result(body, browser_ip):
    ips = [str(ipaddress.ip_address(ip)) for ip in body.get('ips', [])[:30]]
    public = [ip for ip in ips if ipaddress.ip_address(ip).is_global]
    completed = body.get('completed') is True
    if public and browser_ip:
        mismatch = any(ip != browser_ip for ip in public)
        return {'state': 'fail' if mismatch else 'pass', 'ips': public,
                'reason': '公网 UDP 与浏览器 Claude 出口不同' if mismatch else '本次公网 UDP 与浏览器 Claude 出口一致'}
    return {'state': 'unknown', 'ips': public, 'reason':
            '浏览器 Claude 出口未确认，暂不判断是否一致' if public else
            '收集完成，未发现公网候选；不能证明客户端无泄露' if completed else
            '未取得公网候选，探针超时或受限'}


def availability(port):
    rows = []
    for url in ['https://www.anthropic.com/cdn-cgi/trace', 'https://status.claude.com/api/v2/status.json']:
        try:
            body, ms = request_text(url, port)
            if 'status.json' in url:
                data = json.loads(body).get('status', {})
                rows.append({'name': 'Claude 官方状态', 'value': data.get('description', '未提供'), 'state': 'pass' if data.get('indicator') == 'none' else 'fail'})
            else: rows.append({'name': 'anthropic.com', 'value': f'HTTP 200 · {ms} ms', 'state': 'pass'})
        except Exception: rows.append({'name': 'Claude 官方状态' if 'status.json' in url else 'anthropic.com', 'value': '未确认', 'state': 'unknown'})
    return rows


class Dashboard:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {'busy': False, 'report': None, 'extra': {}, 'run_id': '', 'expected': settings()['expected_ip']}
        self.last_seen = time.time()

    def start(self, expected):
        ipaddress.IPv4Address(expected)
        with self.lock:
            if self.state['busy']: return
            save_settings(expected)
            run_id = secrets.token_hex(8)
            self.state = {'busy': True, 'report': None, 'extra': {}, 'run_id': run_id, 'expected': expected, 'partial': {},
                          'browser_dns_token': secrets.token_hex(12), 'browser_started': False}
        def work():
            try:
                def emit(k, v):
                    with self.lock: self.state['partial'][k] = v
                report = run_checks(expected, emit)
                port = report.get('runtime', {}).get('mixed_port', 7897)
                with self.lock:
                    self.state['report'] = report
                jobs = {'availability': lambda: availability(port)}
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    futures = {pool.submit(fn): k for k, fn in jobs.items()}
                    for future in concurrent.futures.as_completed(futures):
                        with self.lock: self.state['extra'][futures[future]] = future.result()
                with self.lock: self.state['report'] = report
            except Exception as exc:
                with self.lock: self.state['error'] = str(exc)
            finally:
                with self.lock: self.state['busy'] = False
        threading.Thread(target=work, daemon=True).start()

    def record_browser(self, body):
        measured = browser_results(body)
        with self.lock:
            if body.get('run_id') != self.state['run_id'] or self.state.get('browser_started'):
                return False
            self.state['browser_started'] = True
            self.state['extra']['browser'] = measured
            run_id, token = self.state['run_id'], self.state['browser_dns_token']
            self.state['extra']['webrtc'] = webrtc_result(body.get('webrtc') or {}, measured['claude'].get('ip'))
        def work():
            port = local_file_port()
            ip = measured['claude'].get('ip')
            jobs = {'risk': lambda: risk(ip, port) if ip else {'state': 'unknown', 'reason': '浏览器 Claude 出口未确认'},
                    'browser_geo': lambda: geo_probe(ip, port) if ip else {},
                    'dns': lambda: dns_result(token, port)}
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                futures = {pool.submit(fn): key for key, fn in jobs.items()}
                for future in concurrent.futures.as_completed(futures):
                    try: value = future.result()
                    except Exception: value = {'state': 'unknown', 'reason': '查询未完成'}
                    with self.lock:
                        if self.state['run_id'] == run_id:
                            self.state['extra'][futures[future]] = value
        threading.Thread(target=work, daemon=True).start()
        return True

    def readiness(self):
        report, extra = self.state.get('report'), self.state['extra']
        checks = (report or {}).get('checks', {})
        age = time.time() - (report or {}).get('finished', 0)
        fresh = bool(report and report.get('expected_ip') == self.state['expected'] and 0 <= age <= 60)
        rows = [{'label': '代理请求出口一致', 'state': checks.get('claude', {}).get('state', 'unknown')},
                {'label': '系统请求出口一致', 'state': checks.get('system', {}).get('state', 'unknown')},
                {'label': '客户端规则覆盖', 'state': checks.get('guard', {}).get('state', 'unknown')},
                {'label': '原生客户端安装', 'state': 'pass' if (report or {}).get('native') else 'unknown'},
                {'label': '本次预检有效期（60 秒）', 'state': 'pass' if fresh else 'expired' if report else 'unknown'}]
        ready = not self.state['busy'] and all(r['state'] == 'pass' for r in rows)
        return rows, ready


def main():
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
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def send(self, status, data, content_type='application/json; charset=utf-8'):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status); self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def allowed(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}' and self.path.startswith('/' + token + '/')
        def do_GET(self):
            if not self.allowed(): return self.send(404, {})
            route = self.path.split('?', 1)[0].split('/')[-1]
            dashboard.last_seen = time.time()
            if route in ('', 'index.html', 'dashboard.js'):
                path = ROOT / ('dashboard.html' if route in ('', 'index.html') else 'dashboard.js')
                return self.send(200, path.read_bytes(), 'text/html; charset=utf-8' if path.suffix == '.html' else 'text/javascript; charset=utf-8')
            if route == 'state':
                with dashboard.lock:
                    rows, ready = dashboard.readiness()
                    state = json.loads(json.dumps(dashboard.state))
                    if state.get('report'): state['report'].pop('native', None)
                    state['required'], state['ready'] = rows, ready
                    state['asset_version'] = str((ROOT / 'dashboard.js').stat().st_mtime_ns) + ':' + str((ROOT / 'dashboard.html').stat().st_mtime_ns)
                return self.send(200, state)
            if route == 'connections':
                try: return self.send(200, {'connections': classify_connections(controller(connections=True))})
                except Exception: return self.send(200, {'error': '客户端连接观察暂不可用'})
            return self.send(404, {})
        def do_POST(self):
            if not self.allowed() or self.headers.get('Origin') != f'http://127.0.0.1:{self.server.server_port}': return self.send(403, {})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size > 12000: return self.send(413, {})
                body = json.loads(self.rfile.read(size))
                route = self.path.split('/')[-1]
                if route == 'check': dashboard.start(body['expected']); return self.send(200, {'ok': True})
                if route == 'browser':
                    ok = dashboard.record_browser(body)
                    return self.send(200 if ok else 409, {'ok': ok})
                if route == 'launch':
                    with dashboard.lock:
                        rows, ready = dashboard.readiness()
                    if not ready or validate_guard(controller())['state'] != 'pass': return self.send(409, {'error': '客户端预检未通过或结果已过期；本工具不会自动修复网络配置'})
                    with dashboard.lock:
                        if not dashboard.readiness()[1]:
                            return self.send(409, {'error': '预检结果已变化或过期，请重新检测'})
                    app = native_claude()
                    if not app: return self.send(409, {'error': '未发现原生 Claude 客户端，请先安装'})
                    if app['kind'] == 'aumid': os.startfile('shell:AppsFolder\\' + app['target'])
                    else: subprocess.Popen([app['target']])
                    return self.send(200, {'ok': True})
                return self.send(404, {})
            except Exception: return self.send(400, {'error': '请求未能完成，请重新检测'})
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    url = f'http://127.0.0.1:{server.server_port}/{token}/'
    (ROOT / 'reports').mkdir(exist_ok=True)
    (ROOT / 'reports' / 'desktop-session.json').write_text(json.dumps({'url': url, 'pid': os.getpid()}), encoding='utf-8')
    edge = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
    if not edge.exists(): raise RuntimeError('未找到 Microsoft Edge')
    # The page runs locally in its own desktop-style application window.
    if not resume and '--no-window' not in sys.argv:
        subprocess.Popen([str(edge), '--app=' + url, '--window-size=1220,950'])
    def watchdog():
        while time.time() - dashboard.last_seen < 120: time.sleep(5)
        server.shutdown()
    threading.Thread(target=watchdog, daemon=True).start()
    server.serve_forever(poll_interval=0.5)


if __name__ == '__main__': main()
