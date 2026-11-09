"""Same-origin API extension; old Claude routes and response contracts stay separate."""
import json
import subprocess
import threading
from urllib.parse import urlsplit

from launcher_store import LauncherError


class LauncherAPI:
    def __init__(self, store, service, token, unavailable=None, status=None):
        self.store, self.service = store, service
        self.prefix = '/' + token + '/api/'
        self.unavailable = unavailable
        from launcher_status import LauncherStatus
        self.status = status or LauncherStatus()
        self.scanner = self.workflows = self.backups = self.desktop = None
        self.features_lock = threading.RLock()
        self.icons_initialized = False

    def features(self):
        with self.features_lock:
            if self.scanner is None:
                from launcher_scanner import AppScanner
                from launcher_workflows import Workflows
                from launcher_backup import Backups
                self.scanner = AppScanner(self.store)
                self.workflows = Workflows(self.store, self.service, self.status)
                self.backups = Backups(self.store, self.workflows, self.scanner.icons)

    def claude_launched(self, run_id):
        # Called only by the existing guarded manual launch handler, never by an API request.
        try:
            item_id = 'claude-desktop'
            if self.workflows:
                with self.workflows.lock:
                    if self.workflows.job['state'] == 'waiting_claude':
                        item_id = next(s['id'] for s in self.workflows.job['steps'] if s['state'] == 'running')
                    self.workflows.claude_launched()
            if self.store:
                self.store.record_use(item_id)
        except Exception:
            pass  # Optional usage history must never change the original launch response.

    def is_route(self, path):
        return urlsplit(path).path.startswith(self.prefix)

    def authorized(self, handler):
        host = handler.headers.get('Host', '')
        permitted = {f'127.0.0.1:{handler.server.server_port}', f'localhost:{handler.server.server_port}'}
        if handler.client_address[0] not in ('127.0.0.1', '::1') or host not in permitted:
            return False
        origin = handler.headers.get('Origin')
        referer = handler.headers.get('Referer')
        expected = 'http://' + host
        # Origin is mandatory for mutations; read requests from fetch carry Referer.
        if origin is not None and origin != expected:
            return False
        if referer is not None:
            ref = urlsplit(referer)
            if ref.scheme + '://' + ref.netloc != expected or not ref.path.startswith(self.prefix[:-4]):
                return False
        if handler.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none'):
            return False
        return origin == expected if handler.command != 'GET' else origin == expected or referer is not None

    def dispatch(self, handler):
        try:
            if not self.authorized(handler):
                raise LauncherError(403, 'forbidden_origin', '仅允许从本机启动台页面访问')
            route = urlsplit(handler.path).path[len(self.prefix):]
            if self.unavailable and route not in ('status', 'status/refresh-exit', 'subscriptions/providers', 'desktop/status', 'desktop/wake', 'backup/preview', 'backup/restore'):
                raise LauncherError(503, 'storage_unavailable', '启动台配置不可用，请恢复配置；Claude 检查仍可使用')
            if handler.command == 'GET':
                if route == 'status':
                    return handler.send(200, self.status.local())
                if route == 'subscriptions/providers':
                    return handler.send(200, self.status.providers())
                if route == 'desktop/status':
                    return handler.send(200, self.desktop.snapshot() if self.desktop else {'tray': False, 'hotkey': False, 'wake_count': 0, 'message': ''})
                if route.startswith(('scanner/', 'icons/', 'workflows', 'backup/')):
                    self.features()
                    if route == 'scanner/apps':
                        return handler.send(200, self.scanner.scan())
                    if route.startswith('icons/'):
                        try: return handler.send(200, self.scanner.icons.read(route[6:]), 'image/png')
                        except FileNotFoundError: raise LauncherError(404, 'not_found', '图标不存在')
                    if route == 'workflows':
                        return handler.send(200, self.workflows.snapshot())
                    if route == 'backup/export':
                        return handler.send(200, self.backups.export(), 'application/zip')
                data = self.store.snapshot()
                if route == 'launcher/items':
                    with self.features_lock:
                        if not self.icons_initialized:
                            self.features()
                            for item in data['items']:
                                self.scanner.enrich(item)
                            self.icons_initialized = True
                            data = self.store.snapshot()
                    return handler.send(200, {'groups': data['groups'], 'items': data['items']})
                if route == 'subscriptions':
                    return handler.send(200, {'subscriptions': data['subscriptions']})
                raise LauncherError(404, 'not_found', '接口不存在')
            if handler.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                raise LauncherError(415, 'json_required', '请使用 JSON 请求')
            if handler.headers.get('Transfer-Encoding'):
                raise LauncherError(400, 'invalid_body', '不支持此请求传输格式')
            size = int(handler.headers.get('Content-Length', '0'))
            maximum = 28 * 1024 * 1024 if route == 'backup/preview' else 700 * 1024 if route == 'scanner/parse-shortcut' else 65536
            if not 0 < size <= maximum:
                raise LauncherError(413, 'body_too_large', '请求大小超出限制')
            def reject_constant(value):
                raise ValueError('Non-finite JSON number')
            body = json.loads(handler.rfile.read(size), parse_constant=reject_constant)
            if not isinstance(body, dict):
                raise LauncherError(400, 'invalid_body', '请求必须是 JSON 对象')
            if route == 'desktop/wake':
                self.exact(body, set())
                if not self.desktop:
                    raise LauncherError(503, 'desktop_unavailable', '桌面唤醒尚未启用')
                return handler.send(200, self.desktop.wake())
            if route.startswith(('scanner/', 'workflows/', 'backup/')):
                self.features()
                if route == 'scanner/parse-shortcut':
                    return handler.send(200, self.scanner.upload(body))
                if route == 'scanner/refresh':
                    self.exact(body, set()); return handler.send(200, self.scanner.scan(refresh=True))
                if route == 'scanner/pick-shortcuts':
                    self.exact(body, set()); return handler.send(200, self.scanner.pick_shortcuts())
                if route == 'scanner/pin':
                    return handler.send(200, self.scanner.pin(body))
                if route == 'workflows/save':
                    return handler.send(200, self.workflows.save(body))
                if route == 'workflows/start':
                    self.exact(body, {'id'}); return handler.send(200, self.workflows.start(body['id']))
                if route == 'workflows/delete':
                    self.exact(body, {'id'}); self.workflows.delete(body['id']); return handler.send(200, {'ok': True})
                if route == 'workflows/cancel':
                    self.exact(body, set()); self.workflows.cancel(); return handler.send(200, {'ok': True})
                if route == 'backup/preview':
                    return handler.send(200, self.backups.preview(body))
                if route == 'backup/restore':
                    result = self.backups.restore(body)
                    self.unavailable = None
                    self.icons_initialized = False
                    return handler.send(200, result)
            if route == 'launcher/open':
                return handler.send(200, self.service.open(body))
            if route in ('launcher/official', 'launcher/reveal'):
                return handler.send(200, self.service.item_action(route.split('/')[-1], body))
            if route == 'subscriptions/open':
                return handler.send(200, self.service.subscription_open(body))
            if route == 'launcher/pick-target':
                self.exact(body, {'launch_type'})
                if body['launch_type'] not in ('exe', 'folder'):
                    raise LauncherError(400, 'invalid_type', '仅支持选择程序或文件夹')
                return handler.send(200, self.service.adapter.pick_target(body['launch_type']))
            if route == 'status/refresh-exit':
                self.exact(body, set())
                return handler.send(200, self.status.refresh_exit())
            for base, collection in [('launcher/items', 'items'), ('subscriptions', 'subscriptions')]:
                if route == base + '/create':
                    item = self.store.save(collection, body, create=True)
                    if collection == 'items':
                        self.features(); item = self.scanner.enrich(item)
                    return handler.send(201, {'ok': True, 'item': item})
                if route == base + '/update':
                    item = self.store.save(collection, body)
                    if collection == 'items' and 'target_config' in body:
                        self.features(); item = self.scanner.enrich(item)
                    return handler.send(200, {'ok': True, 'item': item})
                if route == base + '/delete':
                    self.exact(body, {'id'})
                    self.store.delete(collection, body['id'])
                    return handler.send(200, {'ok': True})
                if route == base + '/reorder':
                    self.exact(body, {'ids'})
                    self.store.reorder(collection, body['ids'])
                    return handler.send(200, {'ok': True})
            raise LauncherError(404, 'not_found', '接口不存在')
        except LauncherError as exc:
            return handler.send(exc.status, exc.payload)
        except (ValueError, TypeError, KeyError, RecursionError):
            return handler.send(400, {'code': 'invalid_body', 'error': '请求格式不正确'})
        except subprocess.SubprocessError:
            return handler.send(503, {'code': 'action_timeout', 'error': '操作超时，请关闭未完成的本机选择窗口后重试'})
        except OSError:
            return handler.send(503, {'code': 'storage_unavailable', 'error': '无法保存启动台配置，请检查磁盘或文件权限'})

    @staticmethod
    def exact(body, fields):
        if set(body) != fields:
            raise LauncherError(400, 'invalid_body', '请求字段不正确')
