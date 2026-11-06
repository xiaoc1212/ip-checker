"""Reviewable ZIP backups. Restore validates all data first and keeps a pre-restore archive."""
import base64
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import time
import uuid
import zipfile

from PIL import Image
from launcher_store import LauncherError
from launcher_workflows import Workflows, atomic_json

MAX_BACKUP = 20 * 1024 * 1024


class Backups:
    def __init__(self, store, workflows, icons):
        self.store, self.workflows, self.icons = store, workflows, icons
        self.pending = {}

    def export(self):
        with self.store.lock, self.workflows.lock:
            data = self.store._read()
            flows = self.workflows.read()
            # Stale references can result from removing an app; retain scenes but omit removed steps in export.
            ids = {row['id'] for row in data['items']}
            flows['workflows'] = [{**row, 'item_ids': [key for key in row['item_ids'] if key in ids]}
                                  for row in flows['workflows']]
            flows['workflows'] = [row for row in flows['workflows'] if row['item_ids']]
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('launcher.json', json.dumps(data, ensure_ascii=False))
                archive.writestr('workflows.json', json.dumps(flows, ensure_ascii=False))
                for name in {row['icon'].split('/')[-1] for row in data['items'] if row['icon'].startswith('static/icons/')}:
                    try:
                        raw = self.icons.read(name)
                        archive.writestr('icons/' + name, raw)
                    except FileNotFoundError:
                        pass
            return stream.getvalue()

    def preview(self, body):
        if set(body) != {'data'}:
            raise LauncherError(400, 'invalid_backup', '请选择工作台 ZIP 备份')
        try:
            raw = base64.b64decode(body['data'], validate=True)
            if len(raw) > MAX_BACKUP:
                raise ValueError
            icons = {}
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if (len(entries) > 2100 or len({i.filename for i in entries}) != len(entries)
                        or sum(i.file_size for i in entries) > MAX_BACKUP):
                    raise ValueError
                names = {i.filename for i in entries}
                if not {'launcher.json', 'workflows.json'} <= names:
                    raise ValueError
                for name in names:
                    if name in ('launcher.json', 'workflows.json'):
                        continue
                    if not re.fullmatch(r'icons/[a-f0-9]{64}\.png', name):
                        raise ValueError
                    image = archive.read(name)
                    if len(image) > 1024 * 1024 or hashlib.sha256(image).hexdigest() + '.png' != name.split('/')[-1]:
                        raise ValueError
                    with Image.open(io.BytesIO(image)) as picture:
                        if picture.format != 'PNG' or max(picture.size) > 1024:
                            raise ValueError
                        picture.verify()
                    icons[name.split('/')[-1]] = image
                data = json.loads(archive.read('launcher.json'))
                self.store._validate(data)
                flows = json.loads(archive.read('workflows.json'))
                Workflows.validate(flows, {row['id'] for row in data['items']})
                for item in data['items']:
                    if item['icon'].startswith('static/icons/') and item['icon'].split('/')[-1] not in icons:
                        item['icon'] = 'builtin:' + item['group']
            token = uuid.uuid4().hex
            self.pending = {token: (time.time(), data, flows, icons)}
            return {'id': token, 'items': len(data['items']), 'subscriptions': len(data['subscriptions']),
                    'workflows': len(flows['workflows']), 'icons': len(icons)}
        except (ValueError, TypeError, KeyError, OSError, zipfile.BadZipFile, RuntimeError, LauncherError):
            raise LauncherError(400, 'invalid_backup', '备份格式、配置或图标校验失败；当前配置未修改')

    def restore(self, body):
        if set(body) != {'id'} or not isinstance(body['id'], str):
            raise LauncherError(400, 'invalid_backup', '请先预览备份')
        with self.store.lock, self.workflows.lock:
            record = self.pending.get(body['id'])
            if not record or time.time() - record[0] > 600:
                raise LauncherError(409, 'preview_expired', '备份预览已过期，请重新选择文件')
            if self.workflows.job['state'] in ('running', 'waiting_claude'):
                raise LauncherError(409, 'workflow_busy', '请先停止场景，再恢复配置')
            _, data, flows, icons = record
            before = self.store.path.read_bytes() if self.store.path.exists() else None
            before_flows = self.workflows.path.read_bytes() if self.workflows.path.exists() else None
            directory = self.store.path.parent / 'reports' / 'backups'; directory.mkdir(parents=True, exist_ok=True)
            backup_path = directory / ('before-restore-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6] + '.zip')
            try:
                original_archive = self.export()
            except LauncherError:
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                    if before is not None: archive.writestr('launcher.json', before)
                    if before_flows is not None: archive.writestr('workflows.json', before_flows)
                original_archive = stream.getvalue()
            backup_path.write_bytes(original_archive)
            try:
                self.icons.directory.mkdir(parents=True, exist_ok=True)
                for name, raw in icons.items():
                    path = self.icons.directory / name
                    if not path.exists():
                        temp = path.with_suffix('.tmp'); temp.write_bytes(raw); os.replace(temp, path)
                self.store._write(deepcopy(data))
                atomic_json(self.workflows.path, flows)
            except Exception:
                for path, raw in ((self.store.path, before), (self.workflows.path, before_flows)):
                    if raw is not None:
                        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
                        temporary.write_bytes(raw); os.replace(temporary, path)
                    else:
                        path.unlink(missing_ok=True)
                raise LauncherError(503, 'restore_failed', '恢复失败，已还原原配置；恢复前备份也已保留')
            self.pending.clear()
            return {'ok': True, 'backup_name': backup_path.name}
