"""Bounded by the caller's subprocess timeout. Never expose configuration secrets."""
import http.client
import json
import os
from pathlib import Path

import yaml

BASE = Path(os.environ['APPDATA']) / 'io.github.clash-verge-rev.clash-verge-rev'


def load_config():
    return yaml.safe_load((BASE / 'clash-verge.yaml').read_text(encoding='utf-8'))


def request(method, route, body=None):
    config = load_config()
    names = os.listdir('\\\\.\\pipe\\')
    candidates = [n for n in names if n.startswith('verge-mihomo-production-')]
    configured = config.get('external-controller-pipe', '').split('\\')[-1]
    if configured in names:
        candidates.insert(0, configured)
    if not candidates:
        raise RuntimeError('代理控制通道未运行')
    if len(set(candidates)) > 1:
        raise RuntimeError('发现多个代理控制通道，请关闭多余实例后重试')
    pipe = open('\\\\.\\pipe\\' + candidates[0], 'r+b', buffering=0)
    payload = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else b''
    headers = (f'{method} {route} HTTP/1.1\r\nHost: localhost\r\n'
               f'Authorization: Bearer {config.get("secret", "")}\r\n'
               'Connection: close\r\nContent-Type: application/json\r\n'
               f'Content-Length: {len(payload)}\r\n\r\n').encode()

    class PipeSocket:
        def makefile(self, *args, **kwargs):
            return pipe

    try:
        pipe.write(headers + payload)
        response = http.client.HTTPResponse(PipeSocket())
        response.begin()
        data = response.read(8_000_000)
        if response.status >= 400:
            raise RuntimeError(f'代理控制接口返回 HTTP {response.status}')
        return json.loads(data) if data else {}
    finally:
        pipe.close()


def snapshot():
    cfg = request('GET', '/configs')
    return {
        'config': {k: cfg.get(k) for k in ('mode', 'mixed-port', 'find-process-mode', 'ipv6', 'tun')},
        'rules': request('GET', '/rules').get('rules', []),
        'proxies': request('GET', '/proxies').get('proxies', {}),
    }


if __name__ == '__main__':
    import sys
    try:
        result = request('GET', '/connections') if '--connections' in sys.argv else snapshot()
        print(json.dumps({'ok': True, 'data': result}, ensure_ascii=True))
    except Exception as exc:
        message = '没有读取代理状态的权限' if isinstance(exc, PermissionError) else str(exc)
        print(json.dumps({'ok': False, 'error': message}, ensure_ascii=True))
