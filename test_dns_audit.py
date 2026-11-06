import struct
from dns_capture import dns_question, related, decode_packet, query_bytes
from dns_audit import inspect_dns, summarize_capture
from network import GROUP
from unittest.mock import patch, MagicMock
import pytest


def packet(name, source='192.168.1.11', destination='223.5.5.5'):
    import socket
    body = query_bytes(name)
    udp = struct.pack('!HHHH', 53000, 53, len(body) + 8, 0) + body
    ip = bytearray(20)
    ip[0] = 0x45
    ip[2:4] = struct.pack('!H', len(udp) + 20)
    ip[9] = 17
    ip[12:16] = socket.inet_aton(source)
    ip[16:20] = socket.inet_aton(destination)
    return bytes(ip) + udp


def test_dns_parser_and_boundary_matching():
    assert decode_packet(packet('CLAUDE.ai'))['name'] == 'claude.ai'
    assert related('bridge.claudeusercontent.com')
    assert not related('claude.ai.evil.test')
    assert not related('notclaude.ai')


def test_malformed_and_compressed_pointer_loop_rejected():
    assert dns_question(b'') is None
    header = struct.pack('!HHHHHH', 1, 0, 1, 0, 0, 0)
    assert dns_question(header + b'\xc0\x0c') is None
    assert decode_packet(packet('claude.ai')[:24]) is None


def test_zero_queries_without_calibration_is_not_safe():
    report = summarize_capture({'state': 'complete', 'seconds': 40, 'records': []})
    assert report['state'] == 'unknown'


def test_calibrated_zero_queries_is_only_limited_observation():
    report = summarize_capture({'state': 'complete', 'seconds': 40, 'records': [], 'calibration_query_seen': True, 'calibration_reply_seen': True})
    assert report['state'] == 'info'
    assert summarize_capture({'state': 'error', 'records': []})['state'] == 'unknown'


def test_positive_queries_are_not_hidden_by_failed_calibration():
    report = summarize_capture({'state': 'error', 'records': [{'outbound': True, 'response': False}]})
    assert report['state'] == 'fail'


def test_proxy_metadata_is_not_global_no_leak_proof():
    config = {'dns': {'nameserver': ['https://user:secret@dns.test/dns-query#skip-cert-verify=true']}}
    conns = {'connections': [{'metadata': {'process': 'claude.exe', 'type': 'HTTPS', 'host': 'claude.ai', 'destinationIP': ''}, 'chains': [GROUP, 'ISP']}]}
    proxies = {'proxies': {GROUP: {'now': 'ISP'}, 'ISP': {'type': 'Http'}}}
    result = inspect_dns(config, conns, proxies)
    assert result['delegated_count'] == 1 and result['dns_certificate_warning']
    assert 'secret' not in str(result) and 'user:' not in str(result)
    conns['connections'][0]['metadata']['destinationIP'] = '1.1.1.1'
    assert inspect_dns(config, conns, proxies)['delegated_count'] == 0


def test_cancelled_privilege_prompt_never_produces_safety_verdict():
    from dns_audit import run_capture, DIRECTORY
    with patch('pathlib.Path.mkdir'), patch('dns_audit.subprocess.run', return_value=MagicMock(returncode=1)), patch('dns_audit.collect') as collect:
        with pytest.raises(RuntimeError, match='授权取消'):
            run_capture(DIRECTORY / 'cancelled.json')
        collect.assert_not_called()


def test_physical_capture_output_cannot_escape_audit_directory():
    from dns_audit import run_capture, ROOT
    with pytest.raises(RuntimeError, match='目录不合法'):
        run_capture(ROOT / 'unrelated.json')


def test_corrupt_saved_evidence_does_not_break_dashboard():
    from dns_audit import read_saved_evidence
    with patch('pathlib.Path.stat', return_value=MagicMock(st_size=2)), patch('pathlib.Path.read_text', return_value='[]'):
        assert read_saved_evidence() is None
