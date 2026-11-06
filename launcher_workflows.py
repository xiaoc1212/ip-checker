"""Persistent ordered scenes. A protected Claude step resumes only after the original manual launch succeeds."""
from copy import deepcopy
import json
import os
from pathlib import Path
import threading
import time
import uuid

from launcher_store import LauncherError, identifier, text


def atomic_json(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class Workflows:
    def __init__(self, store, service, status):
        self.store, self.service, self.status = store, service, status
        self.path = store.path.with_name('workflows.json')
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.claude_event = threading.Event()
        self.job = {'state': 'idle', 'steps': [], 'message': '选择场景，按顺序打开工具'}
        if not self.path.exists():
            try:
                ids = {i['id'] for i in store.snapshot()['items']}
            except LauncherError:
                ids = set()
            selected = [i for i in ['gemini', 'ai-studio', 'google-flow', 'claude-desktop'] if i in ids]
            atomic_json(self.path, {'version': 1, 'workflows': [{'id': 'ai-creation', 'name': 'AI 创作流',
                'item_ids': selected, 'require_proxy': True}] if selected else []})

    @staticmethod
    def validate(data, item_ids=None):
        if (not isinstance(data, dict) or set(data) != {'version', 'workflows'} or data['version'] != 1
                or not isinstance(data['workflows'], list) or len(data['workflows']) > 100):
            raise LauncherError(400, 'invalid_workflow', '场景配置无效')
        seen = set()
        for row in data['workflows']:
            if not isinstance(row, dict) or set(row) != {'id', 'name', 'item_ids', 'require_proxy'}:
                raise LauncherError(400, 'invalid_workflow', '场景字段无效')
            identifier(row['id']); text(row['name'], '场景名称', 120)
            if (row['id'] in seen or not row['name'].strip() or type(row['require_proxy']) is not bool
                    or not isinstance(row['item_ids'], list) or not 0 < len(row['item_ids']) <= 100
                    or any(not isinstance(i, str) for i in row['item_ids']) or len(set(row['item_ids'])) != len(row['item_ids'])
                    or item_ids is not None and not set(row['item_ids']) <= item_ids):
                raise LauncherError(400, 'invalid_workflow', '场景项目已移除、重复或为空，请重新选择')
            seen.add(row['id'])

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.validate(data)
            return data
        except (OSError, ValueError, TypeError):
            raise LauncherError(503, 'workflow_unavailable', '场景配置不可读，请恢复备份')

    def snapshot(self):
        with self.lock:
            return {**self.read(), 'job': deepcopy(self.job)}

    def save(self, body):
        with self.store.lock, self.lock:
            data = self.read()
            data['workflows'] = [row for row in data['workflows'] if row['id'] != body.get('id')]
            data['workflows'].append(body)
            self.validate(data, {row['id'] for row in self.store.snapshot()['items']})
            atomic_json(self.path, data)
            return {'ok': True}

    def delete(self, item_id):
        identifier(item_id)
        with self.lock:
            data = self.read()
            data['workflows'] = [row for row in data['workflows'] if row['id'] != item_id]
            atomic_json(self.path, data)

    def start(self, item_id):
        identifier(item_id)
        with self.store.lock, self.lock:
            if self.job['state'] in ('running', 'waiting_claude'):
                raise LauncherError(409, 'workflow_busy', '已有场景正在执行或等待手动启动 Claude')
            row = next((r for r in self.read()['workflows'] if r['id'] == item_id), None)
            if not row:
                raise LauncherError(404, 'not_found', '未找到场景')
            self.validate({'version': 1, 'workflows': [row]}, {i['id'] for i in self.store.snapshot()['items']})
            self.cancelled.clear(); self.claude_event.clear()
            self.job = {'id': uuid.uuid4().hex, 'workflow_id': row['id'], 'state': 'running', 'message': '准备环境',
                        'steps': [{'id': i, 'state': 'pending', 'name': self.store.get('items', i)['name']} for i in row['item_ids']]}
            threading.Thread(target=self._run, args=(deepcopy(row),), daemon=True).start()
            return {'ok': True, 'job': deepcopy(self.job)}

    def cancel(self):
        with self.lock:
            self.cancelled.set(); self.claude_event.set()

    def claude_launched(self):
        with self.lock:
            if self.job['state'] == 'waiting_claude':
                self.claude_event.set()

    def _update(self, **fields):
        with self.lock:
            self.job.update(fields)

    def _run(self, scene):
        try:
            if scene['require_proxy']:
                local = self.status.local()
                if local['clash'] != 'running' or local['proxy'] != 'connected':
                    self.service.open({'id': 'clash-desktop'}, singleton=True)
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        if self.cancelled.wait(.5):
                            break
                        local = self.status.local(force=True)
                        if local['clash'] == 'running' and local['proxy'] == 'connected':
                            break
                    if not self.cancelled.is_set() and (local['clash'] != 'running' or local['proxy'] != 'connected'):
                        raise LauncherError(409, 'proxy_not_ready', 'Clash 或代理端口尚未就绪，请打开 Clash 后重试')
            for index, item_id in enumerate(scene['item_ids']):
                if self.cancelled.is_set():
                    break
                item = self.store.get('items', item_id)
                with self.lock:
                    self.job['steps'][index]['state'] = 'running'
                    self.job['message'] = item['name']
                if item['launch_type'] == 'claude_protected':
                    with self.lock:
                        self.claude_event.clear()
                        self.job.update(state='waiting_claude', action='open_claude_drawer', message='请完成 Claude 检测并手动点击启动，再继续场景')
                    self.claude_event.wait()
                    if self.cancelled.is_set():
                        break
                    self._update(state='running', action='')
                else:
                    self.service.open({'id': item_id}, singleton=True)
                with self.lock:
                    self.job['steps'][index]['state'] = 'done'
            self._update(state='cancelled' if self.cancelled.is_set() else 'done', action='',
                         message='已停止后续步骤，已打开的软件保持运行' if self.cancelled.is_set() else '场景已完成')
        except Exception as exc:
            self._update(state='failed', action='', message=str(exc) if isinstance(exc, LauncherError) else '场景未能完成，请检查启动项配置')
