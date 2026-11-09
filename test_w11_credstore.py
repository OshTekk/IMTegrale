"""Offline coupling: real private manager I/O, simulated fixed W10.4 observations."""
import contextlib
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import sys
import time
import types
import unittest
from unittest.mock import patch

import w10_observe as obs
from w104_credstore import confined_manager

SOURCE = os.environ.get('W11_CREDSTORE_SOURCE')
MANAGER_HASH = '046a3d7b002a33192b95e529397fd95d5b0be1bee77685fb3be855dc4fff44e6'
HELPERS_HASH = '717bfad2b4652019b617ffdcf3c95becd63931632b8b912c254c7e8251caf180'


@unittest.skipUnless(SOURCE, 'explicit local private source required; no Linux/production certification')
class CredstoreCoupling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(SOURCE)
        if not root.is_absolute() or root.is_symlink():
            raise RuntimeError('explicit regular private source root required')
        for name, expected in [('manager.py', MANAGER_HASH), ('tests/helpers.py', HELPERS_HASH)]:
            source = root / 'credstore' / name
            if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise RuntimeError('private test source identity mismatch')
        sys.path.insert(0, str(root / 'credstore'))
        cls.private = importlib.import_module('manager')
        helpers = importlib.import_module('tests.helpers')
        if (Path(cls.private.__file__).resolve() != (root / 'credstore/manager.py').resolve()
                or Path(helpers.__file__).resolve() != (root / 'credstore/tests/helpers.py').resolve()):
            raise RuntimeError('private test import identity mismatch')
        cls.Fixture = helpers.Fixture

    def setUp(self):
        self.fixture = self.Fixture()
        self.addCleanup(self.fixture.close)
        self.old = self.fixture.write_sources()
        self.target = self.fixture.write_staging()
        services = self.fixture.policy['services']
        mapping = dict(zip(services['application_services'], obs.UNITS[:5], strict=True))
        services['application_services'] = list(obs.UNITS[:5])
        services['consumers'] = {key: [mapping[name] for name in names]
                                 for key, names in services['consumers'].items()}
        services['stop_for_mutation'] = [mapping[name] for name in services['stop_for_mutation']]
        services['operations_check'] = 'w9-proof-trigger.service'
        self.closed, self.signal, self.observation_error = True, 15, False
        self.values = {unit: {'Id': unit, 'LoadState': 'masked', 'ActiveState': 'inactive',
                             'SubState': 'dead', 'MainPID': '0', 'ControlPID': '0', 'Job': '',
                             'ControlGroup': '', 'Result': 'success'} for unit in obs.UNITS}
        self.values[obs.UNITS[0]].update(ActiveState='failed', SubState='failed', Result='signal',
                                        ExecMainCode='0', ExecMainStatus='0', KillSignal='15', ExecStartPre='')
        for key in ('MainPID', 'ControlPID', 'ControlGroup'):
            self.values['w9-proof-legacy.timer'].pop(key)
        self.observer = obs.Observer()
        patches = [patch.object(obs.subprocess, 'run', side_effect=self.transport),
                   patch.object(obs, 'masks_present', side_effect=lambda: self.closed),
                   patch.object(obs, 'synthetic_processes', return_value=[]),
                   patch.object(self.observer, 'group', return_value={'status': 'disappeared', 'empty': None}),
                   patch.object(obs, 'STOP_STARTED_US', int(time.monotonic() * 1_000_000) - 1000, create=True),
                   contextlib.redirect_stdout(io.StringIO())]
        for item in patches:
            item.__enter__()
            self.addCleanup(item.__exit__, None, None, None)
        self.manager = confined_manager(
            self.private.CredstoreManager, self.fixture.policy,
            groups={}, deadline=time.monotonic() + 30, observer=self.observer,
            expected_uid=os.getuid(), expected_gid=os.getgid(), probe=self.fixture.probe,
            xattr_reader=lambda fd: ())

    def transport(self, args, **kwargs):
        if self.observation_error:
            raise OSError('synthetic observation unavailable')
        if 'journalctl' in args[0]:
            stdout = json.dumps({'MESSAGE': 'Control process exited, code=killed, status=' +
                                str(self.signal) + ('/TERM' if self.signal == 15 else '/KILL'),
                                '__MONOTONIC_TIMESTAMP': str(int(time.monotonic() * 1_000_000))})
        elif 'list-jobs' in args:
            stdout = ''
        else:
            stdout = '\n'.join(key + '=' + value for key, value in self.values[args[2]].items())
        return types.SimpleNamespace(returncode=0, stdout=stdout, stderr='')

    def error(self, code, operation):
        with self.assertRaises(self.private.CredstoreError) as caught:
            operation()
        self.assertEqual(caught.exception.exit_code, code)

    def test_rotation_real_publication_and_final_confinement(self):
        self.assertTrue(self.manager.rotate().changed)
        self.assertEqual(self.fixture.source_values(), self.target)
        self.assertTrue(self.manager.final_confinement_verified)
        self.assertTrue(self.closed)  # Coupling never reopens admission.

    def test_restore_uses_guarded_generation_path(self):
        self.assertTrue(self.manager.restore().changed)
        self.assertEqual(self.fixture.source_values(), self.target)
        self.assertTrue(self.manager.final_confinement_verified)

    def test_install_uses_guarded_generation_path(self):
        for path in self.fixture.sources.iterdir():
            path.unlink()
        self.assertTrue(self.manager.install().changed)
        self.assertEqual(self.fixture.source_values(), self.target)
        self.assertTrue(self.manager.final_confinement_verified)

    def test_missing_masks_refuse_before_any_store_mutation(self):
        self.closed = False
        staged = list(self.fixture.staging.iterdir())
        self.error(self.private.EXIT_QUIESCENCE, self.manager.rotate)
        self.assertEqual(self.fixture.source_values(), self.old)
        self.assertEqual(list(self.fixture.journal.iterdir()), [])
        self.assertEqual(list(self.fixture.staging.iterdir()), staged)
        self.assertFalse(self.manager.final_confinement_verified)

    def test_observation_error_refuses_mutation(self):
        self.observation_error = True
        self.error(self.private.EXIT_QUIESCENCE, self.manager.rotate)
        self.assertEqual(self.fixture.source_values(), self.old)
        self.assertFalse(self.manager.final_confinement_verified)

    def test_unexpected_signal_refuses_even_when_namespace_empty(self):
        self.signal = 9
        self.error(self.private.EXIT_QUIESCENCE, self.manager.rotate)
        self.assertEqual(self.fixture.source_values(), self.old)
        self.assertEqual(list(self.fixture.journal.iterdir()), [])

    def test_interruption_before_publication_then_previous_recovery(self):
        with self.assertRaises(self.private.SimulatedInterruption):
            self.manager.rotate(interrupt_after=0)
        self.assertEqual(self.fixture.source_values(), self.old)
        self.assertTrue(self.manager.final_confinement_verified)
        self.manager.recover('previous')
        self.assertEqual(self.fixture.source_values(), self.old)
        self.assertEqual(list(self.fixture.journal.iterdir()), [])

    def test_partial_publication_recovery_refuses_lost_window(self):
        with self.assertRaises(self.private.SimulatedInterruption):
            self.manager.rotate(interrupt_after=1)
        mixed = self.fixture.source_values()
        journal = self.fixture.journal / 'active.json'
        saved = journal.read_bytes()
        self.closed = False
        self.error(self.private.EXIT_QUIESCENCE, self.manager.recover)
        self.assertEqual(self.fixture.source_values(), mixed)
        self.assertEqual(journal.read_bytes(), saved)
        self.assertFalse(self.manager.final_confinement_verified)
        self.closed = True
        self.manager.recover('previous')
        self.assertEqual(self.fixture.source_values(), self.old)
        self.assertTrue(self.manager.final_confinement_verified)

    def test_partial_publication_target_recovery(self):
        with self.assertRaises(self.private.SimulatedInterruption):
            self.manager.rotate(interrupt_after=1)
        self.manager.recover('target')
        self.assertEqual(self.fixture.source_values(), self.target)
        self.assertEqual(list(self.fixture.journal.iterdir()), [])

    def test_interruption_after_commit_preserves_committed_target(self):
        write = self.manager._write_journal
        def cut(journal):
            write(journal)
            if journal['step'] == 'COMMITTED':
                raise self.private.SimulatedInterruption()
        with patch.object(self.manager, '_write_journal', side_effect=cut), \
             self.assertRaises(self.private.SimulatedInterruption):
            self.manager.rotate()
        self.manager.recover('previous')
        self.assertEqual(self.fixture.source_values(), self.target)
        self.assertTrue(self.manager.final_confinement_verified)

    def test_commit_error_rolls_back_only_under_verified_window(self):
        write = self.manager._write_journal
        def fail(journal):
            if journal['step'] == 'COMMITTED':
                raise self.private.CredstoreError(self.private.EXIT_IO)
            write(journal)
        with patch.object(self.manager, '_write_journal', side_effect=fail):
            self.error(self.private.EXIT_IO, self.manager.rotate)
        self.assertEqual(self.fixture.source_values(), self.old)
        self.assertEqual(list(self.fixture.journal.iterdir()), [])
        self.assertTrue(self.manager.final_confinement_verified)

    def test_unexpected_write_exception_retains_primary_and_rolls_back(self):
        atomic = self.manager.io.atomic_write
        failed = False
        def write(parent, basename, payload):
            nonlocal failed
            result = atomic(parent, basename, payload)
            if parent == str(self.fixture.sources) and not failed:
                failed = True
                raise RuntimeError('primary synthetic write failure')
            return result
        with patch.object(self.manager.io, 'atomic_write', side_effect=write), \
             self.assertRaisesRegex(RuntimeError, 'primary synthetic write failure'):
            self.manager.rotate()
        self.assertEqual(self.fixture.source_values(), self.old)
        self.assertTrue(self.manager.final_confinement_verified)

    def test_lost_admission_during_publication_leaves_recoverable_journal(self):
        atomic = self.manager.io.atomic_write
        def write(parent, basename, payload):
            result = atomic(parent, basename, payload)
            if parent == str(self.fixture.sources):
                self.closed = False
            return result
        with patch.object(self.manager.io, 'atomic_write', side_effect=write):
            self.error(self.private.EXIT_QUIESCENCE, self.manager.rotate)
        self.assertEqual(self.fixture.source_values(), [self.target[0], *self.old[1:]])
        self.assertTrue((self.fixture.journal / 'active.json').is_file())
        self.assertFalse(self.manager.final_confinement_verified)
        self.closed = True
        self.manager.recover('previous')
        self.assertEqual(self.fixture.source_values(), self.old)

    def test_recovery_write_error_preserves_journal_for_retry(self):
        with self.assertRaises(self.private.SimulatedInterruption):
            self.manager.rotate(interrupt_after=2)
        atomic = self.manager.io.atomic_write
        writes = 0
        def write(parent, basename, payload):
            nonlocal writes
            if parent == str(self.fixture.sources):
                writes += 1
                if writes == 2:
                    raise self.private.CredstoreError(self.private.EXIT_IO)
            return atomic(parent, basename, payload)
        with patch.object(self.manager.io, 'atomic_write', side_effect=write):
            self.error(self.private.EXIT_IO, self.manager.recover)
        self.assertTrue((self.fixture.journal / 'active.json').exists())
        self.assertTrue(self.manager.final_confinement_verified)
        self.manager.recover('previous')
        self.assertEqual(self.fixture.source_values(), self.old)

    def test_failed_final_confinement_cannot_return_success(self):
        cleanup = self.manager._cleanup_staging
        def lose_window():
            cleanup()
            self.closed = False
        with patch.object(self.manager, '_cleanup_staging', side_effect=lose_window):
            self.error(self.private.EXIT_QUIESCENCE, self.manager.rotate)
        self.assertEqual(self.fixture.source_values(), self.target)  # Commit already durable.
        self.assertFalse(self.manager.final_confinement_verified)

    def test_mode_normalization_and_recovery_obey_same_guard(self):
        for path in self.fixture.sources.iterdir():
            path.chmod(self.private.CURRENT_LEGACY_MODE)
        self.closed = False
        self.error(self.private.EXIT_QUIESCENCE, self.manager.normalize_mode)
        self.assertEqual(self.fixture.source_modes(), [self.private.CURRENT_LEGACY_MODE] * 3)
        self.closed = True
        with self.assertRaises(self.private.SimulatedInterruption):
            self.manager.normalize_mode(interrupt_after=1)
        self.manager.recover('previous')
        self.assertEqual(self.fixture.source_modes(), [self.private.CURRENT_LEGACY_MODE] * 3)
        self.assertTrue(self.manager.final_confinement_verified)

    def test_guard_is_strict_and_production_mapping_is_not_assumed(self):
        manager = self.fixture.manager(mutation_guard=lambda: None)
        self.error(self.private.EXIT_QUIESCENCE, manager.rotate)
        self.assertEqual(self.fixture.source_values(), self.old)
        self.fixture.policy['services']['operations_check'] = 'unqualified.service'
        with self.assertRaisesRegex(ValueError, 'fixed synthetic'):
            confined_manager(self.private.CredstoreManager, self.fixture.policy,
                             groups={}, deadline=time.monotonic() + 5, observer=self.observer)


if __name__ == '__main__':
    unittest.main()
