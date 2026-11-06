"""Launcher contract/security tests. Every OS launch is mocked; configs live in tmp_path."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
import http.client
import json
from pathlib import Path
import threading
import uuid
from unittest.mock import Mock, patch

import pytest

from dashboard import Dashboard, make_handler
from http.server import ThreadingHTTPServer
from launcher_api import LauncherAPI
from launcher_service import LauncherService, WindowsLauncher, split_arguments
from launcher_store import (LauncherError, LauncherStore, defaults, make_item,
                            subscription_view, validate_item)


@pytest.fixture
def tmp_path():
    # Keep isolated evidence under reports; this host rejects pytest's private-mode temp directories.
    directory = Path(__file__).resolve().parent / 'reports' / 'launcher-tests' / uuid.uuid4().hex
    directory.mkdir(parents=True)
    return directory


@pytest.fixture
def store(tmp_path):
    return LauncherStore(tmp_path / 'launcher.json')


def test_defaults_and_dynamic_dates(store):
    data = store.snapshot()
    assert len(data['items']) == 17
    assert len(data['groups']) == 7
    assert len(data['subscriptions']) == 2
    assert all(s['remaining_days'] is None and s['expiry_status'] == 'unfilled' for s in data['subscriptions'])
    sub = data['subscriptions'][0]
    for expiry, days, status in [('2026-11-05', -1, 'expired'), ('2026-11-06', 0, 'due_today'),
                                 ('2026-11-09', 3, 'urgent'), ('2026-11-13', 7, 'warning'),
                                 ('2026-11-14', 8, 'normal')]:
        result = subscription_view({**sub, 'expire_date': expiry}, today=date(2026, 11, 6))
        assert (result['remaining_days'], result['expiry_status']) == (days, status)


def test_subscription_crud_validation_and_restart(store):
    store.save('subscriptions', {'id': 'static-ip', 'expire_date': '2026-12-06', 'ip_info': 'example:123'})
    store.save('subscriptions', {'id': 'secondary', 'name': '备用 VPN'}, create=True)
    order = ['secondary', 'vpn', 'static-ip']
    store.reorder('subscriptions', order)
    restarted = LauncherStore(store.path, discover=Mock(side_effect=AssertionError('must not rediscover')))
    assert [s['id'] for s in restarted.snapshot()['subscriptions']] == order
    for payload in [{'expire_date': '2026-02-30'}, {'remaining_days': -1},
                    {'renew_url': 'javascript:alert(1)'}, {'expire_date': '20261106'}]:
        with pytest.raises(LauncherError):
            store.save('subscriptions', {'id': 'static-ip', **payload})
    store.delete('subscriptions', 'secondary')
    assert len(store.snapshot()['subscriptions']) == 2


def test_item_crud_and_delete_never_removes_file(store, tmp_path):
    target = tmp_path / 'demo.exe'
    target.write_bytes(b'not a real executable')
    item = make_item('demo', 'Demo', 'daily_apps', 'exe', str(target))
    item.pop('last_used'); item.pop('use_count')
    store.save('items', item, create=True)
    store.save('items', {'id': 'demo', 'pinned': True, 'target_config': {'args': '--name "hello world"'}})
    row = store.get('items', 'demo')
    assert row['pinned'] and row['target_config']['args'] == '--name "hello world"'
    ids = [r['id'] for r in store.snapshot()['items']][::-1]
    store.reorder('items', ids)
    assert [r['id'] for r in store.snapshot()['items']] == ids
    with pytest.raises(LauncherError):
        store.reorder('items', ids[:-1])
    store.delete('items', 'demo')
    assert target.exists()


def test_concurrent_counters_are_not_lost(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: store.record_use('gemini'), range(24)))
    row = store.get('items', 'gemini')
    assert row['use_count'] == 24 and row['last_used'] > 0
    assert not list(store.path.parent.glob('*.tmp'))


def test_corrupt_config_is_not_replaced_and_atomic_write_failure(store):
    original = store.path.read_bytes()
    with patch('launcher_store.os.replace', side_effect=OSError('disk failure')):
        with pytest.raises(OSError):
            store.save('items', {'id': 'gemini', 'name': 'changed'})
    assert store.path.read_bytes() == original
    store.path.write_text('{broken', encoding='utf-8')
    with pytest.raises(LauncherError, match='原文件未覆盖'):
        LauncherStore(store.path)
    assert store.path.read_text(encoding='utf-8') == '{broken'


@pytest.mark.parametrize('kind,target', [('web', 'file:///C:/test.exe'), ('web', 'javascript:alert(1)'),
    ('web', 'https://example.com/\n--bad'), ('exe', 'C:/test.cmd'), ('exe', 'relative.exe'),
    ('exe', '//server/share/test.exe'), ('exe', 'C:/test.exe:evil.exe'),
    ('exe', 'C:/demo/../test.exe'), ('uwp', 'Package!App & calc.exe')])
def test_invalid_targets_are_rejected(kind, target):
    with pytest.raises(LauncherError):
        validate_item(make_item('bad', 'Bad', 'projects', kind, target))


def test_no_browser_singleton_and_protected_claude_cannot_be_downgraded(store):
    item = make_item('browser', 'Browser', 'projects', 'exe', 'C:/Apps/chrome.exe')
    item['target_config']['singleton_process'] = 'chrome.exe'
    with pytest.raises(LauncherError): validate_item(item)
    for kind, target in [('exe', 'C:/Apps/Claude.exe'), ('uwp', 'Anthropic.Claude_123!App')]:
        with pytest.raises(LauncherError): validate_item(make_item('copy', 'Claude', 'projects', kind, target))
    with pytest.raises(LauncherError): store.save('items', {'id': 'claude-desktop', 'launch_type': 'exe'})


def test_open_only_id_and_no_statistics_for_failure_or_claude_panel(store):
    adapter = Mock()
    adapter.launch.return_value = {'status': 'opened'}
    service = LauncherService(store, adapter)
    for body in [{'id': 'gemini', 'path': 'C:/evil.exe'}, {'target': 'C:/evil.exe'}, [], {'id': 'missing'}]:
        with pytest.raises(LauncherError): service.open(body)
    assert service.open({'id': 'claude-desktop'})['action'] == 'open_claude_drawer'
    adapter.launch.assert_not_called()
    assert store.get('items', 'claude-desktop')['use_count'] == 0
    service.open({'id': 'gemini'})
    assert store.get('items', 'gemini')['use_count'] == 1
    adapter.launch.side_effect = OSError('cannot launch')
    with pytest.raises(LauncherError): service.open({'id': 'gemini'})
    assert store.get('items', 'gemini')['use_count'] == 1


def test_windows_native_argument_parser_keeps_quotes_and_literal_metacharacters():
    assert split_arguments('--name "hello world" --path "C:\\My Files" & echo $HOME') == [
        '--name', 'hello world', '--path', 'C:\\My Files', '&', 'echo', '$HOME']


def test_web_profile_launch_never_uses_browser_singleton():
    adapter = WindowsLauncher()
    item = make_item('site', 'Site', 'projects', 'web', 'https://example.com/?a=1&b=2')
    item['target_config'].update(browser='msedge', profile='Profile 1')
    with patch.object(adapter, 'browser_path', return_value='C:/Edge/msedge.exe'), \
            patch('launcher_service.subprocess.Popen') as launch, patch.object(adapter, 'running_pids') as running:
        adapter.launch(item)
    assert launch.call_args.args[0] == ['C:/Edge/msedge.exe', '--profile-directory=Profile 1',
                                       '--new-tab', 'https://example.com/?a=1&b=2']
    assert launch.call_args.kwargs['shell'] is False
    running.assert_not_called()


def test_exe_singleton_and_missing_target(store, tmp_path):
    adapter = WindowsLauncher()
    target = tmp_path / 'demo.exe'; target.write_bytes(b'fake')
    item = make_item('demo', 'Demo', 'projects', 'exe', str(target))
    item['target_config'].update(args='--name "hello world"', working_dir=str(tmp_path), singleton_process='demo.exe')
    with patch.object(adapter, 'running_pids', return_value=[123]), patch.object(adapter, 'activate', return_value=False), \
            patch('launcher_service.subprocess.Popen') as launch:
        assert adapter.launch(item)['status'] == 'already_running'
        launch.assert_not_called()
    with patch.object(adapter, 'running_pids', return_value=[]), patch('launcher_service.subprocess.Popen') as launch:
        adapter.launch(item)
        assert launch.call_args.args[0] == [str(target), '--name', 'hello world']
        assert launch.call_args.kwargs == {'cwd': str(tmp_path), 'shell': False}
    target.unlink()
    with pytest.raises(LauncherError) as error: adapter.launch(item)
    assert error.value.payload['code'] == 'target_missing'


def test_folder_uwp_and_default_browser_use_os_adapters(tmp_path):
    adapter = WindowsLauncher()
    with patch('launcher_service.os.startfile') as start:
        adapter.launch(make_item('folder', 'Folder', 'projects', 'folder', str(tmp_path)))
        start.assert_called_once_with(str(tmp_path))
    with patch('launcher_service.subprocess.Popen') as launch:
        adapter.launch(make_item('store', 'Store app', 'projects', 'uwp', 'Vendor.App_123!App'))
        assert launch.call_args.args[0][-1] == 'shell:AppsFolder\\Vendor.App_123!App'
    with patch('launcher_service.os.startfile') as start:
        adapter.launch(make_item('site', 'Site', 'projects', 'web', 'https://example.com/'))
        start.assert_called_once_with('https://example.com/')


@contextmanager
def live_server(store, unavailable=False):
    adapter = Mock(); adapter.launch.return_value = {'status': 'opened'}
    api = LauncherAPI(store, LauncherService(store, adapter), 'test-session', unavailable=unavailable)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(Dashboard(), 'test-session', api))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield server, adapter
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def request(server, route, method='GET', body=None, host='127.0.0.1', origin=True, referer=True,
            extra=None, token='test-session'):
    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    base = f'http://{host}:{server.server_port}'
    headers = {'Host': base.removeprefix('http://'), 'Content-Type': 'application/json'}
    if origin: headers['Origin'] = base if origin is True else origin
    if referer: headers['Referer'] = base + '/' + token + '/' if referer is True else referer
    headers.update(extra or {})
    conn.request(method, '/' + token + '/' + route, json.dumps(body) if body is not None else None, headers)
    response = conn.getresponse(); raw = response.read(); status = response.status; conn.close()
    return status, raw


def test_real_http_origin_host_id_and_old_routes(store):
    with live_server(store) as (server, adapter):
        for options in [{'origin': 'https://evil.example'}, {'host': 'evil.example'},
                        {'referer': 'https://evil.example/'}, {'origin': False, 'referer': False},
                        {'origin': False}, {'extra': {'Sec-Fetch-Site': 'cross-site'}}]:
            status, _ = request(server, 'api/launcher/open', 'POST', {'id': 'gemini'}, **options)
            assert status == 403
        assert request(server, 'api/launcher/open', 'POST', {'id': 'gemini', 'path': 'C:/evil.exe'})[0] == 400
        assert request(server, 'api/launcher/open', 'POST', {'id': 'gemini'}, token='wrong-token')[0] == 403
        adapter.launch.assert_not_called()
        assert request(server, 'api/launcher/items', origin=False)[0] == 200
        assert request(server, 'api/launcher/items', origin=False, referer=False)[0] == 403
        assert request(server, 'api/launcher/items', host='localhost')[0] == 200
        assert request(server, 'api/launcher/open', 'POST', {'id': 'claude-desktop'})[0] == 200
        adapter.launch.assert_not_called()
        assert request(server, 'api/launcher/open', 'POST', {'id': 'gemini'})[0] == 200
        assert adapter.launch.call_count == 1
        status, raw = request(server, 'state', origin=False, referer=False)
        state = json.loads(raw)
        assert status == 200 and state['report'] is None and not state['ready']
        assert {'required', 'run_id', 'expected', 'monitor', 'dns_audit_job', 'asset_version'} <= state.keys()
        assert request(server, 'launch', 'POST', {'run_id': 'fake', 'expected': state['expected']})[0] == 409
        status, raw = request(server, 'index.html', origin=False, referer=False)
        assert status == 200 and '开始环境检查' in raw.decode('utf-8')
        assert request(server, 'connections', origin=False, referer=False)[0] == 200
        # Unknown API paths must not accidentally dispatch to the old /launch handler.
        assert request(server, 'api/launcher/launch', 'POST', {})[0] == 404


def test_http_crud_both_collections_and_content_type(store):
    with live_server(store) as (server, adapter):
        for base, collection, name in [('launcher/items', 'items', '快捷方式'), ('subscriptions', 'subscriptions', '订阅')]:
            assert request(server, 'api/' + base + '/create', 'POST', {'id': 'new', 'name': name})[0] == 201
            assert request(server, 'api/' + base + '/create', 'POST', {'id': 'new', 'name': name})[0] == 409
            assert request(server, 'api/' + base + '/update', 'POST', {'id': 'new', 'name': '改名'})[0] == 200
            ids = [r['id'] for r in store.snapshot()[collection]][::-1]
            assert request(server, 'api/' + base + '/reorder', 'POST', {'ids': ids})[0] == 200
            assert request(server, 'api/' + base + '/delete', 'POST', {'id': 'new'})[0] == 200
            assert request(server, 'api/' + base + '/delete', 'POST', {'id': 'new'})[0] == 404
        assert request(server, 'api/launcher/open', 'POST', {'id': 'gemini'}, extra={'Content-Type': 'text/plain'})[0] == 415
        assert request(server, 'api/launcher/open', 'POST', {'id': 'gemini', 'junk': 'x' * 65536})[0] == 413
        adapter.launch.assert_not_called()


def test_broken_launcher_storage_does_not_break_original_claude_page(store):
    with live_server(store, unavailable=True) as (server, _):
        assert request(server, 'api/launcher/items')[0] == 503
        assert request(server, 'index.html')[0] == 200
        assert request(server, 'state')[0] == 200


def test_original_quit_still_shuts_down_the_bound_server(store):
    with live_server(store) as (server, _):
        assert request(server, 'quit', 'POST', {})[0] == 200
        assert server._BaseServer__is_shut_down.wait(3)


def test_history_save_error_does_not_report_a_successful_launch_as_failed(store):
    adapter = Mock(); adapter.launch.return_value = {'status': 'opened'}
    with patch.object(store, 'record_use', side_effect=OSError('disk full')):
        result = LauncherService(store, adapter).open({'id': 'gemini'})
    assert result['ok'] and result['history_saved'] is False
    adapter.launch.assert_called_once()


def test_discovery_does_not_launch_anything_and_keeps_empty_fallbacks():
    adapter = WindowsLauncher()
    with patch('launcher_service.subprocess.run', return_value=Mock(stdout=b'[]', returncode=0)), \
            patch('launcher_service.subprocess.Popen') as launch, patch('launcher_service.os.startfile') as start:
        found = adapter.discover_defaults()
    launch.assert_not_called(); start.assert_not_called()
    assert all(Path(row['target']).is_file() for row in found.values() if row['launch_type'] == 'exe')
    assert defaults()['items'][3]['target_config']['target'] == ''


def test_launcher_status_local_poll_never_calls_external_api():
    from launcher_status import LauncherStatus
    status = LauncherStatus()
    with patch('launcher_status.local_file_port', return_value=7897), \
            patch('launcher_status.socket.create_connection', side_effect=OSError), \
            patch('launcher_status.subprocess.run', return_value=Mock(returncode=0, stdout=b'"mihomo.exe","123"')), \
            patch('launcher_status.request_text', return_value=('ip=203.0.113.1\nloc=US', 1)) as external:
        for _ in range(3):
            data = status.local()
            assert data['proxy'] == 'disconnected' and data['clash'] == 'running'
            assert data['exit']['state'] == 'unknown'
        external.assert_not_called()
        assert status.refresh_exit()['region'] == 'US'
        status.local(); status.local()
        external.assert_called_once()
        assert 'ready' not in status.local()
        external.side_effect = OSError('rate limit')
        assert status.refresh_exit()['state'] == 'unknown'
        assert status.local()['exit']['region'] == ''


def test_id_only_secondary_actions_and_subscription_links(store):
    adapter = Mock(); service = LauncherService(store, adapter)
    with pytest.raises(LauncherError):
        service.item_action('official', {'id': 'steam', 'url': 'https://evil.example'})
    service.item_action('official', {'id': 'steam'})
    assert adapter.open_web.call_args.args[0]['target'] == 'https://store.steampowered.com/about/'
    with pytest.raises(LauncherError):
        service.subscription_open({'id': 'static-ip', 'link': 'renew_url'})
    store.save('subscriptions', {'id': 'static-ip', 'renew_url': 'https://example.com/renew'})
    service.subscription_open({'id': 'static-ip', 'link': 'renew_url'})
    assert adapter.open_web.call_args.args[0]['target'] == 'https://example.com/renew'
    with pytest.raises(LauncherError):
        service.subscription_open({'id': 'static-ip', 'link': 'https://evil.example'})


def test_picker_and_secondary_actions_enforce_origin_and_fields(store):
    with live_server(store) as (server, adapter):
        adapter.pick_target.return_value = {'cancelled': True, 'target': ''}
        assert request(server, 'api/launcher/pick-target', 'POST', {'launch_type': 'exe'}, origin='https://evil.example')[0] == 403
        adapter.pick_target.assert_not_called()
        assert request(server, 'api/launcher/pick-target', 'POST', {'launch_type': 'exe', 'path': 'C:/evil.exe'})[0] == 400
        assert request(server, 'api/launcher/pick-target', 'POST', {'launch_type': 'claude_protected'})[0] == 400
        assert request(server, 'api/launcher/pick-target', 'POST', {'launch_type': 'exe'})[0] == 200
        assert request(server, 'api/launcher/official', 'POST', {'id': 'steam'})[0] == 200
        assert request(server, 'api/launcher/reveal', 'POST', {'id': 'steam'})[0] == 200
        assert request(server, 'api/subscriptions/open', 'POST', {'id': 'static-ip', 'link': 'renew_url'})[0] == 409
        for asset in ('workbench.js', 'workbench.css'):
            response, raw = request(server, asset)
            assert response == 200 and len(raw) > 100


def test_window_enumeration_failure_never_duplicates_a_running_client():
    with patch('win32gui.EnumWindows', side_effect=RuntimeError('window access denied')):
        assert WindowsLauncher().activate([123]) is False
