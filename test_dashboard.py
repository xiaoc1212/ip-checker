from dashboard import Dashboard, risk, dns_probe, browser_results, webrtc_result
from network import result
from unittest.mock import patch
import json
import time


def test_risk_does_not_adopt_other_address_score():
    with patch('dashboard.request_text', return_value=(json.dumps({'ip': '161.77.11.85', 'trust_score': 97}), 1)):
        assert risk('161.77.11.188', 7897)['state'] == 'unknown'


def test_risk_can_display_a_covering_network_profile():
    data = {'ip': '161.77.11.85', 'cidr': '161.77.11.0/24', 'trust_score': 97, 'isResidential': True}
    data.update({k: False for k in ['is_datacenter', 'is_vpn', 'is_proxy', 'is_tor', 'is_crawler', 'is_abuser']})
    with patch('dashboard.request_text', return_value=(json.dumps(data), 1)):
        value = risk('161.77.11.188', 7897)
        assert value['state'] == 'pass' and value['scope'] == 'network'
        assert value['data']['trust_score'] == 97
        assert risk('161.77.12.188', 7897)['state'] == 'unknown'


def test_dns_accepts_the_websites_string_array_and_resolves_geo():
    responses = [(json.dumps({'dns_servers': ['1.1.1.1']}), 1), (json.dumps({'country_code': 'de', 'isp': 'Example'}), 1)]
    with patch('socket.getaddrinfo', return_value=[]), patch('dashboard.time.sleep'), patch('dashboard.request_text', side_effect=responses):
        value = dns_probe(7897)
        assert value['state'] == 'pass'
        assert value['servers'][0]['ip'] == '1.1.1.1'


def test_dns_missing_results_remain_unknown():
    with patch('socket.getaddrinfo', return_value=[]), patch('dashboard.time.sleep'), patch('dashboard.request_text', return_value=(json.dumps({'dns_servers': []}), 1)):
        assert dns_probe(7897)['state'] == 'unknown'


def test_startup_requires_every_mandatory_check_and_fresh_report():
    app = Dashboard()
    app.state['report'] = {'finished': time.time(), 'expected_ip': app.state['expected'],
                           'native': {'kind': 'exe', 'target': 'Claude.exe'},
                           'checks': {k: result('pass', '') for k in ['guard', 'claude', 'system']}}
    assert app.readiness()[1]
    app.state['extra'] = {k: {'state': 'pass'} for k in ['risk', 'dns']}
    assert app.readiness()[1]
    for key in ['risk', 'dns']:
        app.state['extra'][key]['state'] = 'unknown'
        assert app.readiness()[1]
        app.state['extra'][key]['state'] = 'pass'
    app.state['report']['finished'] -= 61
    assert not app.readiness()[1]
    rows = app.readiness()[0]
    assert rows[0]['state'] == 'pass'
    assert rows[-1]['state'] == 'expired'


def test_browser_probe_is_not_a_substitute_for_native_udp_guard():
    app = Dashboard()
    app.state['report'] = {'finished': time.time(), 'expected_ip': app.state['expected'],
                           'native': {'kind': 'exe', 'target': 'Claude.exe'},
                           'checks': {k: result('pass', '') for k in ['guard', 'claude', 'system']}}
    app.state['extra'] = {'risk': {'state': 'pass'}, 'dns': {'state': 'pass'}, 'webrtc': {'state': 'unknown'}}
    assert app.readiness()[1]
    app.state['report']['checks']['guard']['state'] = 'fail'
    assert not app.readiness()[1]


def test_missing_installation_blocks_launch():
    app = Dashboard()
    app.state['report'] = {'finished': time.time(), 'expected_ip': app.state['expected'],
                           'checks': {k: result('pass', '') for k in ['guard', 'claude', 'system']}}
    assert not app.readiness()[1]


def test_browser_failure_is_not_replaced_by_backend_ip():
    assert browser_results({})['claude']['state'] == 'unknown'
    assert 'ip' not in browser_results({})['claude']


def test_webrtc_no_candidates_or_no_reference_remains_unknown():
    assert webrtc_result({'ips': [], 'completed': True}, '161.77.11.188')['state'] == 'unknown'
    assert webrtc_result({'ips': ['1.1.1.1']}, None)['state'] == 'unknown'


def test_webrtc_compares_browser_exit_including_ipv6():
    assert webrtc_result({'ips': ['161.77.11.188']}, '161.77.11.188')['state'] == 'pass'
    assert webrtc_result({'ips': ['2606:4700:4700::1111']}, '161.77.11.188')['state'] == 'fail'
    assert webrtc_result({'ips': ['192.168.1.1'], 'completed': True}, '161.77.11.188')['state'] == 'unknown'


def test_old_browser_run_cannot_replace_current_result():
    app = Dashboard()
    app.state['run_id'] = 'new'
    assert not app.record_browser({'run_id': 'old'})
    assert 'browser' not in app.state['extra']
