import time
from unittest.mock import patch
from dashboard import Dashboard
from network import gate


def complete():
    app = Dashboard()
    app.state['report'] = {'finished': time.time(), 'expected_ip': app.state['expected'], 'native': {'kind':'exe'},
        'checks': {key: {'state':'pass'} for key in ('guard','claude','system','tls')}}
    return app


def test_ui_and_backend_gate_agree_on_tls_failure():
    app = complete()
    assert app.readiness()[1] and gate(app.state['report'], app.state['expected'])[0]
    for state in ('unknown', 'fail'):
        app.state['report']['checks']['tls']['state'] = state
        assert not app.readiness()[1] and not gate(app.state['report'], app.state['expected'])[0]


def test_ipv6_observation_does_not_claim_client_leak_or_gate():
    app = complete()
    app.state['report']['checks']['ipv6'] = {'state':'info'}
    assert app.readiness()[1]
    app.state['report']['checks']['ipv6'] = {'state':'unknown'}
    assert app.readiness()[1]


def test_missing_installation_and_stale_report_block():
    app = complete()
    app.state['report'].pop('native')
    assert not app.readiness()[1]
    app = complete()
    app.state['report']['finished'] -= 61
    assert not app.readiness()[1]


def test_monitor_reports_control_channel_loss():
    app = Dashboard()
    with patch('dashboard.controller', side_effect=RuntimeError('secret')):
        app.monitor_once()
    assert app.monitor['state'] == 'unknown'
    assert 'secret' not in str(app.monitor)


def test_monitor_detects_wrong_client_chain():
    app = Dashboard()
    connections = {'connections':[{'metadata':{'process':'Claude.exe', 'destinationIP':'1.1.1.1'},'chains':['DIRECT']}]}
    with patch('dashboard.controller', side_effect=[{}, connections]), patch('dashboard.validate_guard', return_value={'state':'pass','detail':'ok'}):
        app.monitor_once()
    assert app.monitor['state'] == 'fail'
    assert app.monitor['connections'][0]['state'] == 'fail'


def test_check_returns_its_own_run_and_rejects_overlap():
    app = Dashboard()
    with patch('dashboard.threading.Thread'), patch('dashboard.save_settings'):
        run = app.start('161.77.11.188')
        assert run and run == app.state['run_id']
        assert app.start('161.77.11.189') is None
        assert app.state['expected'] == '161.77.11.188'


def test_dns_job_prevents_probe_and_launch_overlap():
    app = complete()
    with patch('dashboard.threading.Thread'):
        assert app.start_dns_audit() is True
    assert app.start('161.77.11.188') is None
    assert not app.readiness()[1]
