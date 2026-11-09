"""Historical command dispatch through the explicit confined constructor, offline."""
import contextlib
import io
import json
import os
import time
import unittest
from unittest.mock import patch

import test_w11_credstore as previous
from w104_credstore import confined_main


@unittest.skipUnless(previous.SOURCE, 'explicit pinned local source required')
class ConfinedCommands(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        previous.CredstoreCoupling.setUpClass.__func__(cls)

    setUp = previous.CredstoreCoupling.setUp
    transport = previous.CredstoreCoupling.transport

    def command(self, operation, *, apply=True, preference=None):
        args = [operation]
        if apply:
            args.append('--apply')
        if preference:
            args += ['--recover-to', preference]
        stream = io.StringIO()
        with (patch.object(self.private.os, 'geteuid', return_value=0),
              patch.object(self.private, 'load_policy', return_value=self.fixture.policy),
              contextlib.redirect_stdout(stream)):
            code = confined_main(
                self.private, args, groups={}, deadline=time.monotonic() + 30,
                observer=self.observer, expected_uid=os.getuid(), expected_gid=os.getgid(),
                probe=self.fixture.probe, xattr_reader=lambda fd: ())
        report = json.loads(stream.getvalue())
        self.assertEqual(set(report), self.private.OUTPUT_FIELDS)
        self.assertEqual(report['operation'], operation)
        for value in self.old + self.target:
            self.assertNotIn(value.decode(), stream.getvalue())
        return code, report

    def test_apply_gate_keeps_sources_and_staging(self):
        code, report = self.command('rotate', apply=False)
        self.assertEqual(code, self.private.EXIT_APPLY_REQUIRED)
        self.assertEqual(report['status'], 'APPLY_REQUIRED')
        self.assertEqual(self.fixture.source_values(), self.old)
        self.assertEqual(len(list(self.fixture.staging.iterdir())), 3)

    def test_install_rotate_restore_keep_historical_dispatch(self):
        for path in self.fixture.paths:
            path.unlink()
        for operation in ('install', 'rotate', 'restore'):
            if operation != 'install':
                self.target = self.fixture.write_staging(prefix=operation.encode())
            code, report = self.command(operation)
            self.assertEqual(code, 0)
            self.assertEqual(report['status'], 'COMPLIANT')
            self.assertEqual(self.fixture.source_values(), self.target)
            self.assertTrue(self.closed)

    def test_partial_publication_recovers_through_command(self):
        with self.assertRaises(self.private.SimulatedInterruption):
            self.manager.rotate(interrupt_after=1)
        code, report = self.command('recover', preference='previous')
        self.assertEqual(code, 0)
        self.assertEqual(report['status'], 'COMPLIANT')
        self.assertEqual(self.fixture.source_values(), self.old)

    def test_recovery_refuses_missing_masks_and_retains_journal(self):
        with self.assertRaises(self.private.SimulatedInterruption):
            self.manager.rotate(interrupt_after=1)
        journal = (self.fixture.journal / 'active.json').read_bytes()
        self.closed = False
        code, report = self.command('recover')
        self.assertEqual(code, self.private.EXIT_QUIESCENCE)
        self.assertNotEqual(report['status'], 'COMPLIANT')
        self.assertEqual((self.fixture.journal / 'active.json').read_bytes(), journal)

    def test_observation_failure_is_redacted_without_publication(self):
        self.observation_error = True
        code, _ = self.command('rotate')
        self.assertEqual(code, self.private.EXIT_QUIESCENCE)
        self.assertEqual(self.fixture.source_values(), self.old)

    def test_unexpected_signal_is_not_command_success(self):
        self.signal = 9
        code, report = self.command('rotate')
        self.assertEqual(code, self.private.EXIT_QUIESCENCE)
        self.assertNotEqual(report['status'], 'COMPLIANT')
        self.assertEqual(self.fixture.source_values(), self.old)

    def test_normalization_uses_existing_command(self):
        for path in self.fixture.paths:
            path.chmod(self.private.CURRENT_LEGACY_MODE)
        code, report = self.command('normalize-mode')
        self.assertEqual(code, 0)
        self.assertEqual(report['status'], 'COMPLIANT')
        self.assertEqual(self.fixture.source_modes(), [self.private.FINAL_MODE] * 3)

    def test_factory_failure_preserves_redacted_exit_contract(self):
        stream = io.StringIO()
        with (patch.object(self.private.os, 'geteuid', return_value=0),
              patch.object(self.private, 'load_policy', return_value=self.fixture.policy),
              contextlib.redirect_stdout(stream)):
            code = self.private.safe_main(['rotate', '--apply'], manager_factory=lambda policy: 1 / 0)
        self.assertEqual(code, self.private.EXIT_IO)
        self.assertNotIn('ZeroDivisionError', stream.getvalue())
        self.assertNotEqual(json.loads(stream.getvalue())['status'], 'COMPLIANT')


if __name__ == '__main__':
    unittest.main()
