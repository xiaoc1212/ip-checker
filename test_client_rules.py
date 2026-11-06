import copy
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import yaml

import client_rules
from configure import transform
from network import GROUP


def configuration():
    return {'proxy-groups': [{'name': GROUP, 'proxies': ['ISP']}],
            'dns': {'nameserver': ['https://example.test/dns-query']},
            'tun': {'enable': True, 'strict-route': False}, 'rules': ['MATCH,Other']}


@pytest.mark.parametrize('field', ['dns', 'tun', 'mode', 'proxy-groups', 'find-process-mode'])
def test_global_changes_are_rejected(field):
    before = configuration()
    after = transform(copy.deepcopy(before))
    after[field] = 'unapproved-change'
    with pytest.raises(RuntimeError, match='规则以外'):
        client_rules.require_rule_only(before, after)


def test_only_expected_rule_additions_are_accepted():
    before = configuration()
    after = transform(copy.deepcopy(before))
    client_rules.require_rule_only(before, after)
    after['rules'].insert(0, 'MATCH,DIRECT')
    with pytest.raises(RuntimeError, match='最小规则'):
        client_rules.require_rule_only(before, after)


def deploy_mocks(health):
    stack = ExitStack()
    before = configuration()
    after = transform(copy.deepcopy(before))
    plan = {'state': 'prepared', 'expected': '161.77.11.188',
            'before': {'script': 'old-script', 'runtime': 'old-runtime'},
            'candidates': {'Script.candidate.js': 'candidate-script', 'config.candidate.yaml': 'candidate-runtime'}}
    hashes = {'Script.js': 'old-script', 'clash-verge.yaml': 'old-runtime',
              'Script.candidate.js': 'candidate-script', 'config.candidate.yaml': 'candidate-runtime'}
    text = {'config.before.yaml': yaml.safe_dump(before), 'config.candidate.yaml': yaml.safe_dump(after),
            'clash-verge.yaml': yaml.safe_dump(after), 'Script.candidate.js': 'candidate-source'}
    stack.enter_context(patch('client_rules.load_plan', return_value=(plan, Path('fake-backup'))))
    stack.enter_context(patch('client_rules.digest', side_effect=lambda p: hashes[p.name]))
    stack.enter_context(patch.object(Path, 'read_text', autospec=True, side_effect=lambda p, **kw: text[p.name]))
    stack.enter_context(patch('client_rules.validate_core'))
    stack.enter_context(patch('client_rules.snapshot', return_value={'config': {'mixed-port': 7897}}))
    stack.enter_context(patch('client_rules.validate_guard', return_value={'state': 'pass'}))
    stack.enter_context(patch('client_rules.health', side_effect=health))
    write = stack.enter_context(patch('client_rules.write_atomic'))
    copy_file = stack.enter_context(patch('client_rules.shutil.copy2'))
    reload = stack.enter_context(patch('client_rules.request'))
    stack.enter_context(patch('client_rules.RECEIPT', MagicMock()))
    return stack, plan, write, copy_file, reload


def test_failed_baseline_never_writes_configuration():
    stack, plan, write, copy_file, reload = deploy_mocks([{'proxy': {'state': 'unknown'}}])
    with stack, pytest.raises(RuntimeError, match='联网验收'):
        client_rules.apply_prepared()
    write.assert_not_called()
    copy_file.assert_not_called()
    reload.assert_not_called()


def test_failed_postcheck_restores_both_files_and_core():
    stack, plan, write, copy_file, reload = deploy_mocks([{'proxy': {'state': 'pass'}}, {'proxy': {'state': 'unknown'}}])
    with stack, pytest.raises(RuntimeError, match='已恢复'):
        client_rules.apply_prepared()
    assert write.call_count == 2 and copy_file.call_count == 2 and reload.call_count == 2
    assert plan['state'] == 'rolled_back'
