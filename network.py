import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import winreg

import requests
from diagnostics import environment_diagnostics, run_probes

ROOT = Path(__file__).resolve().parent
EXPECTED_IP = '161.77.11.188'
GROUP = '🛡️ 纯净住宅IP'
PROCESS_PATTERN = r'(?i)^claude(?:-code)?\.exe$'
PATH_PATTERN = r'(?i)[\\/](?:AnthropicClaude|Claude)(?:_[^\\/]+)?[\\/]'
DOMAINS = ['claude.ai', 'claude.com', 'anthropic.com', 'claudeusercontent.com',
           'claudemcpcontent.com', 'claude.app', 'clau.de']
HIDDEN = subprocess.CREATE_NO_WINDOW


def result(state, value, detail='', **extra):
    return dict(state=state, value=value, detail=detail, **extra)


def settings():
    try:
        data = json.loads((ROOT / 'settings.json').read_text(encoding='utf-8'))
        ipaddress.IPv4Address(data['expected_ip'])
        return data
    except (OSError, ValueError, KeyError):
        return {'expected_ip': EXPECTED_IP}


def save_settings(ip):
    ipaddress.IPv4Address(ip)
    target = ROOT / 'settings.json'
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps({'expected_ip': ip}, indent=2), encoding='utf-8')
    temp.replace(target)


def python_exe():
    # pythonw does not reliably provide stdout pipes on every Python distribution.
    sibling = Path(sys.executable).with_name('python.exe')
    return str(sibling if sibling.exists() else Path(sys.executable))


def controller(connections=False):
    try:
        proc = subprocess.run([python_exe(), str(ROOT / 'controller_helper.py')] +
                              (['--connections'] if connections else []),
                              capture_output=True, timeout=14, creationflags=HIDDEN)
        data = json.loads(proc.stdout.decode('utf-8'))
        if not data['ok']:
            raise RuntimeError(data.get('error', '代理状态读取失败'))
        return data['data']
    except subprocess.TimeoutExpired:
        raise RuntimeError('读取代理状态超时') from None
    except (ValueError, KeyError):
        raise RuntimeError('无法读取代理实时状态') from None


def expected_guard_rules():
    rules = []
    for typ, pattern in [('ProcessNameRegex', PROCESS_PATTERN), ('ProcessPathRegex', PATH_PATTERN)]:
        for cidr in ['127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16', '::1/128']:
            rules.append({'type': 'AND', 'payload': f'(({typ},{pattern}) && (IPCIDR,{cidr}))', 'proxy': 'DIRECT'})
        rules.append({'type': 'AND', 'payload': f'(({typ},{pattern}) && (Network,udp))', 'proxy': 'REJECT'})
        rules.append({'type': typ, 'payload': pattern, 'proxy': GROUP})
    for domain in DOMAINS:
        rules.append({'type': 'AND', 'payload': f'((DomainSuffix,{domain}) && (Network,udp))', 'proxy': 'REJECT'})
        rules.append({'type': 'DomainSuffix', 'payload': domain, 'proxy': GROUP})
    return rules


def guard_rule_order(rules):
    """Conservative precedence proof, permitting only known-safe interleaving.

    Local DIRECT rules and already satisfied duplicates cannot alter the covered
    external routes. Other predecessors remain unverified, rather than guessed.
    """
    expected = expected_guard_rules()
    position = 0
    local_networks = [ipaddress.ip_network(n) for n in
                      ['127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16', '::1/128']]
    for original in rules:
        if (original.get('extra') or {}).get('disabled'):
            continue
        rule = {key: original.get(key) for key in ('type', 'payload', 'proxy')}
        if rule == expected[position]:
            position += 1
            if position == len(expected):
                return True
            continue
        if rule in expected[:position]:
            continue
        if rule['type'] in ('IPCIDR', 'IPCIDR6') and rule['proxy'] == 'DIRECT':
            try:
                network = ipaddress.ip_network(rule['payload'])
                if any(network.version == local.version and network.subnet_of(local) for local in local_networks):
                    continue
            except ValueError:
                pass
        return False
    return False


def validate_guard(snapshot):
    cfg = snapshot['config']
    reasons = []
    if cfg.get('mode') != 'rule':
        reasons.append('代理未处于规则模式')
    if not cfg.get('tun', {}).get('enable'):
        reasons.append('TUN 未开启，无法核验系统接管')
    if cfg.get('find-process-mode') not in ('always', 'strict'):
        reasons.append('进程规则识别未确认')
    if cfg.get('ipv6') is not False:
        reasons.append('当前 IPv6 设置与本方案不一致')
    proxies = snapshot.get('proxies', {})
    group = proxies.get(GROUP, {})
    members = group.get('all', [])
    if len(members) != 1 or members[0] in ('DIRECT', 'REJECT') or group.get('now') != members[0]:
        reasons.append('固定出口组并非锁定唯一代理')
    elif proxies.get(members[0], {}).get('type', '').lower() != 'http':
        reasons.append('固定出口节点类型已变更，需要重新核验')
    rules = snapshot.get('rules', [])
    expected = expected_guard_rules()
    if not guard_rule_order(rules):
        reasons.append('尚未核验完整客户端规则（进程、域名、UDP、本地例外及优先级）；不自动修改配置')
    if reasons:
        return result('fail', '规则需要处理', '；'.join(reasons))
    return result('pass', '固定出口规则已生效',
                  '规则模式 · TUN 开启 · 客户端进程与域名规则 · 单一 HTTP 出口 · Claude 公网 UDP 阻断')


def client_evidence(snapshot):
    """Read-only evidence, independent of browser fingerprints or public scores."""
    cfg = snapshot['config']
    active = [{k: r.get(k) for k in ('type', 'payload', 'proxy')}
              for r in snapshot.get('rules', []) if not (r.get('extra') or {}).get('disabled')]
    expected = expected_guard_rules()
    categories = [
        ('进程及子进程规则', [r for r in expected if r['type'] in ('ProcessNameRegex', 'ProcessPathRegex')]),
        ('Claude 域名分流', [r for r in expected if r['type'] == 'DomainSuffix']),
        ('客户端公网 UDP 规则', [r for r in expected if r['proxy'] == 'REJECT']),
    ]
    rows = []
    for label, rules in categories:
        count = sum(rule in active for rule in rules)
        rows.append({'label': label, 'state': 'pass' if count == len(rules) else 'unknown',
                     'detail': f'已识别 {count}/{len(rules)} 条预期规则；整体优先级另行核验'})
    rows.insert(0, {'label': '系统接管', 'state': 'pass' if cfg.get('mode') == 'rule' and cfg.get('tun', {}).get('enable') else 'fail',
                   'detail': '规则模式 / TUN ' + ('开启' if cfg.get('tun', {}).get('enable') else '未开启')})
    rows.append({'label': 'IPv6 旁路', 'state': 'unknown',
                 'detail': ('代理 IPv6 已关闭' if cfg.get('ipv6') is False else '代理 IPv6 未关闭') + '；尚未实测客户端是否存在系统 IPv6 旁路'})
    rows.append({'label': '代理退出后的断线阻断', 'state': 'unknown', 'detail': '未部署、未测试；当前只有检测和提示，不会强制切断客户端网络'})
    rows.append({'label': '浏览器登录回跳', 'state': 'unknown', 'detail': '需在实际登录时核验；启动前无法证明整个授权流程已通过'})
    return rows


def request_text(url, proxy_port=None):
    session = requests.Session()
    session.trust_env = False
    proxies = {'http': f'http://127.0.0.1:{proxy_port}', 'https': f'http://127.0.0.1:{proxy_port}'} if proxy_port else {}
    started = time.monotonic()
    try:
        response = session.get(url, proxies=proxies, timeout=(8, 12), allow_redirects=False,
                               headers={'User-Agent': 'IPDesk/1.0', 'Cache-Control': 'no-cache'})
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError(f'HTTP {response.status_code}，未获得检测数据')
        return response.text, round((time.monotonic() - started) * 1000)
    finally:
        session.close()


def parse_trace(text):
    values = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
    ip = values.get('ip', '')
    ipaddress.ip_address(ip)
    return ip, values.get('loc', '未知')


def trace_probe(expected, port, system=False, generic=False):
    url = 'https://www.cloudflare.com/cdn-cgi/trace' if generic else 'https://claude.ai/cdn-cgi/trace'
    scope = '检测工具 → 系统网络 / TUN → Claude' if system else '检测工具 → 本地代理 → ' + ('Cloudflare' if generic else 'Claude')
    try:
        try:
            text, ms = request_text(url, None if system else port)
        except requests.Timeout:
            # Retry a transient CONNECT/trace timeout once, never reuse old success.
            text, ms = request_text(url, None if system else port)
        ip, country = parse_trace(text)
        state = 'info' if generic else ('pass' if ip == expected else 'fail')
        return result(state, ip, f'{country} · {ms} ms · {scope}', ip=ip, ms=ms, country=country)
    except Exception as exc:
        # A request error is not evidence of a leak or a successful test.
        detail = '连接超时' if isinstance(exc, requests.Timeout) else '未取得有效结果（' + type(exc).__name__ + '）'
        return result('unknown', '未确认', detail + '；' + scope, error=type(exc).__name__)


def geo_probe(ip, port):
    # The geography endpoint is independent of its risk-score endpoint. Some risk
    # responses describe another address in the same /24, so do not use that score.
    try:
        text, _ = request_text('https://ip.net.coffee/api/geoip/' + ip, port)
        data = json.loads(text)
        if data.get('ip', ip) != ip or not data.get('country'):
            raise ValueError('归属查询未返回匹配结果')
        location = ' / '.join(str(data.get(k, '')) for k in ['country_code', 'region', 'city'] if data.get(k))
        return result('info', location, '来源 ip.net.coffee · 查询 ' + ip + '\n' +
                      str(data.get('isp') or 'ISP 未提供') + '\n归属为第三方数据，不生成估算信誉分。', geo=data)
    except Exception:
        pass
    try:
        text, _ = request_text('https://api.ipapi.is/?q=' + ip, port)
        data = json.loads(text)
        if data.get('ip') != ip or data.get('error'):
            raise ValueError('IP 查询结果不匹配')
        location = data.get('location') or {}
        company = data.get('company') or {}
        asn = data.get('asn') or {}
        value = ' / '.join(str(x) for x in [location.get('country_code'), location.get('state'), location.get('city')] if x) or '归属未提供'
        attributes = []
        for key, title in [('is_datacenter', '机房'), ('is_proxy', '代理'), ('is_vpn', 'VPN'), ('is_tor', 'Tor')]:
            attributes.append(title + '：' + ('是' if data.get(key) is True else '否' if data.get(key) is False else '未知'))
        return result('info', value, f'来源 api.ipapi.is · {company.get("name") or asn.get("org") or "ISP 未提供"}\n' + ' / '.join(attributes) + '\n第三方分类，仅供参考；不生成估算信誉分。')
    except Exception:
        return result('unknown', '归属信息未确认', '第三方查询未返回有效数据；不补默认分数。')


def native_claude():
    script = r'''
$items=@(); Get-AppxPackage *Claude* | ForEach-Object {
  $p=$_; $m=$p|Get-AppxPackageManifest
  foreach($a in $m.Package.Applications.Application){
    if([string]$a.Executable -match '(?i)claude\.exe$'){
      $items += @{kind='aumid';target=($p.PackageFamilyName+'!'+$a.Id);label=('Claude '+$p.Version)}
    }
  }
}; ConvertTo-Json -InputObject @($items) -Compress
'''
    try:
        raw = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                             capture_output=True, timeout=12, creationflags=HIDDEN)
        items = json.loads(raw.stdout.decode('utf-8-sig'))
        if items:
            return items[0]
    except Exception:
        pass
    local = Path(os.environ['LOCALAPPDATA'])
    paths = [local / 'AnthropicClaude' / 'claude.exe', local / 'Programs' / 'Claude' / 'Claude.exe']
    paths.extend(sorted((local / 'AnthropicClaude').glob('app-*/claude.exe'), key=lambda p: p.stat().st_mtime, reverse=True))
    paths.extend(sorted((local / 'Claude').glob('app-*/claude.exe'), key=lambda p: p.stat().st_mtime, reverse=True))
    for path in paths:
        if path.is_file():
            return {'kind': 'exe', 'target': str(path), 'label': 'Claude 客户端'}
    return None


def system_proxy():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
            enabled = winreg.QueryValueEx(key, 'ProxyEnable')[0]
            return '系统代理：' + ('已开启' if enabled else '未开启')
    except OSError:
        return '系统代理状态未确认'


def local_file_port():
    try:
        from controller_helper import load_config
        port = int(load_config().get('mixed-port', 7897))
        return port if 0 < port < 65536 else 7897
    except Exception:
        return 7897


def run_checks(expected, emit=lambda key, value: None):
    ipaddress.IPv4Address(expected)
    report = {'expected_ip': expected, 'started': time.time(), 'checks': {}}
    report['environment'] = environment_diagnostics()
    def add(key, value):
        report['checks'][key] = value
        emit(key, value)
    def stage(step, label):
        emit('progress', {'step': step, 'label': label})

    stage('client', '识别本机客户端')
    try:
        native = native_claude()
    except Exception:
        native = None
    report['native'] = native
    add('client', result('info' if native else 'unknown', '已安装' if native else '未发现原生客户端',
                         native['label'] if native else '请安装官方 Claude 客户端后重新检查'))

    stage('guard', '核对代理与专用规则')
    try:
        snap = controller()
        port = int(snap['config']['mixed-port'])
        add('guard', validate_guard(snap))
        report['client_evidence'] = client_evidence(snap)
        report['runtime'] = {'mode': snap['config'].get('mode'), 'tun': snap['config'].get('tun', {}).get('enable'),
                             'ipv6': snap['config'].get('ipv6'), 'mixed_port': port}
    except Exception as exc:
        port = local_file_port()
        add('guard', result('unknown', '实时配置未确认', type(exc).__name__ + '：未取得运行证据'))

    stage('exit', '实测两条 Claude 出口路径')
    add('claude', trace_probe(expected, port))
    add('system', trace_probe(expected, port, system=True))

    stage('tls', '验证证书与连接')
    # These are real sequential stages. No presentation delay or fabricated pass.
    for key, probe in run_probes(expected, port).items():
        add(key, probe)
    for evidence in report.get('client_evidence', []):
        if evidence['label'] == 'IPv6 旁路':
            probe = report['checks'].get('ipv6', {})
            evidence.update(state=probe.get('state', 'unknown'), detail=probe.get('detail', '未完成'))
    report['finished'] = time.time()
    report['system_proxy'] = system_proxy()
    report['limitations'] = ['这是检测工具的请求结果；客户端连接以实时观察记录为准。',
                             'DNS / WebRTC 泄露未实测，不能据此显示无泄露。',
                             'IPv6 系统出口为参考，失败不证明无旁路，也不作为客户端已泄露的证据。',
                             '代理退出后的系统级断网保护未部署。']
    return report


def startup_rows(report, expected, now=None):
    now = time.time() if now is None else now
    checks = (report or {}).get('checks', {})
    fresh = bool(report and report.get('expected_ip') == expected and 0 <= now - report.get('finished', 0) <= 60)
    rows = [{'label': label, 'state': checks.get(key, {}).get('state', 'unknown')}
            for key, label in [('claude', '代理请求出口一致'), ('system', '系统请求出口一致'),
                               ('guard', '客户端规则覆盖'), ('tls', '证书与 Claude 服务链路')]]
    rows += [{'label': '原生客户端安装', 'state': 'pass' if (report or {}).get('native') else 'unknown'},
             {'label': '本次预检有效期（60 秒）', 'state': 'pass' if fresh else 'expired' if report else 'unknown'}]
    return rows


def gate(report, expected, now=None):
    now = time.time() if now is None else now
    if not report or report.get('expected_ip') != expected:
        return False, '请先检测当前目标 IP'
    if now - report.get('finished', 0) > 60 or now < report.get('finished', 0):
        return False, '检测结果已过期，请重新检测'
    if any(row['state'] != 'pass' for row in startup_rows(report, expected, now)):
        return False, '出口、客户端规则或证书链路未全部确认，已停止启动'
    return True, '固定出口预检通过'


def classify_connections(data):
    import re
    connections = []
    for conn in data.get('connections', []):
        meta = conn.get('metadata') or {}
        process = meta.get('process', '')
        path = meta.get('processPath', '')
        if not (re.search(PROCESS_PATTERN, process) or re.search(r'(?i)[\\/](?:AnthropicClaude|Claude)(?:_[^\\/]+)?[\\/]', path)):
            continue
        chains = conn.get('chains') or []
        destination = meta.get('destinationIP', '')
        local = False
        try:
            address = ipaddress.ip_address(destination)
            local = any(address in ipaddress.ip_network(net) for net in ['127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16', '::1/128'] if address.version == ipaddress.ip_network(net).version)
        except ValueError:
            pass
        state = 'local' if local and 'DIRECT' in chains else 'pass' if GROUP in chains and 'DIRECT' not in chains else 'fail'
        connections.append({'id': conn.get('id'), 'process': process or 'Claude 子进程',
                            'host': meta.get('host') or destination, 'chains': chains, 'state': state,
                            'rule': conn.get('rule'), 'observed': time.time()})
    return connections


def save_report(report):
    directory = ROOT / 'reports'
    directory.mkdir(exist_ok=True)
    target = directory / 'last-check.json'
    # Native installation path and AUMID are not needed in an exported network report.
    clean = {k: v for k, v in report.items() if k != 'native'}
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(target)
    return target


if __name__ == '__main__':
    report = run_checks(settings()['expected_ip'])
    path = save_report(report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
