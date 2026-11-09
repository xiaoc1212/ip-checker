"""Software import, workflow gates, archive validation and native icon tests."""
import base64
from contextlib import contextmanager
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import time
import uuid
from unittest.mock import Mock, patch
import zipfile

import pytest

from launcher_store import LauncherStore, LauncherError, make_item, validate_item
from launcher_scanner import AppScanner, MAX_SHORTCUT, target_key
from launcher_service import LauncherService, WindowsLauncher
from launcher_icons import IconCache
from launcher_workflows import Workflows
from launcher_backup import Backups
from test_launcher import live_server, request


@pytest.fixture
def root():
    directory=Path(__file__).parent/'reports'/'feature-tests'/uuid.uuid4().hex
    directory.mkdir(parents=True)
    return directory


@pytest.fixture
def store(root):
    return LauncherStore(root/'launcher.json')


def encoded(raw):
    return base64.b64encode(raw).decode('ascii')


def test_url_and_steam_upload_staging_duplicate_confirmation(store):
    scanner=AppScanner(store)
    raw=b'[InternetShortcut]\nURL=https://gemini.google.com/\n'
    candidate=scanner.upload({'name':'Gemini.url','data':encoded(raw)})
    assert candidate['name']=='Gemini' and candidate['duplicate_ids']==['gemini']
    body={'ids':[candidate['id']],'group':'google_ai','pinned':True,'allow_duplicate':False}
    with pytest.raises(LauncherError) as error: scanner.pin(body)
    assert error.value.payload['code']=='duplicate_target'
    assert scanner.pin({**body,'allow_duplicate':True})['count']==1
    assert len(store.snapshot()['items'])==44
    assert not list((store.path.parent/'reports'/'shortcut-imports').iterdir())
    steam=scanner.upload({'name':'Game.url','data':encoded(b'[InternetShortcut]\nURL=steam://rungameid/570\n')})
    scanner.pin({**body,'ids':[steam['id']],'group':'daily_apps','allow_duplicate':False})
    item=store.snapshot()['items'][-1]
    assert item['launch_type']=='web' and item['target_config']['target']=='steam://rungameid/570'
    with patch('launcher_service.os.startfile') as start:
        WindowsLauncher().launch(item)
        start.assert_called_once_with('steam://rungameid/570')


@pytest.mark.parametrize('name,raw',[('a.exe',b'EXE'),('../a.url',b'x'),('a.url',b'a'* (MAX_SHORTCUT+1)),
    ('a.url',b'[InternetShortcut]\nURL=file:///C:/evil.exe'),('a.url',b'[InternetShortcut]\nURL=javascript:alert(1)'),
    ('a.url',b'[InternetShortcut]\nURL=steam://install/123'),('a.url',b'[InternetShortcut]\nURL=steam://run/570//evil')],ids=['exe','traversal','large','file','javascript','steam-install','steam-args'])
def test_shortcut_upload_rejects_unsupported_paths_sizes_and_protocols(store,name,raw):
    with pytest.raises((LauncherError,ValueError)): AppScanner(store).upload({'name':name,'data':encoded(raw)})
    assert len(store.snapshot()['items'])==43


def test_real_native_lnk_keeps_arguments_and_working_directory(store,root):
    import subprocess
    from launcher_service import windows_exe,HIDDEN
    path=root/'Portable.lnk'; target=Path(os.environ['SystemRoot'])/'explorer.exe'
    script="$s=New-Object -ComObject WScript.Shell;$l=$s.CreateShortcut($env:TEST_LINK);$l.TargetPath=$env:TEST_TARGET;$l.Arguments='--name \"hello world\"';$l.WorkingDirectory=$env:TEST_WORKING;$l.IconLocation=$env:TEST_TARGET+',0';$l.Save()"
    env=dict(os.environ,TEST_LINK=str(path.resolve()),TEST_TARGET=str(target),TEST_WORKING=str(root))
    subprocess.run([windows_exe('System32/WindowsPowerShell/v1.0/powershell.exe'),'-NoProfile','-Command',script],env=env,check=True,capture_output=True,creationflags=HIDDEN)
    scanner=AppScanner(store)
    item=scanner.parse_file(path)
    assert item['target_config']['args']=='--name "hello world"'
    assert Path(item['target_config']['working_dir'])==root
    assert Path(item['target_config']['target'])==target
    assert item['icon'].startswith('static/icons/')
    upload=scanner.upload({'name':'Portable.lnk','data':encoded(path.read_bytes())})
    assert upload['name']=='Portable'


def test_scan_deduplicates_and_protected_claude_never_becomes_executable(store,root):
    scanner=AppScanner(store)
    a=scanner._candidate('Claude','C:/Apps/Claude.exe')
    assert a['launch_type']=='claude_protected' and not a['target_config']['target']
    candidate=make_item('candidate-1','Demo','daily_apps','web','https://example.com/')
    (root/'demo.url').write_text('[InternetShortcut]\nURL=https://example.com/')
    (root/'duplicate.url').write_text('[InternetShortcut]\nURL=https://example.com/')
    with patch.object(scanner,'_roots',return_value=[root]), patch.object(scanner,'_store_apps',return_value=[]):
        data=scanner.scan(); assert len(data['apps'])==1
        assert len(scanner.scan()['apps'])==1
    with pytest.raises(LauncherError): scanner.pin({'ids':['nonexistent'],'group':'projects','pinned':False,'allow_duplicate':False})


def test_icon_is_transparent_content_addressed_png(root):
    from PIL import Image
    import hashlib
    cache=IconCache(root)
    icon=cache.extract(str(Path(os.environ['SystemRoot'])/'explorer.exe'))
    assert icon
    raw=(root/icon).read_bytes()
    assert Path(icon).stem==hashlib.sha256(raw).hexdigest()
    picture=Image.open(io.BytesIO(raw))
    assert picture.mode=='RGBA' and picture.size==(128,128)
    assert picture.getextrema()[-1]==(0,255)
    with pytest.raises(FileNotFoundError): cache.read('../../launcher.json')


def wait_state(flow,desired):
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        result=flow.snapshot()['job']
        if result['state'] in desired:return result
        time.sleep(.01)
    raise AssertionError(flow.snapshot()['job'])


@pytest.fixture
def workflow(store):
    adapter=Mock();adapter.launch.return_value={'status':'opened'}
    status=Mock();status.local.return_value={'clash':'running','proxy':'connected'}
    service=LauncherService(store,adapter)
    return Workflows(store,service,status),adapter


def test_workflow_pauses_at_claude_until_manual_launch_signal(workflow):
    flow,adapter=workflow
    flow.save({'id':'test','name':'Test','item_ids':['gemini','claude-desktop','ai-studio'],'require_proxy':False})
    flow.start('test')
    job=wait_state(flow,{'waiting_claude'})
    assert adapter.launch.call_count==1
    assert job['action']=='open_claude_drawer' and job['steps'][2]['state']=='pending'
    with pytest.raises(LauncherError): flow.start('test')
    flow.claude_launched()
    assert wait_state(flow,{'done'})['steps'][2]['state']=='done'
    assert [call.args[0]['id'] for call in adapter.launch.call_args_list]==['gemini','ai-studio']


def test_workflow_cancel_and_failed_proxy_do_not_run_following_steps(workflow):
    flow,adapter=workflow
    flow.save({'id':'cancel','name':'Cancel','item_ids':['claude-desktop','gemini'],'require_proxy':False})
    flow.start('cancel');wait_state(flow,{'waiting_claude'});flow.cancel()
    assert wait_state(flow,{'cancelled'})['steps'][1]['state']=='pending'
    adapter.launch.assert_not_called()
    flow.status.local.return_value={'clash':'stopped','proxy':'disconnected'}
    flow.start('ai-creation')
    assert wait_state(flow,{'failed'})['state']=='failed'
    adapter.launch.assert_not_called()  # Empty Clash target fails before any scene tool opens.


def test_workflow_crud_and_missing_reference_are_validated(workflow):
    flow,_=workflow
    for ids in ([],['missing'],['gemini','gemini']):
        with pytest.raises(LauncherError): flow.save({'id':'bad','name':'Bad','item_ids':ids,'require_proxy':False})
    flow.save({'id':'good','name':'Good','item_ids':['gemini'],'require_proxy':False})
    assert any(row['id']=='good' for row in flow.snapshot()['workflows'])
    flow.delete('good');assert not any(row['id']=='good' for row in flow.snapshot()['workflows'])


def test_backup_preview_does_not_mutate_and_restore_keeps_original_archive(store,workflow):
    flow,_=workflow; cache=IconCache(store.path.parent);backup=Backups(store,flow,cache)
    raw=backup.export(); before=store.path.read_bytes()
    preview=backup.preview({'data':encoded(raw)})
    assert preview['items']==17 and store.path.read_bytes()==before
    store.save('items',{'id':'gemini','name':'Changed'})
    result=backup.restore({'id':preview['id']})
    assert store.get('items','gemini')['name']=='Gemini 网页版'
    assert (store.path.parent/'reports'/'backups'/result['backup_name']).exists()
    with pytest.raises(LauncherError): backup.restore({'id':preview['id']})


@pytest.mark.parametrize('extra',['../evil.exe','icons/../../evil.png','unrelated.txt'])
def test_backup_rejects_traversal_and_unknown_files(store,workflow,extra):
    flow,_=workflow;backup=Backups(store,flow,IconCache(store.path.parent))
    stream=io.BytesIO(backup.export())
    with zipfile.ZipFile(stream,'a') as archive:archive.writestr(extra,b'bad')
    before=store.path.read_bytes()
    with pytest.raises(LauncherError): backup.preview({'data':encoded(stream.getvalue())})
    assert store.path.read_bytes()==before


def test_backup_rejects_unsafe_target_and_restores_transaction_on_failure(store,workflow):
    flow,_=workflow;backup=Backups(store,flow,IconCache(store.path.parent))
    raw=backup.export(); preview=backup.preview({'data':encoded(raw)})
    store.save('items',{'id':'gemini','name':'Current'})
    import launcher_backup
    original=launcher_backup.atomic_json; count=0
    def fail_once(path,data):
        nonlocal count
        count+=1
        if count==1:raise OSError('disk error')
        return original(path,data)
    with patch('launcher_backup.atomic_json',side_effect=fail_once),pytest.raises(LauncherError):
        backup.restore({'id':preview['id']})
    assert store.get('items','gemini')['name']=='Current'
    data=store._read();data['items'][0]['target_config']['target']='file:///C:/evil.exe'
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        archive.writestr('launcher.json',json.dumps(data));archive.writestr('workflows.json',json.dumps(flow.read()))
    with pytest.raises(LauncherError):backup.preview({'data':encoded(stream.getvalue())})


def test_new_api_origin_and_no_workflow_resume_bypass(store):
    with live_server(store) as (server,adapter):
        for route,body in [('scanner/pin',{}),('workflows/start',{'id':'ai-creation'}),('backup/restore',{'id':'x'}),('desktop/wake',{})]:
            assert request(server,'api/'+route,'POST',body,origin='https://evil.example')[0]==403
        assert request(server,'api/workflows/resume','POST',{'id':'fake'})[0]==404
        assert request(server,'api/scanner/parse-shortcut','POST',{'path':'C:/evil.lnk'})[0]==400
        assert request(server,'api/workflows')[0]==200
        assert request(server,'api/backup/export')[0]==200
        adapter.launch.assert_not_called()


def test_only_original_manual_launch_handler_notifies_workflow(store):
    from dashboard import Dashboard,make_handler
    from launcher_api import LauncherAPI
    from http.server import ThreadingHTTPServer
    import threading
    adapter=Mock();service=LauncherService(store,adapter);api=LauncherAPI(store,service,'test-session');api.features()
    api.workflows.save({'id':'guard','name':'Guard','item_ids':['claude-desktop'],'require_proxy':False})
    api.workflows.start('guard');wait_state(api.workflows,{'waiting_claude'})
    dashboard=Dashboard()
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(dashboard,'test-session',api));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        assert request(server,'launch','POST',{'run_id':'forged','expected':dashboard.state['expected']})[0]==409
        assert api.workflows.snapshot()['job']['state']=='waiting_claude'
        dashboard.state.update(run_id='verified',report={'finished':time.time(),'expected_ip':dashboard.state['expected'],'native':{'kind':'exe'},'checks':{key:{'state':'pass'} for key in ('guard','claude','system','tls')}})
        with patch('dashboard.validate_guard',return_value={'state':'pass'}),patch('dashboard.controller',return_value={}), \
            patch('dashboard.classify_connections',return_value=[]),patch('dashboard.native_claude',return_value={'kind':'exe','target':'C:/Fake/Claude.exe'}),patch('dashboard.subprocess.Popen') as launch:
            assert request(server,'launch','POST',{'run_id':'verified','expected':dashboard.state['expected']})[0]==200
            launch.assert_called_once()
        assert wait_state(api.workflows,{'done'})['state']=='done'
    finally:
        api.workflows.cancel();server.shutdown();server.server_close();thread.join(2)


def test_desktop_closed_window_reopens_same_session_and_throttles_duplicates():
    from launcher_desktop import Desktop
    desktop=Desktop('http://127.0.0.1:1234/session/',lambda:None)
    with patch.object(desktop,'app_windows',return_value=[]) as find,patch('launcher_desktop.subprocess.Popen') as launch:
        desktop.wake();desktop.wake()
        assert launch.call_count==1
        assert '--app=http://127.0.0.1:1234/session/' in launch.call_args.args[0]
        find.assert_called_with(desktop.url)
        assert desktop.snapshot()['wake_count']==2


def test_preset_repair_preserves_custom_configuration_and_uses_native_app(store):
    scanner=AppScanner(store)
    native=make_item('candidate-native','ChatGPT','daily_apps','uwp','OpenAI.ChatGPT_123!App')
    browser=make_item('candidate-browser','ChatGPT','daily_apps','exe','C:/Apps/msedge.exe')
    scanner.repair_presets([browser,native])
    assert store.get('items','chatgpt-desktop')['launch_type']=='uwp'
    original=store.get('items','chatgpt-desktop')['target_config']['target']
    scanner.repair_presets([make_item('another','ChatGPT','daily_apps','uwp','Other.Package_123!App')])
    assert store.get('items','chatgpt-desktop')['target_config']['target']==original


def test_valid_backup_can_recover_corrupt_local_configuration(store,workflow):
    flow,_=workflow;backup=Backups(store,flow,IconCache(store.path.parent));raw=backup.export()
    store.path.write_text('{broken',encoding='utf-8')
    preview=backup.preview({'data':encoded(raw)})
    result=backup.restore({'id':preview['id']})
    assert len(store.snapshot()['items'])==43
    with zipfile.ZipFile(store.path.parent/'reports'/'backups'/result['backup_name']) as original:
        assert original.read('launcher.json')==b'{broken'


def test_recovery_api_reenables_launcher_after_corrupt_configuration_restore(store,workflow):
    flow,_=workflow;backup=Backups(store,flow,IconCache(store.path.parent));raw=backup.export()
    store.path.write_text('{broken',encoding='utf-8')
    with live_server(store,unavailable=True) as (server,adapter):
        assert request(server,'api/launcher/items')[0]==503
        status,body=request(server,'api/backup/preview','POST',{'data':encoded(raw)})
        assert status==200
        assert request(server,'api/backup/restore','POST',{'id':json.loads(body)['id']})[0]==200
        assert request(server,'api/launcher/items')[0]==200
        assert request(server,'state')[0]==200
        adapter.launch.assert_not_called()
