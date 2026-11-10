"""Corrected candidate with real PostgreSQL; systemd local fake or real W17 window."""
import contextlib
import io
import json
import os
from pathlib import Path
import select
import signal
import socket
import time
import unittest
from unittest.mock import patch

import psycopg
from psycopg import pq, sql
import test_w11_credstore as previous
import w10_observe as obs
from w16_postgres_admission import AdmissionError, PostgresAdmission, admitted_manager, admitted_main

PARAMS = None
RESTART = None
WINDOW = None


@unittest.skipUnless(previous.SOURCE, 'explicit pinned private source required')
class AdmissionCandidate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if PARAMS is None:
            raise RuntimeError('owned disposable PostgreSQL context required; no skipped qualification')
        previous.CredstoreCoupling.setUpClass.__func__(cls)
        cls.serial = 0

    transport = previous.CredstoreCoupling.transport

    def setUp(self):
        if WINDOW is None:
            previous.CredstoreCoupling.setUp(self)
            self.deadline = time.monotonic()+30
            self.groups = {}
        else:
            self.fixture = self.Fixture()
            self.addCleanup(self.fixture.close)
            self.old = self.fixture.write_sources()
            self.target = self.fixture.write_staging()
            services = self.fixture.policy['services']
            mapping = dict(zip(services['application_services'], obs.UNITS[:5], strict=True))
            services['application_services'] = list(obs.UNITS[:5])
            services['consumers'] = {k:[mapping[n] for n in names] for k,names in services['consumers'].items()}
            services['stop_for_mutation'] = [mapping[n] for n in services['stop_for_mutation']]
            services['operations_check'] = 'w9-proof-trigger.service'
            self.deadline, self.groups, self.observer = WINDOW
        type(self).serial += 1
        self.database = 'w16_lab_' + str(self.serial)
        self.params = dict(PARAMS)
        self.admin = psycopg.connect(**self.params)
        self.addCleanup(self.admin.close)
        self.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(self.database)))
        setup = psycopg.connect(**dict(self.params, dbname=self.database))
        setup.execute('CREATE TABLE durable_jobs (status text, lease_expires_at timestamptz)')
        setup.execute('CREATE TABLE notification_outbox (status text, lease_expires_at timestamptz, kind text)')
        setup.execute('CREATE TABLE pass_operations (status text)')
        setup.execute('CREATE TABLE pass_system_state (active_operation_id uuid)')
        setup.execute('CREATE TABLE synthetic_marker (id integer)')
        setup.execute('GRANT ALL ON ALL TABLES IN SCHEMA public TO w16_consumer')
        setup.close()
        self.fixture.policy['database']['roles'] = ['w16_consumer']
        self.fixture.policy['database']['name'] = self.database
        self.receipt = self.fixture.journal / 'w16-admission.json'
        self.new_candidate()

    def new_candidate(self):
        self.gate = PostgresAdmission(self.params, self.database, self.receipt)
        self.addCleanup(self.gate.close)
        self.manager = admitted_manager(
            self.private, self.fixture.policy, admission=self.gate,
            groups=self.groups, deadline=self.deadline, observer=self.observer,
            expected_uid=os.getuid(), expected_gid=os.getgid(),
            probe=self.fixture.probe, xattr_reader=lambda fd: ())

    def consumer(self):
        c = psycopg.connect(**dict(self.params, user='w16_consumer', password='w16-synthetic-only', dbname=self.database))
        self.addCleanup(c.close)
        return c

    def assert_db_closed(self):
        self.assertEqual(self.admin.execute('SELECT datallowconn FROM pg_database WHERE datname=%s', (self.database,)).fetchone(), (False,))
        self.assertEqual(self.admin.execute('SELECT count(*) FROM pg_stat_activity WHERE datname=%s', (self.database,)).fetchone(), (0,))
        for user in ('w16_consumer','w16_admin'):
            with self.assertRaises(psycopg.OperationalError) as caught:
                psycopg.connect(**dict(self.params, dbname=self.database, user=user, password='w16-synthetic-only'))
            self.assertIn('not currently accepting connections', str(caught.exception))

    def refused(self, operation=None, *, unchanged=True):
        before = self.fixture.source_values()
        with self.assertRaises(self.private.CredstoreError) as caught:
            (operation or self.manager.rotate)()
        self.assertEqual(caught.exception.exit_code, self.private.EXIT_QUIESCENCE)
        if unchanged:
            self.assertEqual(self.fixture.source_values(), before)

    def test_late_w13_connection_is_refused_before_publication(self):
        check = self.manager._require_quiescence
        late = []
        def after_snapshot():
            check()
            with self.assertRaises(psycopg.OperationalError):
                self.consumer()
            late.append(True)
        with patch.object(self.manager, '_require_quiescence', side_effect=after_snapshot):
            self.assertTrue(self.manager.rotate().changed)
        self.assertTrue(late)
        self.assertEqual(self.fixture.source_values(), self.target)
        self.assertTrue(self.manager.final_confinement_verified)
        self.assert_db_closed()

    def test_existing_transaction_and_query_are_terminated(self):
        old = self.consumer()
        old.execute('BEGIN')
        old.execute('INSERT INTO synthetic_marker VALUES (16)')
        active = self.consumer()
        active.pgconn.send_query(b'SELECT pg_sleep(20)')
        deadline = time.monotonic()+3
        while time.monotonic()<deadline:
            if self.admin.execute('SELECT wait_event FROM pg_stat_activity WHERE pid=%s',(active.info.backend_pid,)).fetchone()==('PgSleep',):
                break
            time.sleep(.01)
        else: self.fail('real active-query barrier missing')
        self.assertTrue(self.manager.rotate().changed)
        with self.assertRaises(psycopg.Error): old.execute('COMMIT')
        self.assert_db_closed()

    def test_scram_startup_in_flight_is_refused(self):
        info = psycopg.conninfo.make_conninfo(**{k:v for k,v in dict(self.params,dbname=self.database,user='w16_consumer',password='w16-synthetic-only').items() if k!='autocommit'})
        pg = pq.PGconn.connect_start(info.encode())
        self.addCleanup(pg.finish)
        self.assertEqual(pg.connect_poll(),pq.PollingStatus.READING)
        self.assertTrue(select.select([pg.socket],[],[],3)[0])
        peek=socket.fromfd(pg.socket,socket.AF_UNIX,socket.SOCK_STREAM)
        try: challenge=peek.recv(9,socket.MSG_PEEK)
        finally: peek.close()
        self.assertEqual(challenge[:1],b'R')
        self.assertEqual(int.from_bytes(challenge[5:9],'big'),10)
        self.manager.rotate()
        end=time.monotonic()+3
        while time.monotonic()<end:
            state=pg.connect_poll()
            if state in (pq.PollingStatus.OK,pq.PollingStatus.FAILED): break
            select.select([pg.socket] if state==pq.PollingStatus.READING else [],[pg.socket] if state==pq.PollingStatus.WRITING else [],[],.05)
        else: self.fail('startup outcome unobserved')
        self.assertEqual(state,pq.PollingStatus.FAILED)
        self.assertIn(b'not currently accepting connections',pg.error_message)
        self.assert_db_closed()

    def test_postauth_startup_invisible_to_activity_is_terminated(self):
        info=psycopg.conninfo.make_conninfo(**{k:v for k,v in dict(self.params,dbname=self.database,options='-c post_auth_delay=3').items() if k!='autocommit'})
        pg=pq.PGconn.connect_start(info.encode())
        self.addCleanup(pg.finish)
        self.assertEqual(pg.connect_poll(),pq.PollingStatus.READING)
        oid=self.admin.execute('SELECT oid FROM pg_database WHERE datname=%s',(self.database,)).fetchone()[0]
        end=time.monotonic()+2
        while time.monotonic()<end:
            locks=self.admin.execute("SELECT pid FROM pg_locks WHERE locktype='object' AND classid='pg_database'::regclass AND objid=%s AND pid IS NOT NULL",(oid,)).fetchall()
            if locks: break
            time.sleep(.005)
        else: self.fail('postauth startup lock unobserved')
        self.assertEqual(self.admin.execute('SELECT count(*) FROM pg_stat_activity WHERE datid=%s',(oid,)).fetchone(),(0,))
        self.assertTrue(self.manager.rotate().changed)
        end=time.monotonic()+3
        while time.monotonic()<end:
            state=pg.connect_poll()
            if state in (pq.PollingStatus.OK,pq.PollingStatus.FAILED): break
            select.select([pg.socket] if state==pq.PollingStatus.READING else [],[pg.socket] if state==pq.PollingStatus.WRITING else [],[],.05)
        else: self.fail('postauth startup outcome missing')
        if state == pq.PollingStatus.OK:
            # Startup protocol bytes may already be buffered when SIGTERM kills
            # the backend. An apparent connect success must still be unable to write.
            result=pg.exec_(b'INSERT INTO synthetic_marker VALUES (19)')
            self.assertEqual(result.status,pq.ExecStatus.FATAL_ERROR)
        else:
            self.assertEqual(state,pq.PollingStatus.FAILED)
        self.assert_db_closed()

    def test_logical_slot_survives_session_and_refuses_publication(self):
        c=psycopg.connect(**dict(self.params,dbname=self.database))
        c.execute("SELECT * FROM pg_create_logical_replication_slot('w16_slot','pgoutput')")
        c.close()
        self.refused()
        self.assert_db_closed()
        self.assertEqual(self.admin.execute('SELECT count(*) FROM pg_replication_slots WHERE database=%s',(self.database,)).fetchone(),(1,))

    def test_disabled_subscription_refuses_publication_without_connecting(self):
        c=psycopg.connect(**dict(self.params,dbname=self.database))
        c.execute("CREATE SUBSCRIPTION w16_sub CONNECTION 'host=/nonexistent-w16-synthetic' PUBLICATION w16_pub WITH (connect=false,enabled=false,slot_name=NONE)")
        c.close()
        self.refused()
        self.assert_db_closed()
        self.assertEqual(self.admin.execute('SELECT count(*) FROM pg_subscription WHERE subdbid=(SELECT oid FROM pg_database WHERE datname=%s)',(self.database,)).fetchone(),(1,))

    def test_z_origin_state_refuses_without_automatic_resolution(self):
        self.admin.execute("SELECT pg_replication_origin_create('w16_origin')")
        self.refused()
        self.assert_db_closed()
        self.assertEqual(self.admin.execute("SELECT count(*) FROM pg_replication_origin WHERE roname='w16_origin'").fetchone(),(1,))

    def test_prepared_work_refuses_publication_and_survives_restart(self):
        c=self.consumer()
        c.execute('BEGIN')
        c.execute('INSERT INTO synthetic_marker VALUES (15)')
        c.execute("PREPARE TRANSACTION 'w16_prepared'")
        c.close()
        self.refused()
        self.assert_db_closed()
        self.assertFalse(self.receipt.exists())
        self.assertFalse((self.fixture.journal/'active.json').exists())
        self.assertEqual(self.admin.execute('SELECT count(*) FROM pg_prepared_xacts WHERE database=%s',(self.database,)).fetchone(),(1,))
        self.admin.close()
        self.gate.close()
        RESTART()
        self.admin=psycopg.connect(**self.params)
        self.addCleanup(self.admin.close)
        self.new_candidate()
        self.refused()
        self.assert_db_closed()
        self.assertEqual(self.admin.execute('SELECT count(*) FROM pg_prepared_xacts WHERE database=%s',(self.database,)).fetchone(),(1,))

    def test_sql_observation_error_closes_and_refuses(self):
        c=psycopg.connect(**dict(self.params,dbname=self.database))
        c.execute('DROP TABLE pass_system_state')
        c.close()
        self.refused()
        self.assert_db_closed()
        self.assertFalse(self.receipt.exists())

    def test_partial_publication_recovers_after_controller_sigkill_and_db_crash(self):
        child=os.fork()
        if child==0:
            try:
                write=self.manager._write_journal
                def killed(journal):
                    write(journal)
                    if journal['step']=='REPLACING': os.kill(os.getpid(),signal.SIGKILL)
                self.manager._write_journal=killed
                self.manager.rotate()
            except BaseException: os._exit(2)
            os._exit(3)
        _,status=os.waitpid(child,0)
        self.assertTrue(os.WIFSIGNALED(status) and os.WTERMSIG(status)==signal.SIGKILL)
        self.assertTrue(self.receipt.exists())
        self.assertTrue((self.fixture.journal/'active.json').exists())
        self.admin.close()
        RESTART()
        self.admin=psycopg.connect(**self.params)
        self.addCleanup(self.admin.close)
        self.new_candidate()
        self.manager.recover('previous')
        self.assertEqual(self.fixture.source_values(),self.old)
        self.assertFalse((self.fixture.journal/'active.json').exists())
        self.assert_db_closed()

    def test_crash_before_receipt_refuses_recovery_without_reopening(self):
        def die(_): raise RuntimeError('synthetic death before receipt fsync')
        with patch.object(self.gate,'_write_receipt',side_effect=die): self.refused()
        self.gate.close()
        self.new_candidate()
        self.refused(self.manager.recover)
        self.assert_db_closed()
        self.assertFalse(self.receipt.exists())

    def test_lost_admission_midpublication_preserves_journal_and_refuses_recovery(self):
        atomic=self.manager.io.atomic_write
        changed=[]
        def reopen(parent,name,data):
            result=atomic(parent,name,data)
            if parent==str(self.fixture.sources) and not changed:
                changed.append(True)
                self.admin.execute(sql.SQL('ALTER DATABASE {} ALLOW_CONNECTIONS true').format(sql.Identifier(self.database)))
            return result
        with patch.object(self.manager.io,'atomic_write',side_effect=reopen):
            self.refused(unchanged=False)
        journal=(self.fixture.journal/'active.json').read_bytes()
        self.gate.close()
        self.new_candidate()
        self.refused(self.manager.recover)
        self.assertEqual((self.fixture.journal/'active.json').read_bytes(),journal)

    def test_receipt_loss_or_corruption_refuses_before_source_changes(self):
        self.manager.rotate()
        self.gate.close()
        self.receipt.write_text('{}')
        self.new_candidate()
        self.refused(self.manager.recover)
        self.assert_db_closed()

    def test_advisory_controller_contention_refuses_before_closure(self):
        oid=self.admin.execute('SELECT oid::bigint FROM pg_database WHERE datname=%s',(self.database,)).fetchone()[0]
        self.admin.execute('SELECT pg_advisory_lock(%s)',(0x5731360000000000|oid,))
        self.refused()
        self.assertEqual(self.admin.execute('SELECT datallowconn FROM pg_database WHERE datname=%s',(self.database,)).fetchone(),(True,))

    def test_receipt_symlink_refuses_without_touching_target(self):
        outside=self.fixture.root/'synthetic-untouched'
        outside.write_text('unchanged')
        self.receipt.symlink_to(outside)
        self.refused()
        self.assertEqual(outside.read_text(),'unchanged')

    def test_policy_database_mismatch_refuses_before_any_closure(self):
        self.fixture.policy['database']['name']='w16_wrong_target'
        with self.assertRaises(self.private.CredstoreError): self.new_candidate()
        self.assertEqual(self.admin.execute('SELECT datallowconn FROM pg_database WHERE datname=%s',(self.database,)).fetchone(),(True,))
        self.assertEqual(self.fixture.source_values(),self.old)

    def test_control_and_template_databases_cannot_be_targets(self):
        for name in ('postgres','template0','template1'):
            with self.assertRaises(AdmissionError): PostgresAdmission(self.params,name,self.receipt)

    def test_command_apply_gate_and_dispatch_use_corrected_candidate(self):
        class RootOS:
            def __getattr__(self,name): return getattr(os,name)
            def geteuid(self): return 0
        def command(apply):
            stream=io.StringIO()
            args=['rotate']+(['--apply'] if apply else [])
            with (patch.object(self.private,'os',RootOS()),
                  patch.object(self.private,'load_policy',return_value=self.fixture.policy),
                  contextlib.redirect_stdout(stream)):
                code=admitted_main(self.private,args,admission=self.gate,
                    groups=self.groups,deadline=self.deadline,observer=self.observer,
                    expected_uid=os.getuid(),expected_gid=os.getgid(),
                    probe=self.fixture.probe,xattr_reader=lambda fd:())
            report=json.loads(stream.getvalue())
            self.assertEqual(set(report),self.private.OUTPUT_FIELDS)
            return code
        self.assertEqual(command(False),self.private.EXIT_APPLY_REQUIRED)
        self.assertEqual(self.fixture.source_values(),self.old)
        self.assertEqual(self.admin.execute('SELECT datallowconn FROM pg_database WHERE datname=%s',(self.database,)).fetchone(),(True,))
        self.assertEqual(command(True),0)
        self.assertEqual(self.fixture.source_values(),self.target)
        self.assert_db_closed()

    def test_control_loss_retains_journal_until_fresh_guarded_recovery(self):
        with self.assertRaises(self.private.SimulatedInterruption):
            self.manager.rotate(interrupt_after=1)
        journal=(self.fixture.journal/'active.json').read_bytes()
        self.gate.control.close()
        self.refused(self.manager.recover)
        self.assertEqual((self.fixture.journal/'active.json').read_bytes(),journal)
        self.gate.close()
        self.new_candidate()
        self.manager.recover('previous')
        self.assertEqual(self.fixture.source_values(),self.old)
        self.assert_db_closed()

    def test_install_restore_and_normalize_use_same_admission(self):
        for path in self.fixture.paths: path.unlink()
        self.assertTrue(self.manager.install().changed)
        self.target=self.fixture.write_staging(prefix=b'restore')
        self.assertTrue(self.manager.restore().changed)
        for path in self.fixture.paths: path.chmod(self.private.CURRENT_LEGACY_MODE)
        self.manager.normalize_mode()
        self.assertEqual(self.fixture.source_modes(),[self.private.FINAL_MODE]*3)
        self.assert_db_closed()


def workload_case(statement):
    def test(self):
        c=psycopg.connect(**dict(self.params,dbname=self.database))
        c.execute(statement)
        c.close()
        self.refused()
        self.assert_db_closed()
        self.assertFalse(self.receipt.exists())
    return test

for index, statement in enumerate((
    "INSERT INTO durable_jobs VALUES ('running',NULL)",
    "INSERT INTO notification_outbox VALUES ('sending',now(),'synthetic')",
    "INSERT INTO pass_operations VALUES ('running')",
    "INSERT INTO pass_system_state VALUES ('00000000-0000-0000-0000-000000000001')",
    "INSERT INTO notification_outbox VALUES ('pending',NULL,'telegram_new_notes')",
)):
    setattr(AdmissionCandidate,'test_workload_counter_'+str(index)+'_refuses',workload_case(statement))
