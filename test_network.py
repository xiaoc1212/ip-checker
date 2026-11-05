import copy
import json
import time
from unittest.mock import patch

import pytest
import requests

from configure import transform, apply
from network import (DOMAINS, GROUP, PROCESS_PATTERN, classify_connections, gate, geo_probe,
                     parse_trace, trace_probe, validate_guard, expected_guard_rules, request_text)


def runtime():
    return {'config': {'mode': 'rule', 'tun': {'enable': True, 'strict-route': True}, 'find-process-mode': 'always', 'ipv6': False},
            'proxies': {GROUP: {'all': ['ISP'], 'now': 'ISP'}, 'ISP': {'type': 'Http'}},
            'rules': expected_guard_rules()}


def test_trace_rejects_html_missing_or_invalid_ip():
    for body in ['<html>Access denied</html>', 'loc=US', 'ip=not-an-ip']:
        with pytest.raises(ValueError): parse_trace(body)
    assert parse_trace('ip=161.77.11.188\nloc=US')[0] == '161.77.11.188'


def test_requests_response_and_redirect_are_handled():
    response = requests.Response()
    response.status_code = 200
    response._content = b'ip=161.77.11.188\nloc=US'
    with patch('requests.Session.get', return_value=response):
        assert request_text('https://claude.ai/cdn-cgi/trace', 7897)[0].startswith('ip=')
        response.status_code = 302
        with pytest.raises(ValueError): request_text('https://claude.ai/cdn-cgi/trace', 7897)


def test_network_timeout_is_unknown_not_pass():
    with patch('network.request_text', side_effect=requests.Timeout):
        assert trace_probe('161.77.11.188', 7897)['state'] == 'unknown'


def test_wrong_exit_fails_and_generic_exit_is_not_a_failure():
    with patch('network.request_text', return_value=('ip=203.0.113.1\nloc=US', 20)):
        assert trace_probe('161.77.11.188', 7897)['state'] == 'fail'
        assert trace_probe('161.77.11.188', 7897, generic=True)['state'] == 'info'


def test_geo_failure_never_fabricates_score_or_success():
    with patch('network.request_text', side_effect=ValueError):
        value = geo_probe('161.77.11.188', 7897)
        assert value['state'] == 'unknown'
        assert '97' not in json.dumps(value)


def test_geo_rejects_a_different_address():
    with patch('network.request_text', return_value=(json.dumps({'ip': '161.77.11.85', 'country': 'US', 'trust_score': 97}), 20)):
        assert geo_probe('161.77.11.188', 7897)['state'] == 'unknown'


@pytest.mark.parametrize('change', ['direct_fallback', 'global', 'tun_off', 'process_missing', 'domain_missing', 'disabled', 'udp_missing', 'ipv6_on', 'priority', 'path_missing', 'domain_udp_missing'])
def test_guard_detects_route_drift(change):
    data = runtime()
    assert validate_guard(data)['state'] == 'pass'
    if change == 'direct_fallback': data['proxies'][GROUP]['all'].append('DIRECT')
    if change == 'global': data['config']['mode'] = 'global'
    if change == 'tun_off': data['config']['tun']['enable'] = False
    if change == 'process_missing': data['rules'].pop(7)
    if change == 'domain_missing': data['rules'].pop()
    if change == 'disabled': data['rules'][1]['extra'] = {'disabled': True}
    if change == 'udp_missing': data['rules'].pop(6)
    if change == 'ipv6_on': data['config']['ipv6'] = True
    if change == 'priority': data['rules'].insert(0, {'type': 'Match', 'payload': '', 'proxy': 'DIRECT'})
    if change == 'path_missing': data['rules'].pop(15)
    if change == 'domain_udp_missing': data['rules'].pop(16)
    assert validate_guard(data)['state'] == 'fail'


def test_gate_requires_fresh_complete_matching_result():
    report = {'expected_ip': '161.77.11.188', 'finished': 100, 'checks': {k: {'state': 'pass'} for k in ['guard', 'claude', 'system']}}
    assert gate(report, '161.77.11.188', now=110)[0]
    assert not gate(report, '161.77.11.188', now=161)[0]
    assert not gate(report, '161.77.11.189', now=110)[0]
    report['checks']['system']['state'] = 'unknown'
    assert not gate(report, '161.77.11.188', now=110)[0]


def test_connection_observer_does_not_call_browser_traffic_native():
    def conn(process, chain, ip='104.18.1.1'):
        return {'id': process, 'metadata': {'process': process, 'host': 'claude.ai', 'destinationIP': ip}, 'chains': chain}
    rows = classify_connections({'connections': [conn('chrome.exe', [GROUP]), conn('Claude.exe', [GROUP]),
                                                 conn('claude-code.exe', ['DIRECT'])]})
    assert len(rows) == 2
    assert [r['state'] for r in rows] == ['pass', 'fail']
    assert classify_connections({'connections': [conn('Claude.exe', ['DIRECT'], '127.0.0.1')]})[0]['state'] == 'local'


def test_persistent_rules_are_idempotent_and_preserve_unrelated_routes():
    config = {'proxies': [{'name': 'ISP', 'type': 'http', 'server': '192.0.2.1', 'port': 12323}],
              'proxy-groups': [{'name': GROUP, 'type': 'select', 'proxies': ['ISP']}],
              'rules': ['PROCESS-NAME,YY.exe,DIRECT', 'DOMAIN-SUFFIX,bilibili.com,DIRECT', 'MATCH,Other'],
              'tun': {'enable': True, 'stack': 'gvisor'}, 'dns': {'nameserver': ['https://example.com/dns-query']}}
    once = transform(copy.deepcopy(config))
    twice = transform(copy.deepcopy(once))
    assert once == twice
    assert once['rules'][-3:] == config['rules']
    assert once['tun'] == config['tun']
    assert once['dns'] == config['dns']
    assert not any('PROCESS-NAME,node.exe' in r for r in once['rules'])


def test_persistent_guard_rejects_fallback_group():
    with pytest.raises(RuntimeError):
        transform({'proxy-groups': [{'name': GROUP, 'proxies': ['ISP', 'DIRECT']}]})


def test_strict_tun_route_is_not_required_for_client_preflight():
    data = runtime()
    data['config']['tun']['strict-route'] = False
    data['config']['find-process-mode'] = 'strict'
    assert validate_guard(data)['state'] == 'pass'


def test_retired_apply_cannot_touch_clash():
    with patch('configure.snapshot') as snapshot:
        with pytest.raises(RuntimeError, match='旧版自动配置已停用'):
            apply()
        snapshot.assert_not_called()

def test_ipv6_dns_failure_is_inconclusive():
    from network import ipv6_probe
    import socket
    with patch('network.socket.getaddrinfo', side_effect=socket.gaierror()):
        check = ipv6_probe()
    assert check['state'] == 'unknown'


def test_ipv6_empty_dns_is_inconclusive():
    from network import ipv6_probe
    with patch('network.socket.getaddrinfo', return_value=[]):
        assert ipv6_probe()['state'] == 'unknown'


def test_ipv6_connection_failure_is_inconclusive():
    from network import ipv6_probe
    import socket
    answer = [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2606:4700::1111', 443, 0, 0))]
    with patch('network.socket.getaddrinfo', return_value=answer), \
         patch('network.socket.create_connection', side_effect=OSError('unreachable')):
        check = ipv6_probe()
    assert check['state'] == 'unknown'


def test_ipv6_live_public_egress_is_flagged():
    from network import ipv6_probe
    import socket
    from unittest.mock import MagicMock
    answer = [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2606:4700::1111', 443, 0, 0))]
    raw = MagicMock()
    raw.__enter__.return_value = raw
    tls = MagicMock()
    tls.__enter__.return_value = tls
    tls.recv.side_effect = [b'HTTP/1.1 200 OK\\r\\n\\r\\nip=2606:4700::1111\\nloc=US\\n', b'']
    context = MagicMock()
    context.wrap_socket.return_value = tls
    with patch('network.socket.getaddrinfo', return_value=answer), \
         patch('network.socket.create_connection', return_value=raw), \
         patch('network.ssl.create_default_context', return_value=context):
        value = ipv6_probe()
    assert value['state'] == 'fail'
    assert value['ip'] == '2606:4700::1111'
