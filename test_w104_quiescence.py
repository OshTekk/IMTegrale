"""Nine regressions: fresh evidence, termination versus outcome, and signal redaction."""
import contextlib
import io
import subprocess
import time
import types
import unittest
from unittest.mock import patch

import w10_observe as obs


class Quiescence(unittest.TestCase):
    def setUp(self):
        self.values = {unit: {'Id': unit, 'LoadState': 'masked', 'ActiveState': 'inactive',
                             'SubState': 'dead', 'MainPID': '0', 'ControlPID': '0', 'Job': '',
                             'ControlGroup': '', 'Result': 'success'} for unit in obs.UNITS}
        self.jobs = ''
        self.observer = types.SimpleNamespace(
            unit_decision=lambda *a: None, residuals=lambda *a: None,
            group=lambda group: {'status': 'disappeared', 'empty': None})

    def read(self, args, **kwargs):
        text = self.jobs if 'list-jobs' in args else '\n'.join(
            key + '=' + value for key, value in self.values[args[2]].items())
        return types.SimpleNamespace(returncode=0, stdout=text, stderr='')

    def proof(self, masks=True, residuals=()):
        with patch.object(obs.subprocess, 'run', side_effect=self.read), \
             patch.object(obs, 'masks_present', return_value=masks), \
             patch.object(obs, 'synthetic_processes', return_value=list(residuals)):
            return obs.namespace_quiescent({}, time.monotonic() + 5, self.observer)

    def test_inactive_complete_proof(self):
        self.assertTrue(self.proof())

    def test_failed_terminated_preserves_failure(self):
        general = self.values['w9-proof-general.service']
        general.update(ActiveState='failed', SubState='failed', Result='signal')
        self.assertTrue(self.proof())
        self.assertEqual(general['Result'], 'signal')
        self.assertEqual(general['ActiveState'], 'failed')

    def test_failed_populated_group_rejected(self):
        self.values['w9-proof-general.service'].update(ActiveState='failed', SubState='failed')
        self.observer.group = lambda _: {'status': 'present', 'empty': False,
                                         'pids': [123], 'truncated': False}
        self.assertFalse(self.proof())

    def test_zero_main_pid_descendant_and_incomplete_group_rejected(self):
        self.observer.group = lambda _: {'status': 'present', 'empty': True,
                                         'pids': [123], 'truncated': False}
        self.assertFalse(self.proof())
        self.observer.group = lambda _: {'status': 'incomplete', 'empty': None}
        with self.assertRaisesRegex(RuntimeError, 'cgroup observation'):
            self.proof()

    def test_remaining_job_or_control_pid_rejected(self):
        self.jobs = '123 w9-proof-general.service stop running\n'
        self.assertFalse(self.proof())
        self.jobs = ''
        self.values['w9-proof-general.service']['ControlPID'] = '123'
        self.assertFalse(self.proof())

    def test_observation_error_unknown_and_truncation_refuse(self):
        for result in (types.SimpleNamespace(returncode=1, stdout='', stderr='private'),
                       types.SimpleNamespace(returncode=0, stdout='x' * 16385, stderr='')):
            with patch.object(obs, 'masks_present', return_value=True), \
                 patch.object(obs.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(RuntimeError, 'observation failed'):
                    obs.namespace_quiescent({}, time.monotonic() + 5, self.observer)
        del self.values['w9-proof-general.service']['Job']
        with self.assertRaisesRegex(RuntimeError, 'incomplete quiescence'):
            self.proof()
        with patch.object(obs, 'masks_present', return_value=True), \
             patch.object(obs.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixed', 1)):
            with self.assertRaises(subprocess.TimeoutExpired):
                obs.namespace_quiescent({}, time.monotonic() + 5, self.observer)

    def test_missing_masks_residual_and_nonterminal_refuse(self):
        self.assertFalse(self.proof(masks=False))
        self.assertFalse(self.proof(residuals=[123]))
        self.values['w9-proof-general.service'].update(ActiveState='deactivating', SubState='stop-sigterm')
        self.assertFalse(self.proof())

    def signal_evidence(self, status):
        text = ('Id=w9-proof-general.service\nActiveState=failed\nSubState=failed\n'
                'Result=signal\nExecMainCode=0\nExecMainStatus=0\nKillSignal=15\n'
                'ExecStartPre={ path=/SECRET; argv[]=private; code=killed ; status=' + str(status) + ' }\n')
        def read(args, **kwargs):
            return types.SimpleNamespace(returncode=0, stdout=(
                'killed signal SECRET=private /private/path' if 'journalctl' in args[0] else text), stderr='')
        with patch.object(obs.subprocess, 'run', side_effect=read), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = obs.general_stop_evidence(time.monotonic() + 5)
        self.assertNotIn('SECRET', output.getvalue())
        self.assertNotIn('/private', output.getvalue())
        return result

    def test_unexpected_signal_is_not_expected_success(self):
        result = self.signal_evidence(9)
        self.assertFalse(result['expected_pre_sigterm'])
        self.assertEqual(result['pre_exits'], [{'code': 'killed', 'status': 9}])

    def test_expected_pre_sigterm_is_evidenced(self):
        result = self.signal_evidence(15)
        self.assertTrue(result['expected_pre_sigterm'])
        self.assertEqual(result['ExecMainCode'], '0')
        self.assertEqual(result['ExecMainStatus'], '0')
        self.assertEqual(result['Result'], 'signal')


if __name__ == '__main__':
    unittest.main()
