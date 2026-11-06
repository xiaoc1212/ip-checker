"""Explicit, scoped Claude rule deployment; never changes DNS/TUN/proxy settings.

Credentials and full configurations stay in the existing Clash user directory.
The desktop dashboard never calls this module.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import yaml

from configure import transform, write_atomic
from controller_helper import BASE, request, snapshot
from diagnostics import tls_probe
from network import ROOT, GROUP, settings, trace_probe, request_text, validate_guard

RECEIPT = ROOT / 'reports' / 'client-rule-plan.json'
BACKUPS = BASE / 'ipdesk-client-backups'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_rule_only(before, after):
    if {k: v for k, v in before.items() if k != 'rules'} != {k: v for k, v in after.items() if k != 'rules'}:
        raise RuntimeError('候选配置包含规则以外的改动，已停止')
    if transform(before) != after or before.get('rules') == after.get('rules'):
        raise RuntimeError('候选配置不是预期的最小规则变更，已停止')


def validate_core(candidate):
    run = subprocess.run([r'D:\Clash Verge\verge-mihomo.exe', '-t', '-d', str(BASE), '-f', str(candidate)],
                         capture_output=True, timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
    if run.returncode:
        raise RuntimeError('内核配置校验失败；未输出可能含凭据的配置错误')


def prepare():
    snap = snapshot()
    cfg = snap['config']
    group = snap['proxies'].get(GROUP, {})
    members = group.get('all', [])
    if cfg.get('mode') != 'rule' or not cfg.get('tun', {}).get('enable') or cfg.get('find-process-mode') not in ('strict', 'always'):
        raise RuntimeError('现有系统接管或进程识别未确认；本方案不会更改这些开关')
    if len(members) != 1 or group.get('now') != members[0] or snap['proxies'].get(members[0], {}).get('type', '').lower() != 'http':
        raise RuntimeError('现有固定住宅组与受支持方案不符，已停止')
    script, runtime = BASE / 'profiles' / 'Script.js', BASE / 'clash-verge.yaml'
    original = script.read_text(encoding='utf-8-sig')
    if 'IPDESK_GUARD_BEGIN' in original:
        raise RuntimeError('源脚本已有旧包装，需先人工核对，不能叠加')
    before_hash = {'script': digest(script), 'runtime': digest(runtime)}
    before = yaml.safe_load(runtime.read_text(encoding='utf-8-sig'))
    after = transform(before)
    require_rule_only(before, after)
    wrapper = (ROOT / 'claude-guard.js').read_text(encoding='utf-8')
    source = original.rstrip() + '\n\n' + wrapper
    check = subprocess.run(['node', '--check'], input=source.encode('utf-8'), capture_output=True, timeout=10)
    if check.returncode:
        raise RuntimeError('订阅脚本语法未通过；未改动现有配置')
    backup = BACKUPS / datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True)
    shutil.copy2(script, backup / 'Script.before.js')
    shutil.copy2(runtime, backup / 'config.before.yaml')
    (backup / 'Script.candidate.js').write_text(source, encoding='utf-8')
    (backup / 'config.candidate.yaml').write_text(yaml.safe_dump(after, allow_unicode=True, sort_keys=False), encoding='utf-8')
    validate_core(backup / 'config.candidate.yaml')
    if before_hash != {'script': digest(script), 'runtime': digest(runtime)}:
        raise RuntimeError('准备期间配置发生变化，已停止；请重新准备')
    plan = {'backup': str(backup), 'before': before_hash, 'expected': settings()['expected_ip'],
            'candidates': {name: digest(backup / name) for name in ('Script.candidate.js', 'config.candidate.yaml')},
            'rules_before': len(before.get('rules', [])), 'rules_after': len(after['rules']),
            'global_settings_unchanged': True, 'state': 'prepared'}
    RECEIPT.parent.mkdir(exist_ok=True)
    RECEIPT.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(plan, ensure_ascii=False), flush=True)


def load_plan():
    plan = json.loads(RECEIPT.read_text(encoding='utf-8'))
    backup = Path(plan['backup']).resolve()
    if not backup.is_relative_to(BACKUPS.resolve()):
        raise RuntimeError('备份目录不合法')
    return plan, backup


def health(expected, port):
    checks = {key: trace_probe(expected, port, system=system) for key, system in [('proxy', False), ('system', True)]}
    checks['tls'] = tls_probe(expected, port)
    for key, url, via in [('domestic', 'https://www.baidu.com/', None),
                          ('general', 'https://www.cloudflare.com/cdn-cgi/trace', port)]:
        try:
            request_text(url, via)
            checks[key] = {'state': 'pass'}
        except Exception:
            checks[key] = {'state': 'unknown'}
    return checks


def check_health(checks):
    if any(row.get('state') != 'pass' for row in checks.values()):
        raise RuntimeError('联网验收未全部通过：' + '、'.join(k for k, v in checks.items() if v.get('state') != 'pass'))


def apply_prepared():
    plan, backup = load_plan()
    if plan['state'] != 'prepared':
        raise RuntimeError('本方案已处理过，不能重复应用')
    script, runtime = BASE / 'profiles' / 'Script.js', BASE / 'clash-verge.yaml'
    if plan['before'] != {'script': digest(script), 'runtime': digest(runtime)}:
        raise RuntimeError('现有配置已改变，已停止覆盖；请重新准备')
    for name, expected in plan['candidates'].items():
        if digest(backup / name) != expected:
            raise RuntimeError('候选文件已改变，已停止')
    before = yaml.safe_load((backup / 'config.before.yaml').read_text(encoding='utf-8-sig'))
    after = yaml.safe_load((backup / 'config.candidate.yaml').read_text(encoding='utf-8'))
    require_rule_only(before, after)
    validate_core(backup / 'config.candidate.yaml')
    port = int(snapshot()['config']['mixed-port'])
    baseline = health(plan['expected'], port)
    check_health(baseline)
    print('原有固定出口、TLS、国内及普通网站基线检查通过。', flush=True)
    if plan['before'] != {'script': digest(script), 'runtime': digest(runtime)}:
        raise RuntimeError('基线检查期间配置发生变化，已停止')
    try:
        write_atomic(script, (backup / 'Script.candidate.js').read_text(encoding='utf-8'))
        write_atomic(runtime, (backup / 'config.candidate.yaml').read_text(encoding='utf-8'))
        request('PUT', '/configs?force=true', {'payload': runtime.read_text(encoding='utf-8')})
        verdict = validate_guard(snapshot())
        if verdict['state'] != 'pass':
            raise RuntimeError('内核中的客户端规则未通过验收')
        print('客户端规则已加载，正在复核原有联网。', flush=True)
        checks = health(plan['expected'], port)
        check_health(checks)
    except Exception:
        # Avoid replacing unrelated edits made while validation was running.
        actual_source = digest(script)
        if actual_source not in (plan['before']['script'], plan['candidates']['Script.candidate.js']):
            raise RuntimeError('源脚本被其他程序更改，未自动覆盖；需核对本批备份') from None
        current = yaml.safe_load(runtime.read_text(encoding='utf-8-sig'))
        if current not in (before, after):
            raise RuntimeError('运行配置被其他程序更改，未自动覆盖；需核对本批备份') from None
        shutil.copy2(backup / 'Script.before.js', script)
        shutil.copy2(backup / 'config.before.yaml', runtime)
        request('PUT', '/configs?force=true', {'payload': runtime.read_text(encoding='utf-8-sig')})
        plan['state'] = 'rolled_back'
        RECEIPT.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
        raise RuntimeError('本次验收未通过，已恢复本批修改前配置') from None
    plan.update(state='applied', after={'script': digest(script), 'runtime': digest(runtime)},
                health=checks, baseline=baseline, verdict=verdict)
    RECEIPT.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'state': 'applied', 'guard': verdict, 'health': {k: v['state'] for k, v in checks.items()},
                      'global_settings_unchanged': True, 'backup': str(backup)}, ensure_ascii=False), flush=True)


def rollback():
    plan, backup = load_plan()
    script, runtime = BASE / 'profiles' / 'Script.js', BASE / 'clash-verge.yaml'
    if plan.get('state') != 'applied' or plan.get('after') != {'script': digest(script), 'runtime': digest(runtime)}:
        raise RuntimeError('配置已改变或本批未应用，停止自动恢复以保护后续修改')
    shutil.copy2(backup / 'Script.before.js', script)
    shutil.copy2(backup / 'config.before.yaml', runtime)
    request('PUT', '/configs?force=true', {'payload': runtime.read_text(encoding='utf-8-sig')})
    plan['state'] = 'rolled_back'
    RECEIPT.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    print('已恢复本批修改前配置。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    for name in ('prepare', 'apply', 'rollback'):
        action.add_argument('--' + name, action='store_true')
    args = parser.parse_args()
    try:
        (prepare if args.prepare else apply_prepared if args.apply else rollback)()
    except Exception as exc:
        # YAML/JSON parser exceptions can include credentials from config lines.
        print(str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__ + '：操作未完成，未输出原始配置错误')
        raise SystemExit(1)
