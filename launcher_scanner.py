"""Read-only software discovery and staged shortcut import; pinning uses opaque candidate IDs."""
import base64
import configparser
from copy import deepcopy
import json
import ntpath
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

from launcher_store import (LauncherError, GROUP_IDS, AUMID_PATTERN, BROWSERS, is_claude_target,
                            make_item, validate_item, local_path, launch_url)
from launcher_service import HIDDEN, windows_exe
from launcher_icons import IconCache

MAX_SHORTCUT = 500 * 1024


def target_key(item):
    value = item['target_config']['target']
    if item['launch_type'] in ('exe', 'folder'):
        value = ntpath.normcase(ntpath.normpath(value))
    return (item['launch_type'], value)


def decode_text(raw):
    for encoding in ('utf-8-sig', 'utf-16', 'mbcs'):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            pass
    raise LauncherError(400, 'invalid_shortcut', '快捷方式文字编码无法读取')


class AppScanner:
    def __init__(self, store, root=None):
        self.store = store
        self.root = Path(root or store.path.parent)
        self.icons = IconCache(self.root)
        self.lock = threading.RLock()
        self.candidates = {}
        self.apps = []
        self.scanned = 0
        self.errors = 0
        self.link_metadata = {}

    def _candidate(self, name, target, args='', working_dir='', icon_source='', icon_index=0, kind=None):
        target = os.path.expandvars(target.strip('"'))
        if not target:
            raise LauncherError(400, 'invalid_shortcut', '快捷方式没有可用目标')
        if target.startswith('shell:AppsFolder\\'):
            kind, target = 'uwp', target.split('\\', 1)[1]
        kind = kind or ('web' if re.match(r'^(?:https?://|steam://)', target, re.I) else
                        'folder' if Path(target).is_dir() else 'exe')
        item = make_item('candidate-' + uuid.uuid4().hex, name[:120], 'daily_apps', kind, target)
        config = item['target_config']
        if is_claude_target(kind, target):
            item['launch_type'], config['target'] = 'claude_protected', ''
        elif kind == 'web':
            launch_url(target)
        elif kind == 'exe':
            local_path(target, '软件目标')
            if not Path(target).is_file():
                raise LauncherError(400, 'target_missing', '快捷方式目标已不存在')
            config.update(args=args, working_dir=os.path.expandvars(working_dir))
            if ntpath.basename(target).lower() not in BROWSERS:
                config['singleton_process'] = ntpath.basename(target)
        elif kind == 'uwp' and 'chatgpt' in target.lower():
            config['singleton_process'] = 'ChatGPT.exe'
        validate_item(item)
        source = icon_source or ('shell:AppsFolder\\' + target if kind == 'uwp' else target if kind in ('exe', 'folder') else '')
        icon = self.icons.extract(source, icon_index)
        if icon:
            item['icon'] = icon
        return item

    def parse_file(self, path):
        path = Path(path)
        if path.suffix.lower() not in ('.lnk', '.url') or path.stat().st_size > MAX_SHORTCUT:
            raise LauncherError(400, 'invalid_shortcut', '请选取不超过 500KB 的 .lnk / .url 快捷方式')
        if path.suffix.lower() == '.url':
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.read_string(decode_text(path.read_bytes()))
            section = next((parser[s] for s in parser.sections() if s.lower() == 'internetshortcut'), None)
            if section is None:
                raise LauncherError(400, 'invalid_shortcut', '未找到 InternetShortcut 内容')
            return self._candidate(path.stem, section.get('url', ''), icon_source=section.get('iconfile', ''),
                                   icon_index=int(section.get('iconindex', '0')), kind='web')
        metadata = self.link_metadata.get(str(path.resolve()))
        if metadata is None:
            self._read_links([path])
            metadata = self.link_metadata.get(str(path.resolve()))
        if not metadata or metadata.get('error'):
            raise LauncherError(400, 'invalid_shortcut', 'Windows 无法解析这个快捷方式，请使用软件库搜索')
        target, args, working = metadata['target'], metadata['args'], metadata['working_dir']
        location = metadata['icon']
        match = re.match(r'^(.*),\s*(-?\d+)$', location)
        source, index = (match.group(1).strip('"'), int(match.group(2))) if match else (location, 0)
        return self._candidate(path.stem, target, args, working, source, index)

    def _read_links(self, paths):
        from network import python_exe
        result = subprocess.run([python_exe(), str(Path(__file__).with_name('launcher_shell.py'))],
                                input=json.dumps([str(Path(p).resolve()) for p in paths]).encode('utf-8'),
                                capture_output=True, timeout=60, creationflags=HIDDEN)
        if result.returncode:
            raise LauncherError(503, 'shortcut_parser_failed', '系统快捷方式解析未完成，原检测服务仍可使用')
        self.link_metadata.update(json.loads(result.stdout.decode('utf-8-sig')))

    def _roots(self):
        from win32com.shell import shell, shellcon
        return [Path(shell.SHGetFolderPath(0, key, None, 0)) for key in
                (shellcon.CSIDL_COMMON_PROGRAMS, shellcon.CSIDL_PROGRAMS,
                 shellcon.CSIDL_DESKTOPDIRECTORY, shellcon.CSIDL_COMMON_DESKTOPDIRECTORY)]

    def _store_apps(self):
        script = "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); ConvertTo-Json -InputObject @(Get-StartApps | Select-Object Name,AppID) -Compress"
        result = subprocess.run([windows_exe('System32/WindowsPowerShell/v1.0/powershell.exe'),
                                 '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, timeout=25, creationflags=HIDDEN)
        if result.returncode:
            raise OSError('Get-StartApps unavailable')
        return json.loads(result.stdout.decode('utf-8-sig'))

    def _stage(self, item):
        self.candidates[item['id']] = (time.time(), item)
        if len(self.candidates) > 5000:
            oldest = min(self.candidates, key=lambda key: self.candidates[key][0])
            self.candidates.pop(oldest)
        return item['id']

    def _view(self, item, existing=None):
        duplicates = [row['id'] for row in (self.store.snapshot()['items'] if existing is None else existing) if target_key(row) == target_key(item)]
        return {key: deepcopy(item[key]) for key in ('id', 'name', 'launch_type', 'icon')} | {'duplicate_ids': duplicates}

    def scan(self, refresh=False):
        with self.lock:
            existing = self.store.snapshot()['items']
            if self.scanned and not refresh:
                return {'apps': [self._view(self.candidates[key][1], existing) for key in self.apps if key in self.candidates],
                        'scanned_at': self.scanned, 'skipped': self.errors}
            items, seen = [], set()
            self.errors = 0
            def add(item):
                key = (*target_key(item), item['target_config']['args'], item['target_config']['profile'])
                if key not in seen:
                    seen.add(key); items.append(item)
            paths = []
            self.link_metadata.clear()
            for root in self._roots():
                count = 0
                for directory, folders, files in os.walk(root, followlinks=False):
                    folders[:] = [name for name in folders if not (Path(directory) / name).is_symlink()
                                   and not (Path(directory) / name).is_junction()] if len(Path(directory).relative_to(root).parts) < 12 else []
                    for name in files:
                        if Path(name).suffix.lower() not in ('.lnk', '.url'):
                            continue
                        count += 1
                        if count > 2000:
                            break
                        paths.append(Path(directory) / name)
                    if count > 2000:
                        break
            links = [p for p in paths if p.suffix.lower() == '.lnk' and p.stat().st_size <= MAX_SHORTCUT]
            if links:
                try:
                    self._read_links(links)
                except Exception:
                    self.link_metadata.update({str(p.resolve()): {'error': True} for p in links})
            for path in paths:
                try:
                    add(self.parse_file(path))
                except Exception:
                    self.errors += 1
            try:
                for app in self._store_apps():
                    if AUMID_PATTERN.fullmatch(app.get('AppID', '')):
                        try:
                            add(self._candidate(app['Name'], app['AppID'], kind='uwp'))
                        except Exception:
                            self.errors += 1
            except Exception:
                self.errors += 1
            items.sort(key=lambda item: item['name'].casefold())
            self.apps = [self._stage(item) for item in items]
            self.scanned = time.time()
            self.repair_presets(items)
            existing = self.store.snapshot()['items']
            return {'apps': [self._view(item, existing) for item in items], 'scanned_at': self.scanned, 'skipped': self.errors}

    def repair_presets(self, items):
        known = {'chatgpt-desktop': ('ChatGPT 桌面客户端', 'chatgpt'), 'clash-desktop': ('Clash 客户端', 'clash verge')}
        for item_id, (name, match) in known.items():
            try:
                old = self.store.get('items', item_id)
            except LauncherError:
                continue
            if old['name'] != name or old['target_config']['target'] or old['launch_type'] not in ('exe', 'uwp'):
                continue
            matches = [item for item in items if item['name'].lower() == match and item['launch_type'] in ('exe', 'uwp')
                       and (item['launch_type'] == 'uwp' or ntpath.basename(item['target_config']['target']).lower() not in BROWSERS)]
            matches.sort(key=lambda item: (not item['target_config']['target'].lower().startswith('openai.chatgpt'), item['launch_type'] != 'uwp'))
            if matches:
                candidate = matches[0]
                config = {**candidate['target_config'], 'official_url': old['target_config']['official_url']}
                self.store.save('items', {'id': item_id, 'launch_type': candidate['launch_type'], 'icon': candidate['icon'], 'target_config': config})

    def upload(self, body):
        if set(body) != {'name', 'data'} or not isinstance(body['name'], str) or ntpath.basename(body['name']) != body['name']:
            raise LauncherError(400, 'invalid_shortcut', '请上传快捷方式文件')
        suffix = Path(body['name']).suffix.lower()
        if suffix not in ('.lnk', '.url'):
            raise LauncherError(400, 'invalid_shortcut', '请拖入快捷方式或使用本机软件库搜索')
        try:
            raw = base64.b64decode(body['data'], validate=True)
        except (ValueError, TypeError):
            raise LauncherError(400, 'invalid_shortcut', '文件内容无效')
        if not raw or len(raw) > MAX_SHORTCUT:
            raise LauncherError(413, 'shortcut_too_large', '请拖入快捷方式或使用本机软件库搜索（最大 500KB）')
        directory = self.root / 'reports' / 'shortcut-imports'; directory.mkdir(parents=True, exist_ok=True)
        path = directory / (uuid.uuid4().hex + suffix)
        try:
            path.write_bytes(raw)
            item = self.parse_file(path)
            item['name'] = Path(body['name']).stem[:120]
            with self.lock:
                self._stage(item)
                return self._view(item)
        finally:
            path.unlink(missing_ok=True)

    def pick_shortcuts(self):
        from network import python_exe
        result = subprocess.run([python_exe(), str(Path(__file__).with_name('launcher_picker.py')), 'shortcuts'],
                                capture_output=True, timeout=180, creationflags=HIDDEN)
        if result.returncode:
            raise LauncherError(503, 'picker_failed', '未能读取所选快捷方式，请使用软件库或拖入文件')
        paths = json.loads(result.stdout.decode('utf-8-sig'))['files']
        if len(paths) > 50:
            raise LauncherError(400, 'too_many_shortcuts', '每次最多选择 50 个快捷方式，请分批添加')
        items, errors = [], []
        for path in paths[:50]:
            try:
                item = self.parse_file(path)
                with self.lock:
                    self._stage(item); items.append(self._view(item))
            except Exception:
                errors.append(Path(path).name + '：无法解析或目标不支持')
        return {'apps': items, 'errors': errors}

    def pin(self, body):
        if (set(body) != {'ids', 'group', 'pinned', 'allow_duplicate'} or body['group'] not in GROUP_IDS
                or type(body['pinned']) is not bool or type(body['allow_duplicate']) is not bool
                or not isinstance(body['ids'], list) or not 0 < len(body['ids']) <= 200
                or any(not isinstance(key, str) for key in body['ids']) or len(set(body['ids'])) != len(body['ids'])):
            raise LauncherError(400, 'invalid_selection', '请选择软件和所属分组')
        with self.lock, self.store.lock:
            items, existing = [], self.store.snapshot()['items']
            for key in body['ids']:
                candidate = self.candidates.get(key)
                if not candidate or time.time() - candidate[0] > 3600:
                    raise LauncherError(409, 'candidate_expired', '软件列表已过期，请重新扫描或导入')
                item = deepcopy(candidate[1])
                if not body['allow_duplicate'] and any(target_key(row) == target_key(item) for row in existing + items):
                    raise LauncherError(409, 'duplicate_target', '已存在相同入口，是否作为不同启动配置或多账号副本继续添加？')
                item.update(id='app-' + uuid.uuid4().hex, group=body['group'], pinned=body['pinned'])
                items.append(item)
            self.store.add_many(items)
            return {'ok': True, 'count': len(items)}

    def enrich(self, item):
        config = item['target_config']
        source = ('shell:AppsFolder\\' + config['target'] if item['launch_type'] == 'uwp'
                  else config['target'] if item['launch_type'] in ('exe', 'folder') else '')
        if source and item['icon'].startswith('builtin:'):
            icon = self.icons.extract(source)
            if icon:
                return self.store.save('items', {'id': item['id'], 'icon': icon})
        return item
