import json
import ssl
import subprocess
from unittest.mock import patch, MagicMock
from diagnostics import environment_diagnostics, run_probes, tls_probe, ipv6_probe


def test_environment_never_discloses_secrets_or_hosts():
    env={'HTTPS_PROXY':'http://secret-user:secret-password@example-private.test:8080', 'NO_PROXY':'private.test', 'NODE_EXTRA_CA_CERTS':'C:/private/cert.pem'}
    result=json.dumps(environment_diagnostics(env))
    for secret in ('secret-user','secret-password','example-private.test','private.test','C:/private/cert.pem'):
        assert secret not in result


def test_bad_proxy_port_is_sanitized():
    result=environment_diagnostics({'HTTPS_PROXY':'http://u:secret@private.test:invalid'})
    assert 'secret' not in str(result) and 'private.test' not in str(result)


def test_probe_timeout_never_passes():
    with patch('diagnostics.subprocess.run', side_effect=subprocess.TimeoutExpired('probe',18)):
        result=run_probes('161.77.11.188',7897)
    assert all(v['state']=='unknown' for v in result.values())


def test_malformed_child_result_never_passes():
    with patch('diagnostics.subprocess.run', return_value=MagicMock(stdout=b'{"tls":{"state":"pass"}}')):
        result=run_probes('161.77.11.188',7897)
    assert result['tls']['state']=='unknown'


def test_tls_certificate_failure_blocks():
    conn=MagicMock();conn.connect.side_effect=ssl.SSLCertVerificationError('secret')
    with patch('diagnostics.http.client.HTTPSConnection',return_value=conn):
        result=tls_probe('161.77.11.188',7897)
    assert result['state']=='fail' and 'secret' not in str(result)
    conn.close.assert_called_once()


def test_tls_error_never_discloses_proxy_credentials():
    conn=MagicMock();conn.connect.side_effect=OSError('http://u:secret@host')
    with patch('diagnostics.http.client.HTTPSConnection',return_value=conn):
        result=tls_probe('161.77.11.188',7897)
    assert result['state']=='unknown' and 'secret' not in str(result)


def test_tls_healthy_certificate_but_wrong_exit_fails():
    conn=MagicMock();conn.sock.getpeercert.return_value={};response=conn.getresponse.return_value
    response.status=200;response.read.return_value=b'ip=1.1.1.1\nloc=US'
    conn.sock.version.return_value='TLSv1.3'
    with patch('diagnostics.http.client.HTTPSConnection',return_value=conn):
        result=tls_probe('161.77.11.188',7897)
    assert result['state']=='fail'


def test_ipv6_no_answer_is_not_no_leak():
    with patch('diagnostics.socket.getaddrinfo',return_value=[]):
        assert ipv6_probe()['state']=='unknown'
