"""Local formatting/error tests only: no Linux experiment, systemctl or host scan."""
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('observe', ROOT / 'w10_observe.py')
obs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(obs)


def wrapper_source():
    path = ROOT / '.github/workflows/w10-maintenance-window.yml'
    if not path.exists():
        path = ROOT / 'w10-maintenance-window.yml'
    text = path.read_text().split("<<'PY'\n", 1)[1].rsplit('\n          PY', 1)[0]
    return '\n'.join(line[10:] if line.startswith('          ') else line for line in text.splitlines())


class Diagnostics(unittest.TestCase):
    def test_redaction(self):
        for text in ('SECRET=123 /other/path python --token foo', 'credentials password arbitrary', 'x' * 9000):
            result = json.dumps(obs.redacted(text))
            self.assertNotIn('SECRET', result)
            self.assertNotIn('/other', result)
            self.assertNotIn('password', result)
            self.assertLess(len(result), 512)
        self.assertIn('failed', obs.redacted('failed SECRET')['tokens'])

    def test_command_failure_and_timeout(self):
        o = obs.Observer()
        with patch.object(obs.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1, stdout='secret', stderr='failed SECRET')):
            out, status = o.command(('unused',), obs.time.monotonic() + 1)
            self.assertIsNone(out)
            self.assertEqual(status['rc'], 1)
            self.assertNotIn('SECRET', json.dumps(status))
        with patch.object(obs.subprocess, 'run', side_effect=subprocess.TimeoutExpired('unused', .1)):
            out, status = o.command(('unused',), obs.time.monotonic() + 1)
            self.assertIsNone(out)
            self.assertEqual(status['status'], 'TimeoutExpired')
        with patch.object(obs.subprocess, 'run') as run:
            self.assertIsNone(o.command(('unused',), 0)[0])
            run.assert_not_called()

    def test_group_absent_error_and_populated(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(obs, 'CGROOT', Path(folder)):
            o = obs.Observer()
            group = '/system.slice/' + obs.UNITS[0]
            self.assertEqual(o.group(group)['status'], 'disappeared')
            self.assertIsNone(o.group('/other')['empty'])
            path = Path(folder) / group.lstrip('/')
            path.mkdir(parents=True)
            (path / 'cgroup.events').write_text('populated 1\nfrozen 0\n')
            (path / 'cgroup.procs').write_text('123\n')
            self.assertFalse(o.group(group)['empty'])
            self.assertEqual(o.group(group)['pids'], [123])
            with patch.object(Path, 'read_text', side_effect=PermissionError):
                self.assertIsNone(o.group(group)['empty'])
                self.assertEqual(o.group(group)['status'], 'PermissionError')
            (path / 'cgroup.procs').unlink()
            self.assertEqual(o.group(group)['status'], 'incomplete')

    def test_process_fields_and_disappearance(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(obs, 'PROC', Path(folder)):
            p = Path(folder) / '123'
            p.mkdir()
            fields = ['S', '12'] + ['0'] * 17 + ['456']
            (p / 'stat').write_text('123 (do not emit --SECRET) ' + ' '.join(fields))
            result = obs.Observer().process(123)
            self.assertEqual(result['start_ticks'], 456)
            self.assertEqual(result['ppid'], 12)
            self.assertNotIn('SECRET', json.dumps(result))
            self.assertEqual(obs.Observer().process(124)['status'], 'disappeared')

    def test_unknown_jobs_never_empty(self):
        o = obs.Observer()
        def command(args, end):
            if 'list-jobs' in args:
                return 'malformed', {'status': 'ok'}
            return None, {'status': 'error', 'rc': 1}
        with patch.object(o, 'command', side_effect=command), patch.object(o, 'group', return_value={'status': 'disappeared', 'empty': None}), patch.object(o, 'output') as output:
            o.snapshot('test', {}, obs.time.monotonic() + 10)
            data = output.call_args[0][0]['observation']
            self.assertIsNone(data['jobs'])
            self.assertIsNone(data['units'][obs.UNITS[0]]['predicates'])

    def test_exact_jobs_and_unchanged_sample_suppression(self):
        o = obs.Observer()
        unit = obs.UNITS[0]
        def command(args, end):
            if 'list-jobs' in args:
                return '123 ' + unit + ' stop running\n124 foreign.service start waiting\n', {'status': 'ok'}
            return 'Id=' + args[2] + '\nLoadState=loaded\nActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\nJob=0\n', {'status': 'ok'}
        with patch.object(o, 'command', side_effect=command), patch.object(o, 'group', return_value={'status': 'disappeared', 'empty': None}), patch.object(o, 'output') as output:
            o.snapshot('test', {}, obs.time.monotonic() + 10)
            self.assertEqual(output.call_args[0][0]['observation']['jobs'], [['123', unit, 'stop', 'running']])
            self.assertNotIn('foreign', json.dumps(output.call_args[0][0]))
            o.next_at = 0
            o.snapshot('test', {}, obs.time.monotonic() + 10)
            self.assertEqual(output.call_count, 1)

    def test_expiry_uses_stale_snapshot_no_command(self):
        o = obs.Observer()
        o.previous = {'known': 'old'}
        with patch.object(o, 'command') as command, patch.object(o, 'output') as output:
            o.snapshot('closure_exit', {}, 0, force=True)
            command.assert_not_called()
            self.assertTrue(output.call_args[0][0]['last_is_stale'])

    def test_actual_predicates_are_separate_and_unknown(self):
        o = obs.Observer()
        with patch.object(o, 'output') as output:
            o.unit_decision('closure', obs.UNITS[0], {'ActiveState': 'failed', 'MainPID': '0'})
            predicates = output.call_args[0][0]['predicates']
            self.assertFalse(predicates['ActiveState'])
            self.assertTrue(predicates['MainPID'])
            self.assertIsNone(predicates['ControlPID'])
            self.assertIsNone(predicates['Job'])
            o.unit_decision('closure', obs.UNITS[0], {'ActiveState': 'failed', 'MainPID': '0'})
            self.assertEqual(output.call_count, 1)

    def test_output_bound_and_reserved_expiry(self):
        o = obs.Observer()
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            for _ in range(100):
                o.output({'payload': 'x' * 16000})
            o.output({'phase': 'closure_exit', 'status': 'EXPIRED', 'last_is_stale': True})
        text = stream.getvalue()
        self.assertLess(len(text), 197000)
        self.assertEqual(text.count('observation_limit'), 1)
        self.assertIn('EXPIRED', text)

    def test_syntax_and_original_identity(self):
        for filename in ('w9_linux_window.py', 'w9_linux_window_diagnostic.py', 'w10_observe.py'):
            if (ROOT / filename).exists():
                ast.parse((ROOT / filename).read_text(), feature_version=(3, 12))
        tree = ast.parse(wrapper_source(), feature_version=(3, 12))
        for name, file in (('EXPECTED', 'w9_linux_window_diagnostic.py'), ('ORIGINAL_EXPECTED', 'w9_linux_window.py')):
            assignments = [n for n in tree.body if isinstance(n, ast.Assign) and
                           any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
            self.assertEqual(len(assignments), 1)
            self.assertEqual(ast.literal_eval(assignments[0].value), hashlib.sha256((ROOT / file).read_bytes()).hexdigest())
        if (ROOT / 'w9_linux_window.py').exists():
            self.assertEqual(hashlib.sha256((ROOT / 'w9_linux_window.py').read_bytes()).hexdigest(), 'db7256bb9815c549bdc5364311d99b4c07f7f296dbd53e027725c39216d18366')

    def test_primary_failure_survives_secondary(self):
        source = wrapper_source().split('# Namespace ownership', 1)[1].split('\n', 1)[1]
        for rc, driver_error, containment_error, termination_error in (
            (1, None, RuntimeError('secondary'), None),
            (0, None, RuntimeError('secondary'), None),
            (0, None, None, None),
            (None, TimeoutError('PRIMARY'), RuntimeError('secondary'), RuntimeError('terminate')),
        ):
            events = []
            driver = types.SimpleNamespace(pid=123, wait=None)
            calls = iter([driver_error or rc, termination_error or 0])
            def wait(**kwargs):
                result = next(calls)
                if isinstance(result, BaseException):
                    raise result
                return result
            driver.wait = wait
            def contain(end):
                if containment_error:
                    raise containment_error
            def require(condition, reason):
                if not condition:
                    raise RuntimeError(reason)
            ns = {'subprocess': types.SimpleNamespace(Popen=lambda *a, **k: driver),
                  'SCRIPT': 'synthetic', 'module': types.SimpleNamespace(emit=lambda kind, **kw: events.append((kind, kw))),
                  'signal': types.SimpleNamespace(SIGINT=2, SIGTERM=15, SIGHUP=1, SIG_IGN=0, SIGKILL=9, signal=lambda *a: None),
                  'os': types.SimpleNamespace(killpg=lambda *a: None), 'time': obs.time,
                  'contain': contain, 'observe': lambda *a, **k: None, 'require': require}
            if driver_error:
                with self.assertRaisesRegex(TimeoutError, 'PRIMARY'):
                    exec(source, ns)
            elif rc or containment_error:
                with self.assertRaisesRegex(RuntimeError, 'W9 failed' if rc else 'final confinement'):
                    exec(source, ns)
            else:
                with contextlib.redirect_stdout(io.StringIO()):
                    exec(source, ns)
            self.assertIn(('initial_result', {'driver_rc': rc, 'driver_completed': rc is not None}), events)
            self.assertIn(('containment_result', {'verified': True}) if not containment_error else
                          ('containment_result', {'verified': False, 'status': 'RuntimeError', 'initial_driver_rc': rc, 'result': 'NON_VERIFIE'}), events)


if __name__ == '__main__':
    unittest.main()
