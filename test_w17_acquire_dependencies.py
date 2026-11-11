"""Offline failure-injection tests; never contact APT or receive private input."""
import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import w17_acquire_dependencies as dependency


class AcquisitionTests(unittest.TestCase):
    def scenario(self, results):
        calls = []
        contexts = []
        def run(args, seconds):
            calls.append((args, seconds))
            if args[0] == '/usr/bin/apt-get':
                value = next(a for a in args if a.startswith('Dir::Etc::sourcelist='))
                source = Path(value.split('=', 1)[1])
                self.assertTrue(source.is_file())
                self.assertIn('Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg', source.read_text())
                self.assertNotIn('azure.', source.read_text())
                self.assertEqual(source.parent.stat().st_mode & 0o777, 0o755)
                contexts.append(source.parent)
            value = next(results)
            if isinstance(value, BaseException):
                raise value
            return value
        with patch.object(dependency.shutil, 'which', return_value='/usr/bin/tool'), \
             patch.object(dependency.Path, 'is_file', return_value=True), \
             patch.object(dependency, 'bounded', side_effect=run), \
             patch.object(dependency, 'diagnose') as diagnose, \
             contextlib.redirect_stdout(io.StringIO()):
            try:
                rc = dependency.acquire()
            finally:
                for folder in contexts:
                    self.assertFalse(folder.exists(), 'temporary APT context leaked')
        return rc, calls, diagnose

    def test_installed_import_avoids_all_network(self):
        rc, calls, diagnose = self.scenario(iter([0]))
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        diagnose.assert_not_called()

    def test_success_isolated_signed_sources_same_options(self):
        rc, calls, diagnose = self.scenario(iter([1, 0, 0, 0]))
        self.assertEqual(rc, 0)
        update, install = calls[1:3]
        self.assertEqual(update[1], 45)
        self.assertEqual(install[1], 45)
        self.assertEqual(update[0][:-2], install[0][:-6])
        for option in ('Dir::Etc::sourceparts=-', 'APT::Update::Error-Mode=any',
                       'Acquire::Retries=0', 'APT::Get::AllowUnauthenticated=false'):
            self.assertIn(option, update[0])
            self.assertIn(option, install[0])
        diagnose.assert_not_called()

    def test_update_timeout_stops_install(self):
        rc, calls, diagnose = self.scenario(iter([1, 124]))
        self.assertEqual(rc, 124)
        self.assertEqual(len(calls), 2)
        diagnose.assert_called_once()

    def test_incomplete_indexes_stop_install(self):
        rc, calls, diagnose = self.scenario(iter([1, 100]))
        self.assertEqual(rc, 100)
        self.assertEqual(len(calls), 2)
        diagnose.assert_called_once()

    def test_failed_install_stops_import(self):
        rc, calls, diagnose = self.scenario(iter([1, 0, 100]))
        self.assertEqual(rc, 100)
        self.assertEqual(len(calls), 3)
        diagnose.assert_called_once()

    def test_missing_import_after_install_refuses_success(self):
        rc, _, _ = self.scenario(iter([1, 0, 0, 1]))
        self.assertEqual(rc, 1)

    def test_interruption_cleans_context_and_propagates(self):
        with self.assertRaises(InterruptedError):
            self.scenario(iter([1, InterruptedError()]))

    def test_missing_tool_never_starts_acquisition(self):
        with patch.object(dependency.shutil, 'which', return_value=None), \
             patch.object(dependency, 'bounded') as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dependency.acquire(), 1)
        run.assert_not_called()

    def test_diagnostics_allowlist_and_no_output_passthrough(self):
        stream = io.StringIO()
        with patch.object(dependency, 'bounded', return_value=6) as run, \
             contextlib.redirect_stdout(stream):
            dependency.diagnose()
        self.assertEqual(run.call_count, 6)
        self.assertTrue(all(line.endswith('rc=6') for line in stream.getvalue().splitlines()))
        self.assertTrue(all(call.args[1] <= 6 for call in run.call_args_list))

    def test_missing_keyring_stops_before_apt(self):
        with patch.object(dependency.shutil, 'which', return_value='/usr/bin/tool'), \
             patch.object(dependency.Path, 'is_file', return_value=False), \
             patch.object(dependency, 'bounded', return_value=1) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dependency.acquire(), 1)
        self.assertEqual(run.call_count, 1)

    def test_direct_invocation_requires_hosted_marker(self):
        import sys
        result = subprocess.run([sys.executable, '-I', '-B', dependency.__file__],
                                env=dependency.ENV, capture_output=True, text=True,
                                timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('GitHub-hosted runner marker required', result.stderr)

    def test_real_timeout_kills_process_group(self):
        with tempfile.TemporaryDirectory() as folder:
            pidfile = Path(folder) / 'child.pid'
            code = ('import subprocess,time,pathlib; '
                    'p=subprocess.Popen(["/bin/sleep","60"]); '
                    'pathlib.Path(' + repr(str(pidfile)) + ').write_text(str(p.pid)); time.sleep(60)')
            import sys
            self.assertEqual(dependency.bounded([sys.executable, '-c', code], 0.5), 124)
            self.assertTrue(pidfile.exists())
            # Child may remain briefly as a zombie; ps must never show it running.
            child = pidfile.read_text()
            state = subprocess.run(['/bin/ps', '-o', 'stat=', '-p', child],
                                   capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith('Z'), state)


if __name__ == '__main__':
    unittest.main()
