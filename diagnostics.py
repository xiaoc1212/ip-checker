"""Read-only probes. No account credentials, configuration writes or TLS bypass."""
import concurrent.futures
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import time
from urllib.parse import urlsplit
import winreg


def item(state, value, detail):
    return dict(state=state, value=value, detail=detail)


def environment_diagnostics(environ=None):
    environ = os.environ if environ is None else environ
    rows = []
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'NODE_EXTRA_CA_CERTS', 'SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE'):
        value = environ.get(name) or environ.get(name.lower())
        if not value:
            continue
        detail = '已设置；具体值已隐藏，客户端是否继承需要另行确认'
        if name.endswith('_PROXY') and name != 'NO_PROXY':
            try:
                parsed = urlsplit(value)
                valid = parsed.scheme in ('http', 'https', 'socks5', 'socks5h') and bool(parsed.hostname) and bool(parsed.port)
                detail = ('已设置 ' + parsed.scheme + ' 代理' if valid else '格式或端口需人工检查') + '；地址与凭据已隐藏'
            except ValueError:
                detail = '格式需人工检查；具体值已隐藏'
        elif name == 'NO_PROXY':
            detail = '存在代理绕过配置；不读取或展示其中的地址，需核验是否影响 Claude'
        else:
            detail = '存在自定义证书路径；不会关闭证书验证，也不能证明客户端会采用该路径'
        rows.append(dict(label=name, **item('info', '已配置', detail)))
    if not rows:
        rows.append(dict(label='进程环境变量', **item('info', '未配置上述代理或证书变量', '仅代表检测器进程；不代表已运行的 Claude 或商店启动器环境')))
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
            def read(name):
                try: return winreg.QueryValueEx(key, name)[0]
                except OSError: return None
            enabled, pac = bool(read('ProxyEnable')), bool(read('AutoConfigURL'))
        rows.append(dict(label='Windows 系统代理', **item('info', '开启' if enabled else '关闭', '自动配置脚本（PAC）：' + ('已设置' if pac else '未设置') + '；TUN 和具体进程的代理路径另行实测')))
    except OSError:
        rows.append(dict(label='Windows 系统代理', **item('unknown', '未取得', '注册表读取失败，不推断为代理关闭')))
    return rows


def tls_probe(expected, port):
    started = time.monotonic()
    context = ssl.create_default_context()
    context.set_alpn_protocols(['http/1.1'])
    connection = http.client.HTTPSConnection('127.0.0.1', port, timeout=5, context=context)
    connection.set_tunnel('claude.ai', 443)
    try:
        connection.connect()
        sock = connection.sock
        cert = sock.getpeercert()
        issuer = next((v for group in cert.get('issuer', []) for k, v in group if k == 'commonName'), '未提供')
        protocol = sock.version()
        connection.request('GET', '/cdn-cgi/trace', headers={'Host': 'claude.ai', 'Connection': 'close'})
        response = connection.getresponse()
        body = response.read(32768).decode('utf-8', errors='replace')
        if response.status != 200:
            return item('unknown', 'TLS 已建立，服务响应待确认', f'HTTP {response.status}；不把状态码解释为账号风控')
        values = dict(line.split('=', 1) for line in body.splitlines() if '=' in line)
        address = str(ipaddress.ip_address(values.get('ip', '')))
        return item('pass' if address == expected else 'fail', '证书验证通过' if address == expected else '出口与目标不一致',
                    f'{protocol} · 签发者 {issuer} · 出口 {address} · {round((time.monotonic()-started)*1000)} ms；仅代表检测器的 Python 信任库与此路径')
    except ssl.SSLCertVerificationError:
        return item('fail', '证书验证失败', '未跳过验证；需核对证书信任、系统时间或中间代理，不自动判定为攻击')
    except (TimeoutError, socket.timeout):
        return item('unknown', 'TLS / 连接超时', '未取得有效证据，不推断为服务封禁')
    except Exception as exc:
        return item('unknown', 'TLS / 服务探测未完成', type(exc).__name__ + '；未记录可能含代理信息的原始异常')
    finally:
        connection.close()


def ipv6_probe():
    host = 'www.cloudflare.com'
    try:
        answers = socket.getaddrinfo(host, 443, socket.AF_INET6, socket.SOCK_STREAM)
        for answer in answers[:2]:
            connection = http.client.HTTPSConnection(host, timeout=5)
            try:
                raw = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                raw.settimeout(5)
                try:
                    raw.connect(answer[4])
                    connection.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
                except Exception:
                    raw.close()
                    raise
                connection.request('GET', '/cdn-cgi/trace', headers={'Connection': 'close'})
                response = connection.getresponse()
                if response.status != 200:
                    continue
                text = response.read(32768).decode('utf-8', errors='replace')
                values = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
                address = ipaddress.ip_address(values.get('ip', ''))
                if address.version == 6 and address.is_global:
                    return item('info', str(address), '观察到检测器的系统 IPv6 出口；不是 Claude 泄露证明，不单独阻止启动。客户端 IPv6 连接需单独观察')
            except (OSError, ValueError):
                continue
            finally:
                connection.close()
    except OSError:
        pass
    return item('unknown', 'IPv6 未确认', '本次强制 IPv6 请求无有效结果；不能由此证明客户端没有 IPv6 旁路')


def dns_probe():
    try:
        answers = socket.getaddrinfo('claude.ai', 443, type=socket.SOCK_STREAM)
        families = {answer[0] for answer in answers}
        return item('info', '系统解析成功', 'A：' + str(socket.AF_INET in families) + ' / AAAA：' + str(socket.AF_INET6 in families) + '；只证明检测器能解析，不证明客户端 DNS 无泄露')
    except OSError:
        return item('unknown', '系统解析失败', '未取得结果；代理远端解析与客户端行为可能不同')


def run_probes(expected, port):
    # Bound total runtime, including Windows DNS calls that have no socket timeout.
    try:
        executable = Path(sys.executable).with_name('python.exe')
        proc = subprocess.run([str(executable), str(Path(__file__).resolve()), expected, str(port)],
                              capture_output=True, timeout=18, creationflags=subprocess.CREATE_NO_WINDOW)
        data = json.loads(proc.stdout.decode('utf-8'))
        if not all(isinstance(data.get(k), dict) and data[k].get('state') in ('pass', 'fail', 'info', 'unknown') for k in ('tls', 'ipv6', 'dns')):
            raise ValueError()
        return data
    except Exception:
        return {key: item('unknown', '探测超时或未完成', '已限制探测总时长，未将失败当作通过') for key in ('tls', 'ipv6', 'dns')}


if __name__ == '__main__':
    expected, port = str(ipaddress.IPv4Address(sys.argv[1])), int(sys.argv[2])
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {'tls': pool.submit(tls_probe, expected, port), 'ipv6': pool.submit(ipv6_probe), 'dns': pool.submit(dns_probe)}
        print(json.dumps({key: future.result() for key, future in jobs.items()}, ensure_ascii=True))
