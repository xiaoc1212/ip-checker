"""Validated local launcher data. No launch side effects belong in this module."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import math
import ntpath
import os
from pathlib import Path, PurePosixPath
import re
import threading
import uuid
from urllib.parse import urlsplit


GROUPS = [
    {'id': 'pinned', 'name': '常用置顶'},
    {'id': 'google_ai', 'name': 'Google AI'},
    {'id': 'gpt_claude', 'name': 'ChatGPT & Claude'},
    {'id': 'daily_apps', 'name': '日常软件'},
    {'id': 'mail', 'name': '邮箱'},
    {'id': 'network', 'name': '网络工具'},
    {'id': 'projects', 'name': '自定义与项目'},
    {'id': 'ai_creative', 'name': 'AI 视频与生图'},
    {'id': 'assets', 'name': '素材与资源'},
    {'id': 'media', 'name': '影音与社区'},
    {'id': 'ai_reference', 'name': 'AI 信息与工具'},
    {'id': 'vpn_services', 'name': 'VPN 与机场'},
]
LEGACY_GROUPS = GROUPS[:7]
BOOKMARKS = [
    ["runninghub","RunningHub","ai_creative","https://www.runninghub.cn/"],
    ["xiaoyunque","小云雀","ai_creative","https://xyq.jianying.com/"],
    ["libtv","LibTV","ai_creative","https://www.libtv.com/"],
    ["jimeng","即梦","ai_creative","https://jimeng.jianying.com/"],
    ["aigei","爱给网","assets","https://www.aigei.com/"],
    ["unity-store","Unity Asset Store","assets","https://assetstore.unity.com/"],
    ["youtube","YouTube","media","https://www.youtube.com/"],
    ["bilibili","B站","media","https://www.bilibili.com/"],
    ["xiaozhi","小智控制台","ai_reference","https://xiaozhi.me/console/agents"],
    ["priceai","PriceAI","ai_reference","https://priceai.io/"],
    ["opentherank","OpenTheRank","ai_reference","https://opentherank.com/zh/ai-pricing/chatgpt/"],
    ["wgetcloud","WgetCloud","vpn_services","https://wgetcloud.org/"],
    ["justmysocks","Just My Socks","vpn_services","https://justmysocks.net/"],
    ["flyingbird","FlyingBird","vpn_services","https://flyingbird.cc/"],
    ["tag-internet","TAG Internet","vpn_services","https://www.tagvpn.vip/"],
    ["nexitally","Nexitally","vpn_services","https://nexitally.com/"],
    ["mojie","魔戒","vpn_services","https://mojie.co/"],
    ["astrill","Astrill","vpn_services","https://www.astrill.com/"],
    ["letsvpn","LetsVPN","vpn_services","https://letsvpn.world/"],
]
GROUP_IDS = {group['id'] for group in GROUPS}
LAUNCH_TYPES = {'web', 'exe', 'uwp', 'folder', 'claude_protected'}
BROWSERS = {'msedge.exe', 'chrome.exe', 'firefox.exe', 'brave.exe', 'iexplore.exe', 'opera.exe'}
TARGET_FIELDS = {'target', 'args', 'working_dir', 'browser', 'profile', 'singleton_process', 'official_url'}
ITEM_FIELDS = {'id', 'name', 'group', 'launch_type', 'icon', 'pinned', 'last_used', 'use_count', 'target_config'}
SUB_FIELDS = {'id', 'name', 'purpose', 'expire_date', 'official_url', 'renew_url', 'admin_url',
              'ip_info', 'notes', 'update_source'}
ID_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$')
AUMID_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*![A-Za-z0-9._-]+$')


class LauncherError(Exception):
    def __init__(self, status, code, message, **details):
        super().__init__(message)
        self.status = status
        self.payload = {'error': message, 'code': code, **details}


def invalid(message):
    raise LauncherError(400, 'invalid_config', message)


def text(value, label, limit=4096):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        invalid(label + '格式不正确')
    return value


def identifier(value):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        invalid('ID 格式不正确')
    return value


def web_url(value, label='网址'):
    text(value, label)
    if not value:
        return value
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in ('https', 'http') and parsed.hostname and not parsed.username
                 and not parsed.password and not re.search(r'[\s\\<>"\x00-\x1f\x7f]', value))
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        invalid(label + '必须是有效的 HTTP/HTTPS 地址')
    return value


def launch_url(value):
    if isinstance(value, str) and re.fullmatch(r'steam://(?:rungameid/\d+|run/\d+|open/main)', value):
        return value
    return web_url(value)


def local_path(value, label):
    text(value, label)
    if value and (not re.match(r'^[A-Za-z]:[\\/]', value) or ':' in value[2:]
                  or '..' in value.replace('/', '\\').split('\\')
                  or re.search(r'[\x00-\x1f<>"|?*]', value)):
        invalid(label + '必须是本机绝对路径')
    return value


def is_claude_target(kind, target):
    return (kind == 'exe' and ntpath.basename(target).lower() in ('claude.exe', 'claude-code.exe')
            or kind == 'uwp' and 'claude' in target.lower())


def validate_item(raw):
    if not isinstance(raw, dict) or set(raw) != ITEM_FIELDS:
        invalid('启动项字段不完整或含有未知字段')
    item = deepcopy(raw)
    identifier(item['id'])
    if not text(item['name'], '名称', 120).strip():
        invalid('请填写名称')
    if item['group'] not in GROUP_IDS or item['launch_type'] not in LAUNCH_TYPES:
        invalid('分组或启动类型无效')
    if type(item['pinned']) is not bool:
        invalid('置顶值必须是布尔值')
    if (type(item['use_count']) is not int or item['use_count'] < 0
            or type(item['last_used']) not in (int, float) or not math.isfinite(item['last_used'])
            or item['last_used'] < 0):
        invalid('使用记录格式不正确')
    icon = text(item['icon'], '图标', 200)
    if not re.fullmatch(r'builtin:[a-z0-9_-]+', icon):
        parts = PurePosixPath(icon).parts
        if (not re.fullmatch(r'static/icons/[A-Za-z0-9_-]+\.png', icon)
                or '..' in parts):
            invalid('图标必须是内置图标或本地图标缓存')
    config = item['target_config']
    if not isinstance(config, dict) or set(config) != TARGET_FIELDS:
        invalid('启动配置字段不完整或含有未知字段')
    for key, value in config.items():
        text(value, key)
    kind, target = item['launch_type'], config['target']
    if config['browser'] not in ('default', 'msedge', 'chrome'):
        invalid('浏览器配置无效')
    if config['profile'] and (config['browser'] == 'default'
                              or not re.fullmatch(r'[A-Za-z0-9 _-]{1,80}', config['profile'])):
        invalid('请为指定浏览器填写配置文件夹名，而不是路径')
    web_url(config['official_url'], '官网')
    local_path(config['working_dir'], '工作目录')
    if kind == 'web':
        launch_url(target)
        if target.startswith('steam:') and (config['browser'] != 'default' or config['profile']):
            invalid('Steam 游戏入口必须使用系统关联打开')
        if config['args'] or config['working_dir'] or config['singleton_process']:
            invalid('网页不允许配置程序参数、工作目录或单例进程')
    elif kind in ('exe', 'folder'):
        local_path(target, '目标')
        if kind == 'exe' and target and ntpath.splitext(target)[1].lower() != '.exe':
            invalid('程序目标必须是 EXE 文件')
    elif kind == 'uwp' and target and not AUMID_PATTERN.fullmatch(target):
        invalid('商店应用标识 AUMID 无效')
    if kind != 'web' and (config['browser'] != 'default' or config['profile']):
        invalid('仅网页入口可指定浏览器和个人资料')
    if kind != 'exe' and (config['args'] or config['working_dir']):
        invalid('仅 EXE 入口可配置启动参数和工作目录')
    process = config['singleton_process']
    if process and (kind not in ('exe', 'uwp') or not re.fullmatch(r'[A-Za-z0-9_. -]+\.exe', process)
                    or process.lower() in BROWSERS
                    or kind == 'exe' and ntpath.basename(target).lower() in BROWSERS):
        invalid('单例进程仅用于非浏览器客户端')
    if is_claude_target(kind, target) or item['id'] == 'claude-desktop' and kind != 'claude_protected':
        invalid('Claude 客户端必须使用受保护的检测启动入口')
    if kind == 'claude_protected' and any(config[key] for key in ('target', 'args', 'working_dir', 'singleton_process')):
        invalid('Claude 保护入口不接受直接执行目标')
    return item


def validate_subscription(raw):
    if not isinstance(raw, dict) or set(raw) != SUB_FIELDS:
        invalid('订阅字段不完整或含有未知字段')
    sub = deepcopy(raw)
    identifier(sub['id'])
    for key in SUB_FIELDS - {'id'}:
        text(sub[key], key, 8000 if key == 'notes' else 4096)
    if not sub['name'].strip():
        invalid('请填写订阅名称')
    if sub['expire_date']:
        try:
            if date.fromisoformat(sub['expire_date']).isoformat() != sub['expire_date']:
                raise ValueError
        except ValueError:
            invalid('到期日必须为 YYYY-MM-DD')
    for key in ('official_url', 'renew_url', 'admin_url'):
        web_url(sub[key], key)
    if sub['update_source'] not in ('手动填写', '自动同步'):
        invalid('订阅更新来源无效')
    return sub


def subscription_view(sub, today=None):
    today = today or datetime.now(timezone(timedelta(hours=8))).date()
    days = (date.fromisoformat(sub['expire_date']) - today).days if sub['expire_date'] else None
    status = ('unfilled' if days is None else 'expired' if days < 0 else 'due_today' if days == 0
              else 'urgent' if days <= 3 else 'warning' if days <= 7 else 'normal')
    return {**deepcopy(sub), 'remaining_days': days, 'expiry_status': status}


def make_item(item_id, name, group, kind, target='', official_url='', pinned=False):
    return {'id': item_id, 'name': name, 'group': group, 'launch_type': kind,
            'icon': 'builtin:' + group, 'pinned': pinned, 'last_used': 0, 'use_count': 0,
            'target_config': {'target': target, 'args': '', 'working_dir': '', 'browser': 'default',
                              'profile': '', 'singleton_process': '', 'official_url': official_url}}


def defaults(discovered=None):
    entries = [
        ('gemini', 'Gemini 网页版', 'google_ai', 'web', 'https://gemini.google.com/'),
        ('ai-studio', 'Google AI Studio', 'google_ai', 'web', 'https://aistudio.google.com/'),
        ('google-flow', 'Google Flow', 'google_ai', 'web', 'https://labs.google/fx/tools/flow'),
        ('antigravity-desktop', 'Antigravity 客户端', 'google_ai', 'exe', '', 'https://antigravity.google/'),
        ('antigravity-web', 'Antigravity 官网', 'google_ai', 'web', 'https://antigravity.google/'),
        ('chatgpt-web', 'ChatGPT 网页版', 'gpt_claude', 'web', 'https://chatgpt.com/'),
        ('chatgpt-desktop', 'ChatGPT 桌面客户端', 'gpt_claude', 'exe', '', 'https://chatgpt.com/download/'),
        ('claude-desktop', 'Claude 客户端', 'gpt_claude', 'claude_protected', '', 'https://claude.ai/download'),
        ('claude-web', 'Claude 网页版', 'gpt_claude', 'web', 'https://claude.ai/'),
        ('steam', 'Steam', 'daily_apps', 'exe', '', 'https://store.steampowered.com/about/'),
        ('yy', 'YY', 'daily_apps', 'exe', '', 'https://www.yy.com/'),
        ('qq-mail', 'QQ 邮箱', 'mail', 'web', 'https://mail.qq.com/'),
        ('gmail', 'Gmail', 'mail', 'web', 'https://mail.google.com/'),
        ('clash-desktop', 'Clash 客户端', 'network', 'exe', '', 'https://github.com/clash-verge-rev/clash-verge-rev/releases'),
        ('clash-github', 'Clash Verge Rev GitHub', 'network', 'web', 'https://github.com/clash-verge-rev/clash-verge-rev'),
        ('static-ip-admin', '静态 IP 管理后台', 'network', 'web', ''),
        ('vpn-web', '主用 VPN 官网', 'network', 'web', ''),
    ]
    entries.extend((item_id, name, group, 'web', url) for item_id, name, group, url in BOOKMARKS)
    items = [make_item(*entry) for entry in entries]
    for item in items:
        found = (discovered or {}).get(item['id'])
        if found and item['launch_type'] != 'claude_protected':
            item['launch_type'] = found['launch_type']
            item['target_config']['target'] = found['target']
            item['target_config']['singleton_process'] = found.get('singleton_process', '')
        item['pinned'] = item['id'] in ('gemini', 'chatgpt-web', 'claude-desktop')
    subscriptions = [
        {'id': sub_id, 'name': name, 'purpose': purpose, 'expire_date': '', 'official_url': '',
         'renew_url': '', 'admin_url': '', 'ip_info': '', 'notes': '', 'update_source': '手动填写'}
        for sub_id, name, purpose in [('static-ip', '主用静态 IP', '固定出口'), ('vpn', '主用 VPN', '网络连接')]
    ]
    return {'version': 1, 'groups': deepcopy(GROUPS), 'items': items, 'subscriptions': subscriptions}


class LauncherStore:
    @classmethod
    def for_recovery(cls, path):
        store = cls.__new__(cls)
        store.path = Path(path)
        store.lock = threading.RLock()
        return store

    def __init__(self, path, discover=None):
        self.path = Path(path)
        self.lock = threading.RLock()
        with self.lock:
            if self.path.exists():
                self._read()
            else:
                self._write(defaults(discover() if discover else None))

    def _validate(self, data):
        if (not isinstance(data, dict) or set(data) != {'version', 'groups', 'items', 'subscriptions'}
                or data['version'] != 1 or data['groups'] not in (GROUPS, LEGACY_GROUPS)):
            invalid('配置版本或分组无效')
        for key, validate in [('items', validate_item), ('subscriptions', validate_subscription)]:
            rows = data[key]
            if not isinstance(rows, list) or len(rows) > 2000:
                invalid('配置条目数量无效')
            ids = [validate(row)['id'] for row in rows]
            if len(set(ids)) != len(ids):
                invalid('配置 ID 重复')

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self._validate(data)
            if data['groups'] == LEGACY_GROUPS:
                data['groups'] = deepcopy(GROUPS)
                known_ids = {item['id'] for item in data['items']}
                known_urls = {item['target_config']['target'].rstrip('/') for item in data['items'] if item['launch_type'] == 'web'}
                for item_id, name, group, url in BOOKMARKS:
                    if item_id not in known_ids and url.rstrip('/') not in known_urls:
                        data['items'].append(make_item(item_id, name, group, 'web', url))
                self._write(data)
            return data
        except (OSError, ValueError, TypeError, KeyError, LauncherError) as exc:
            raise LauncherError(503, 'storage_unavailable', '启动台配置不可读或已损坏，请恢复备份；原文件未覆盖') from exc

    def _write(self, data):
        self._validate(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temp.open('x', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    def snapshot(self):
        with self.lock:
            data = self._read()
            data['subscriptions'] = [subscription_view(sub) for sub in data['subscriptions']]
            return data

    def get(self, collection, item_id):
        identifier(item_id)
        with self.lock:
            for row in self._read()[collection]:
                if row['id'] == item_id:
                    return row
        raise LauncherError(404, 'not_found', '未找到该配置项')

    def save(self, collection, raw, create=False):
        validator = validate_item if collection == 'items' else validate_subscription
        with self.lock:
            data = self._read()
            rows = data[collection]
            if not isinstance(raw, dict):
                invalid('配置必须是 JSON 对象')
            item_id = identifier(raw.get('id'))
            previous = next((row for row in rows if row['id'] == item_id), None)
            if create and previous:
                raise LauncherError(409, 'duplicate_id', '该 ID 已存在')
            if not create and not previous:
                raise LauncherError(404, 'not_found', '未找到该配置项')
            if collection == 'items':
                seed = previous or make_item(item_id, '', 'projects', 'web')
                allowed = ITEM_FIELDS - {'last_used', 'use_count'}
                if set(raw) - allowed:
                    invalid('使用次数与时间由后端维护，或请求含有未知字段')
                merged = {**seed, **raw, 'target_config': {**seed['target_config'], **raw.get('target_config', {})}}
                if previous and previous['launch_type'] == 'claude_protected' and merged['launch_type'] != 'claude_protected':
                    invalid('不能将 Claude 保护入口改为普通启动入口')
            else:
                seed = previous or {'id': item_id, 'name': '', 'purpose': '', 'expire_date': '',
                                    'official_url': '', 'renew_url': '', 'admin_url': '', 'ip_info': '',
                                    'notes': '', 'update_source': '手动填写'}
                if set(raw) - SUB_FIELDS:
                    invalid('剩余天数由后端计算，或请求含有未知字段')
                merged = {**seed, **raw}
            result = validator(merged)
            if previous:
                rows[rows.index(previous)] = result
            else:
                rows.append(result)
            self._write(data)
            return subscription_view(result) if collection == 'subscriptions' else result

    def delete(self, collection, item_id):
        with self.lock:
            data = self._read()
            row = self.get(collection, item_id)
            data[collection].remove(row)
            self._write(data)

    def reorder(self, collection, ids):
        with self.lock:
            data = self._read()
            rows = {row['id']: row for row in data[collection]}
            if (not isinstance(ids, list) or any(not isinstance(i, str) for i in ids)
                    or len(ids) != len(rows) or set(ids) != set(rows)):
                invalid('排序必须包含全部 ID，且不能重复')
            data[collection] = [rows[item_id] for item_id in ids]
            self._write(data)

    def record_use(self, item_id):
        with self.lock:
            data = self._read()
            row = next((row for row in data['items'] if row['id'] == item_id), None)
            if row is not None:
                import time
                row['last_used'] = time.time()
                row['use_count'] += 1
                self._write(data)

    def add_many(self, items):
        with self.lock:
            data = self._read()
            existing = {row['id'] for row in data['items']}
            for item in items:
                validate_item(item)
                if item['id'] in existing:
                    raise LauncherError(409, 'duplicate_id', '项目 ID 重复')
                existing.add(item['id'])
            data['items'].extend(items)
            self._write(data)
