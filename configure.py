"""Backup, validate, apply and verify a minimal persistent Claude routing guard."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import yaml

from controller_helper import BASE, request, snapshot
from network import GROUP, ROOT, validate_guard


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_atomic(path, text):
    tmp = path.with_name(path.name + '.ipdesk-tmp')
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, path)


def transform(config):
    javascript = r'''
const fs=require('fs'),vm=require('vm');
const ctx={main:c=>c};vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
process.stdout.write(JSON.stringify(ctx.main(JSON.parse(fs.readFileSync(0,'utf8')))));
'''
    proc = subprocess.run(['node', '-e', javascript, str(ROOT / 'claude-guard.js')],
                          input=json.dumps(config, ensure_ascii=False).encode(), capture_output=True, timeout=15)
    if proc.returncode:
        raise RuntimeError('无法生成规则；请检查固定出口组是否只有一个成员')
    return json.loads(proc.stdout)


def prepare():
    script_path = BASE / 'profiles' / 'Script.js'
    runtime_path = BASE / 'clash-verge.yaml'
    source = script_path.read_text(encoding='utf-8-sig')
    source = re.sub(r'\n?// IPDESK_GUARD_BEGIN[\s\S]*?// IPDESK_GUARD_END\s*', '', source)
    new_script = source.rstrip() + '\n\n' + (ROOT / 'claude-guard.js').read_text(encoding='utf-8')
    check = subprocess.run(['node', '--check'], input=new_script.encode(), capture_output=True, timeout=10)
    if check.returncode:
        raise RuntimeError('扩展脚本语法检查失败，未修改现有配置')
    config = yaml.safe_load(runtime_path.read_text(encoding='utf-8'))
    patched = transform(config)
    return script_path, runtime_path, new_script, patched


def apply():
    raise RuntimeError('旧版自动配置已停用：本工具现为只读检测，不能重新写入全局 TUN/DNS 配置。')


def restore():
    receipt = json.loads((ROOT / 'install-receipt.json').read_text(encoding='utf-8'))
    backup = Path(receipt['backup']).resolve()
    if not backup.is_relative_to((BASE / 'ipdesk-backups').resolve()):
        raise RuntimeError('备份目录不合法')
    # Do not replace newer user changes with an older backup.
    if any(digest(Path(p)) != h for p, h in receipt['changed'].items()):
        raise RuntimeError('配置在安装后有新修改，已停止覆盖；请手动对比备份')
    script, runtime = BASE / 'profiles' / 'Script.js', BASE / 'clash-verge.yaml'
    shutil.copy2(backup / 'Script.js', script)
    shutil.copy2(backup / 'clash-verge.yaml', runtime)
    request('PUT', '/configs?force=true', {'payload': runtime.read_text(encoding='utf-8')})
    print('已恢复安装前的配置并重新加载。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    try:
        if args.apply:
            apply()
        elif args.restore:
            restore()
        else:
            print(json.dumps({'read_only': True, 'legacy_apply_disabled': True}, ensure_ascii=False))
    except Exception as exc:
        print(type(exc).__name__ + ': ' + str(exc))
        raise SystemExit(1)
