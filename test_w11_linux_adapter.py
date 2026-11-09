"""Adapter regressions: real killed owners, simulated observation transport only."""
import contextlib
import io
import os
from pathlib import Path
import types
import unittest
from unittest.mock import patch

import w11_linux_coupling as adapter


class LinuxAdapterTests(unittest.TestCase):
    def test_non_linux_refused_before_setup(self):
        with patch.object(adapter.sys, 'platform', 'darwin'):
            with self.assertRaises(RuntimeError):
                adapter.main()

    @unittest.skipUnless(os.environ.get('W11_CREDSTORE_SOURCE'), 'explicit pinned source required')
    def test_actual_owner_death_and_recovery_with_simulated_observations(self):
        source = str(Path(os.environ['W11_CREDSTORE_SOURCE']).resolve())
        output = io.StringIO()
        with (patch.dict(os.environ, W11_CREDSTORE_SOURCE=source),
              patch.object(adapter.window, 'OBSERVER_MODULE', types.SimpleNamespace(STOP_STARTED_US=1)),
              patch.object(adapter.obs, 'namespace_quiescent', return_value=True),
              patch.object(adapter.obs, 'general_stop_evidence', return_value={'expected_pre_sigterm': True}),
              patch.object(adapter.bridge, 'namespace_quiescent', return_value=True),
              patch.object(adapter.bridge, 'general_stop_evidence', return_value={'expected_pre_sigterm': True}),
              patch.object(adapter.window, 'deny_starts'),
              contextlib.redirect_stdout(output)):
            adapter.exercise({})
        self.assertEqual(output.getvalue().count('W11_REAL_MANAGER_CUT_PASS='), 5)
        self.assertIn('NOT_DB_FENCING_CERTIFIED', output.getvalue())


if __name__ == '__main__':
    unittest.main()
