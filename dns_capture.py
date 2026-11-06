"""Short-lived IPv4 DNS observation. Standard library only; no config writes.

Run with isolated Python (-I -S). Requires Windows administrator permission for
SIO_RCVALL. Uses IP-level mode, not promiscuous NIC mode. No raw packets stored.
"""
import argparse
import ctypes
import ipaddress
import json
from pathlib import Path
import secrets
import select
import socket
import struct
import subprocess
import time

ROOT = Path(__file__).resolve().parent
TARGETS = ('claude.ai', 'claude.com', 'anthropic.com', 'claudeusercontent.com',
           'claudemcpcontent.com', 'claude.app', 'clau.de')


def related(name):
    name = name.lower().rstrip('.')
    return any(name == root or name.endswith('.' + root) for root in TARGETS)


def dns_question(data):
    if len(data) < 12:
        return None
    tx, flags, questions = struct.unpack('!HHH', data[:6])
    if questions != 1:
        return None
    pos, labels, seen = 12, [], set()
    for _ in range(128):
        if pos >= len(data) or pos in seen:
            return None
        seen.add(pos)
        length = data[pos]
        if length & 0xC0 == 0xC0:
            if pos + 1 >= len(data):
                return None
            pos = ((length & 63) << 8) | data[pos + 1]
            continue
        if length == 0:
            return dict(id=tx, response=bool(flags & 0x8000), name='.'.join(labels).lower())
        if length > 63 or pos + 1 + length > len(data):
            return None
        try:
            labels.append(data[pos + 1:pos + 1 + length].decode('ascii'))
        except UnicodeDecodeError:
            return None
        pos += 1 + length
    return None


def decode_packet(data):
    if len(data) < 20 or data[0] >> 4 != 4:
        return None
    ihl = (data[0] & 15) * 4
    total = int.from_bytes(data[2:4], 'big')
    if ihl < 20 or len(data) < total or total < ihl + 8:
        return None
    data = data[:total]
    if int.from_bytes(data[6:8], 'big') & 0x3FFF:
        return {'incomplete': True}
    protocol = data[9]
    source, destination = socket.inet_ntoa(data[12:16]), socket.inet_ntoa(data[16:20])
    if protocol not in (6, 17):
        return None
    sport, dport = struct.unpack('!HH', data[ihl:ihl + 4])
    result = {'source': source, 'destination': destination, 'sport': sport, 'dport': dport}
    if 853 in (sport, dport):
        return dict(result, encrypted_dns_port=True)
    if 53 not in (sport, dport):
        return None
    if protocol == 17:
        payload = data[ihl + 8:]
    else:
        if total < ihl + 20:
            return dict(result, incomplete=True)
        offset = (data[ihl + 12] >> 4) * 4
        if offset < 20:
            return dict(result, incomplete=True)
        payload = data[ihl + offset:]
        if len(payload) < 2:
            return dict(result, tcp_no_payload=True)
        length = int.from_bytes(payload[:2], 'big')
        if len(payload) < length + 2:
            return dict(result, incomplete=True)
        payload = payload[2:2 + length]
    question = dns_question(payload)
    return dict(result, **question) if question else dict(result, incomplete=True)


def query_bytes(name):
    labels = b''.join(bytes([len(p)]) + p.encode('ascii') for p in name.split('.')) + b'\0'
    return struct.pack('!HHHHHH', secrets.randbelow(65536), 0x0100, 1, 0, 0, 0) + labels + b'\0\x01\0\x01'


def physical_interfaces():
    script = r'''$items=@(); Get-NetAdapter -Physical | Where-Object Status -eq Up | ForEach-Object {
    $a=$_; Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressState Preferred -ErrorAction SilentlyContinue | ForEach-Object {
        $items+=@{index=$a.ifIndex;address=$_.IPAddress;family=[string]$_.AddressFamily}
    }}; ConvertTo-Json -InputObject @($items) -Compress'''
    process = subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                             capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    return json.loads(process.stdout.decode('utf-8-sig'))


def save(path, report):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def capture(output, seconds):
    report = {'state': 'starting', 'started': time.time(), 'seconds': seconds, 'records': [],
              'calibration_query_seen': False, 'calibration_reply_seen': False,
              'packet_count': 0, 'dns_packet_count': 0, 'incomplete_count': 0, 'dot_packet_count': 0,
              'limits': ['IPv4 物理接口 IP 层短时采样，不是全流量或永久保证',
                         '无进程归属；相关域名查询不自动归因于 Claude.exe',
                         '不解密 DoH/DoT，不覆盖浏览器完整登录或未来请求',
                         'TCP DNS 不做跨段重组，缺少校准或出现不完整数据时不能判无泄露']}
    save(output, report)
    sockets, controls = [], []
    try:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            report.update(state='needs_admin', reason='Windows 要求管理员权限才能观察物理接口')
            return
        interfaces = physical_interfaces()
        report['ipv6_addresses_present'] = any(row['family'] == 'IPv6' and ipaddress.ip_address(row['address'].split('%')[0]).is_global for row in interfaces)
        control = 'ipdesk-' + secrets.token_hex(8) + '.invalid'
        report['calibration'] = '仅对无敏感含义的随机 .invalid 域名发一次直连 DNS 校准；不计入 Claude 查询'
        report['interfaces'] = []
        for interface in interfaces:
            if interface['family'] != 'IPv4' or ipaddress.ip_address(interface['address']).is_link_local:
                continue
            sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
            try:
                sock.bind((interface['address'], 0))
                sock.ioctl(socket.SIO_RCVALL, 3)  # RCVALL_IPLEVEL; no promiscuous mode
                sock.setblocking(False)
            except Exception:
                sock.close()
                raise
            sockets.append(sock)
            report['interfaces'].append({'index': interface['index'], 'address': interface['address']})
        if not sockets:
            raise RuntimeError('没有可观察的物理 IPv4 接口')
        local = {row['address'] for row in report['interfaces']}
        # Bind one non-sensitive calibration request to each physical interface.
        for interface in report['interfaces']:
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            controls.append(udp)
            udp.bind((interface['address'], 0))
            udp.setsockopt(socket.IPPROTO_IP, 31, struct.pack('!I', interface['index']))  # IP_UNICAST_IF
            udp.sendto(query_bytes(control), ('223.5.5.5', 53))
        report['state'] = 'capturing'
        save(output, report)
        deadline, last_save = time.monotonic() + seconds, time.monotonic()
        while time.monotonic() < deadline:
            ready, _, _ = select.select(sockets, [], [], min(.5, max(0, deadline - time.monotonic())))
            for sock in ready:
                packet = sock.recv(65535)
                report['packet_count'] += 1
                decoded = decode_packet(packet)
                if not decoded:
                    continue
                if decoded.get('incomplete'):
                    report['incomplete_count'] += 1
                    continue
                if decoded.get('encrypted_dns_port'):
                    report['dot_packet_count'] += 1
                    continue
                if not decoded.get('name'):
                    continue
                report['dns_packet_count'] += 1
                if decoded['name'] == control:
                    report['calibration_reply_seen' if decoded['response'] else 'calibration_query_seen'] = True
                    continue
                if related(decoded['name']) and len(report['records']) < 200:
                    decoded['at'] = time.time()
                    decoded['outbound'] = decoded['source'] in local
                    report['records'].append(decoded)
            if time.monotonic() - last_save > 3:
                save(output, report)
                last_save = time.monotonic()
        report['state'] = 'complete'
    except Exception as exc:
        report.update(state='error', reason=type(exc).__name__ + '：采样未完成')
    finally:
        for sock in sockets:
            try:
                sock.ioctl(socket.SIO_RCVALL, 0)
            except OSError:
                pass
            sock.close()
        for sock in controls:
            sock.close()
        report['finished'] = time.time()
        save(output, report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--seconds', type=int, default=40)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if not output.is_relative_to((ROOT / 'reports' / 'dns-audit').resolve()) or output.suffix != '.json':
        raise SystemExit('输出位置必须在本项目 DNS 采样目录内')
    if not output.parent.is_dir() or not 5 <= args.seconds <= 60:
        raise SystemExit('采样目录或时间不合法')
    capture(output, args.seconds)
