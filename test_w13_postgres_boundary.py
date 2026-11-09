"""Real PostgreSQL observations, simulated systemd: characterize, not certify fencing."""
import os
import unittest
from unittest.mock import patch

import test_w11_credstore as previous


@unittest.skipUnless(os.environ.get('W13_POSTGRES_SOCKET') and previous.SOURCE,
                     'explicit disposable PostgreSQL 17 and pinned source required')
class PostgresBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        previous.CredstoreCoupling.setUpClass.__func__(cls)
        import psycopg
        cls.pg = psycopg
        cls.params = dict(host=os.environ['W13_POSTGRES_SOCKET'], port=6543,
                          dbname='w13_lab', user='w13_admin', connect_timeout=5,
                          autocommit=True, options='-c statement_timeout=5000')
        cls.db = psycopg.connect(**cls.params)
        if not 170000 <= int(cls.db.execute('SHOW server_version_num').fetchone()[0]) < 180000:
            raise RuntimeError('PostgreSQL 17 required')
        cls.db.execute('CREATE ROLE w13_consumer LOGIN')
        cls.db.execute('CREATE TABLE durable_jobs (status text, lease_expires_at timestamptz)')
        cls.db.execute('CREATE TABLE notification_outbox (status text, lease_expires_at timestamptz, kind text)')
        cls.db.execute('CREATE TABLE pass_operations (status text)')
        cls.db.execute('CREATE TABLE pass_system_state (active_operation_id uuid)')
        cls.addClassCleanup(cls.db.close)

    transport = previous.CredstoreCoupling.transport

    def setUp(self):
        previous.CredstoreCoupling.setUp(self)
        self.db.execute('TRUNCATE durable_jobs, notification_outbox, pass_operations, pass_system_state')
        self.fixture.policy['database']['roles'] = ['w13_consumer']
        outer = self
        base = type(self.fixture.probe)
        class QueryProbe(base):
            def query_count(self, policy, query):
                try:
                    return int(outer.db.execute(query).fetchone()[0])
                except outer.pg.Error:
                    raise outer.private.CredstoreError(outer.private.EXIT_QUIESCENCE) from None
        self.manager.probe = QueryProbe()

    def refused(self):
        with self.assertRaises(self.private.CredstoreError) as caught:
            self.manager.rotate()
        self.assertEqual(caught.exception.exit_code, self.private.EXIT_QUIESCENCE)
        self.assertEqual(self.fixture.source_values(), self.old)
        self.assertFalse((self.fixture.journal / 'active.json').exists())

    def test_empty_real_database_permits_guarded_publication(self):
        self.assertEqual([self.manager.probe.query_count(self.fixture.policy, q)
                          for q in self.manager._database_queries()], [0] * 5)
        self.assertTrue(self.manager.rotate().changed)
        self.assertEqual(self.fixture.source_values(), self.target)
        self.assertTrue(self.manager.final_confinement_verified)

    def test_existing_consumer_connection_refuses_publication(self):
        with self.pg.connect(**dict(self.params, user='w13_consumer')):
            self.refused()

    def test_each_existing_work_counter_refuses_publication(self):
        statements = (
            "INSERT INTO durable_jobs VALUES ('running', NULL)",
            "INSERT INTO notification_outbox VALUES ('sending', now(), 'synthetic')",
            "INSERT INTO pass_operations VALUES ('running')",
            "INSERT INTO pass_system_state VALUES ('00000000-0000-0000-0000-000000000001')",
            "INSERT INTO notification_outbox VALUES ('pending', NULL, 'telegram_new_notes')",
        )
        for sql in statements:
            with self.subTest(counter=statements.index(sql)):
                self.db.execute('TRUNCATE durable_jobs, notification_outbox, pass_operations, pass_system_state')
                for path in self.fixture.staging.iterdir():
                    path.unlink()
                self.fixture.write_staging()
                self.db.execute(sql)
                self.refused()

    def test_query_observation_error_refuses_publication(self):
        self.db.execute('ALTER TABLE pass_system_state RENAME TO w13_hidden_state')
        try:
            self.refused()
        finally:
            self.db.execute('ALTER TABLE w13_hidden_state RENAME TO pass_system_state')

    def test_zero_snapshot_does_not_fence_later_external_connection(self):
        check = self.manager._require_quiescence
        later = []
        def after_snapshot():
            check()
            later.append(self.pg.connect(**dict(self.params, user='w13_consumer')))
        try:
            with patch.object(self.manager, '_require_quiescence', side_effect=after_snapshot):
                result = self.manager.rotate()
            # A passing characterization records a gap; it is NOT safe-rotation certification.
            self.assertTrue(result.changed)
            self.assertEqual(self.fixture.source_values(), self.target)
            self.assertEqual(self.manager.probe.query_count(
                self.fixture.policy, self.manager._database_queries()[0]), 1)
        finally:
            for connection in later:
                connection.close()


if __name__ == '__main__':
    unittest.main()
