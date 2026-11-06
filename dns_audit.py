"""Build DNS evidence from current proxy metadata and a bounded capture report."""
import json
import time
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from controller_helper import load_config
from network import GROUP, ROOT, controller

DIRECTORY = ROOT / 'reports' / 'dns-audit'


def inspect_dns(config, connection_data, proxy_data):
    dns = config.get('dns', {})
    upstreams = []
    for source in dns.get('nameserver', []):
        parsed = urlsplit(str(source))
        options = parse_qs(parsed.fragment)
        skip = any(v.lower() == 'true' for v in options.get('skip-cert-verify', []))
        upstreams.append({'protocol': parsed.scheme, 'host': parsed.hostname,
                          'certificate_check_skipped': skip})
    rows = []
    for connection in connection_data.get('connections', []):
        meta = connection.get('metadata', {})
        if str(meta.get('process', '')).lower() not in ('claude.exe', 'claude-code.exe'):
            continue
        host = str(meta.get('host', '')).lower()
        if not any(host == root or host.endswith('.' + root) for root in
                   ('claude.ai', 'claude.com', 'anthropic.com', 'claudeusercontent.com', 'claudemcpcontent.com', 'claude.app', 'clau.de')):
            continue
        chain = connection.get('chains', [])
        selected = proxy_data.get('proxies', {}).get(GROUP, {}).get('now')
        http = proxy_data.get('proxies', {}).get(selected, {}).get('type', '').lower() == 'http'
        delegated = meta.get('type') == 'HTTPS' and not meta.get('destinationIP') and GROUP in chain and 'DIRECT' not in chain and http
        rows.append({'process': meta.get('process'), 'host': host, 'inbound': meta.get('type'),
                     'local_destination_ip_present': bool(meta.get('destinationIP')),
                     'domain_tunnel_observed': delegated})
    count = sum(row['domain_tunnel_observed'] for row in rows)
    return {'native_connections': rows, 'delegated_count': count,
            'path_statement': f'本次 {count}/{len(rows)} 条相关客户端连接观察到域名 HTTP 隧道，由住宅代理处理目标连接；不证明没有其他 DNS 预取或旁路' if rows else '未取得相关客户端连接，不能判断 DNS 路径',
            'dns_mode': dns.get('enhanced-mode'), 'upstreams': upstreams,
            'dns_certificate_warning': any(row['certificate_check_skipped'] for row in upstreams),
            'configuration_statement': '上游 DNS 证书校验存在跳过设置；这是配置风险，不等于已发生泄露' if any(row['certificate_check_skipped'] for row in upstreams) else '未在默认上游中发现跳过证书校验参数；其他上游和路径仍需另行核对'}


def summarize_capture(capture):
    records = capture.get('records', [])
    outgoing = [row for row in records if row.get('outbound') and not row.get('response')]
    if outgoing:
        return {'state': 'fail', 'value': '观察到相关域名的明文 DNS 出网尝试',
                'detail': '物理 IPv4 接口 IP 层记录到查询；无进程归属，不能自动断言由 Claude.exe 发出', 'query_count': len(outgoing)}
    calibrated = capture.get('calibration_query_seen') and capture.get('calibration_reply_seen')
    if capture.get('state') != 'complete' or not calibrated or capture.get('incomplete_count', 0):
        return {'state': 'unknown', 'value': '采样证据不足',
                'detail': '采样未完成、校准未确认或存在不完整数据；不判定为无泄露'}
    return {'state': 'info', 'value': '本次窗口未观察到相关明文 DNS 查询',
            'detail': f"仅限这次 {capture.get('seconds')} 秒 IPv4 采样，不能证明没有 DoH/DoT、IPv6、缓存命中或未来查询；不是完整 DNS 无泄露认证"}


def collect(capture_path=None):
    evidence = inspect_dns(load_config(), controller(connections=True), controller())
    evidence['checked'] = time.time()
    if capture_path:
        capture = json.loads(Path(capture_path).read_text(encoding='utf-8'))
        evidence['capture'] = capture
        evidence['capture_verdict'] = summarize_capture(capture)
    else:
        evidence['capture_verdict'] = {'state': 'unknown', 'value': '未执行物理接口采样', 'detail': '仅有代理连接证据，不能判断所有 DNS 旁路'}
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    target = DIRECTORY / 'evidence.json'
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(target)
    return evidence


def read_saved_evidence():
    """A historical diagnostic only. Never used to grant startup permission."""
    path = DIRECTORY / 'evidence.json'
    try:
        if path.stat().st_size > 262144:
            return None
        evidence = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(evidence, dict) or not isinstance(evidence.get('checked'), (int, float)):
            return None
        return evidence
    except (OSError, ValueError, TypeError):
        return None


def run_capture(output):
    """One explicit UI action -> OS consent -> bounded helper -> evidence report."""
    output = Path(output).resolve()
    if not output.is_relative_to(DIRECTORY.resolve()) or output.suffix != '.json':
        raise RuntimeError('采样输出目录不合法')
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, 'IPDESK_PYTHON': str(Path(sys.executable).with_name('python.exe')),
                   'IPDESK_DNS_HELPER': str(ROOT / 'dns_capture.py'), 'IPDESK_DNS_OUTPUT': str(output)}
    # Fixed code; paths are data in environment variables, never interpolated as code.
    script = '''$ErrorActionPreference='Stop';
    try {
      $arguments=@('-I','-S',('"'+$env:IPDESK_DNS_HELPER+'"'),'--output',('"'+$env:IPDESK_DNS_OUTPUT+'"'),'--seconds','40');
      $proc=Start-Process -FilePath $env:IPDESK_PYTHON -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -PassThru;
      $proc.WaitForExit(); exit $proc.ExitCode
    } catch { exit 1 }'''
    proc = subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                          env=environment, capture_output=True, timeout=100, creationflags=subprocess.CREATE_NO_WINDOW)
    if proc.returncode or not output.is_file():
        raise RuntimeError('管理员授权取消或采样未启动；未将其判为安全')
    return collect(output)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture')
    args = parser.parse_args()
    try:
        evidence = collect(args.capture)
        print(json.dumps({key: evidence[key] for key in ('delegated_count', 'path_statement', 'configuration_statement', 'capture_verdict')}, ensure_ascii=False))
    except Exception as exc:
        print(type(exc).__name__ + '：DNS 证据采集未完成，未输出原始配置')
        raise SystemExit(1)
