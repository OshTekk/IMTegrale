"""Derived synthetic candidate: durable DB admission separate from quiescence.

Cooperating controllers share the manager lock and a DB advisory lock. Owners,
superusers, single-user mode, HA promotion and external ALTER DATABASE actors
remain trusted/excluded authorities, not fenced by this candidate.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat

import psycopg
from psycopg import sql

from w104_credstore import confined_manager


class AdmissionError(RuntimeError):
    """Redacted refusal: never include connection parameters or SQL errors."""


class PostgresAdmission:
    def __init__(self, control_params, database, receipt):
        if (not isinstance(database, str) or not database or len(database) > 63
                or database in {'postgres','template0','template1'}):
            raise AdmissionError('invalid database identity')
        self.params = dict(control_params, autocommit=True, connect_timeout=5,
                           options='-c statement_timeout=5000 -c lock_timeout=5000')
        self.database = database
        self.receipt = Path(receipt)
        self.control = None
        self.observer = None
        self.identity = None
        self.queries = None
        self.counts = None
        self.poisoned = False

    def close(self):
        # Closure is intentionally never reversed, on success or failure.
        for connection in (self.observer, self.control):
            if connection is not None:
                connection.close()
        self.observer = self.control = None

    def _directory(self):
        fd = os.open(self.receipt.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        s = os.fstat(fd)
        if s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) & 0o022:
            os.close(fd)
            raise AdmissionError('unsafe receipt directory')
        return fd

    def _read_receipt(self):
        folder = self._directory()
        try:
            try:
                fd = os.open(self.receipt.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder)
            except FileNotFoundError:
                return None
            try:
                s = os.fstat(fd)
                if (not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid()
                        or stat.S_IMODE(s.st_mode) != 0o600 or s.st_nlink != 1 or s.st_size > 8192):
                    raise AdmissionError('unsafe receipt')
                data = os.read(fd, 8193)
                if len(data) != s.st_size:
                    raise AdmissionError('incomplete receipt')
                return json.loads(data)
            finally:
                os.close(fd)
        finally:
            os.close(folder)

    def _write_receipt(self, value):
        folder = self._directory()
        name = '.w16-receipt-' + secrets.token_hex(12)
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
            try:
                raw = json.dumps(value, sort_keys=True).encode()
                offset = 0
                while offset < len(raw):
                    offset += os.write(fd, raw[offset:])
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(name, self.receipt.name, src_dir_fd=folder, dst_dir_fd=folder)
            os.fsync(folder)
        finally:
            try:
                os.unlink(name, dir_fd=folder)
            except FileNotFoundError:
                pass
            os.close(folder)

    @staticmethod
    def _query_hash(queries):
        return hashlib.sha256(json.dumps(list(queries), separators=(',', ':')).encode()).hexdigest()

    def _catalog(self):
        return self.control.execute(
            'SELECT oid::bigint, datallowconn FROM pg_database WHERE datname=%s',
            (self.database,)).fetchone()

    def _target_pids(self):
        # Lock scan FIRST, activity scan SECOND. A startup that already passed
        # allowconn holds the database object lock until pgstat_bestart precedes
        # startup commit. Scanning activity first could miss that handover.
        locks = self.control.execute(
            "SELECT DISTINCT pid FROM pg_locks WHERE locktype='object' "
            "AND classid='pg_database'::regclass AND objid=%s AND objsubid=0 AND pid IS NOT NULL",
            (self.identity['database_oid'],)).fetchall()
        active = self.control.execute(
            'SELECT pid FROM pg_stat_activity WHERE datid=%s',
            (self.identity['database_oid'],)).fetchall()
        return {row[0] for row in locks + active}

    def _no_prepared_work(self):
        checks = (
            ('SELECT count(*) FROM pg_prepared_xacts WHERE database=%s', (self.database,)),
            ('SELECT count(*) FROM pg_replication_slots WHERE database=%s', (self.database,)),
            ('SELECT count(*) FROM pg_subscription WHERE subdbid=%s', (self.identity['database_oid'],)),
            # Origins have no database identity: conservative cluster-wide refusal.
            ('SELECT count(*) FROM pg_replication_origin', ()),
        )
        for query, params in checks:
            if self.control.execute(query, params).fetchone() != (0,):
                raise AdmissionError('persistent PostgreSQL work remains')

    def _live_closed(self):
        row = self._catalog()
        if row != (self.identity['database_oid'], False):
            raise AdmissionError('admission closure lost or database replaced')
        if self._target_pids():
            raise AdmissionError('target backend remains')
        self._no_prepared_work()

    def _receipt_value(self):
        return dict(version=1, identity=self.identity,
                    query_hash=self._query_hash(self.queries), counts=list(self.counts))

    def ensure(self, queries):
        """Called under manager exclusive lock and before every mutation.

        Recovery adopts only an already closed target plus a matching protected,
        fsynced post-termination receipt. No receipt means refusal, not reopening.
        Receipt continuity assumes excluded privileged actors do not reopen/reclose.
        """
        if self.poisoned:
            raise AdmissionError('admission observation previously failed')
        try:
            self._ensure(tuple(queries))
        except Exception:
            self.poisoned = True
            if self.observer is not None:
                self.observer.close()
                self.observer = None
            raise AdmissionError('database admission or quiescence unverified') from None

    def _ensure(self, queries):
        if not queries or any(not isinstance(q, str) for q in queries):
            raise AdmissionError('missing workload queries')
        if self.control is None:
            self.control = psycopg.connect(**self.params)
            if self.control.execute('SELECT current_database(), pg_is_in_recovery()').fetchone() != ('postgres', False):
                raise AdmissionError('independent postgres control connection required')
            system = self.control.execute('SELECT system_identifier::text FROM pg_control_system()').fetchone()[0]
            row = self._catalog()
            if row is None:
                raise AdmissionError('target database missing')
            self.identity = dict(system_identifier=system, database_oid=row[0], database=self.database)
            key = 0x5731360000000000 | row[0]
            if self.control.execute('SELECT pg_try_advisory_lock(%s)', (key,)).fetchone() != (True,):
                raise AdmissionError('another admission controller owns database')
            self.queries = queries
            prior = self._read_receipt()
            if prior is not None:
                if (not isinstance(prior, dict) or set(prior) != {'version', 'identity', 'query_hash', 'counts'}
                        or type(prior['version']) is not int or prior['version'] != 1 or prior['identity'] != self.identity
                        or prior['query_hash'] != self._query_hash(queries)
                        or prior['counts'] != [0] * len(queries)
                        or any(type(c) is not int for c in prior['counts'])):
                    raise AdmissionError('receipt mismatch')
                self.counts = tuple(prior['counts'])
                self._live_closed()
            else:
                # A closed database without a receipt cannot be safely inspected by
                # opening it. Controller death here is an explicit fail-closed state.
                if row[1] is not True:
                    raise AdmissionError('closed database lacks workload proof')
                self.observer = psycopg.connect(**dict(self.params, dbname=self.database))
                self.observer.execute('SET default_transaction_read_only=on')
                observer_pid = self.observer.info.backend_pid
                self.control.execute(sql.SQL('ALTER DATABASE {} ALLOW_CONNECTIONS false').format(sql.Identifier(self.database)))
                if self._catalog() != (row[0], False):
                    raise AdmissionError('durable closure unverified')
                pids = self._target_pids() - {observer_pid}
                for pid in sorted(pids):
                    if self.control.execute('SELECT pg_terminate_backend(%s, 5000)', (pid,)).fetchone() != (True,):
                        raise AdmissionError('backend termination unverified')
                if self._target_pids() - {observer_pid}:
                    raise AdmissionError('backend remains after termination')
                self._no_prepared_work()
                self.counts = tuple(self.observer.execute(q).fetchone()[0] for q in queries)
                if any(type(c) is not int or c != 0 for c in self.counts):
                    raise AdmissionError('workload remains after termination')
                self._write_receipt(self._receipt_value())
                self.observer.close()
                self.observer = None
                self._live_closed()
        if queries != self.queries:
            raise AdmissionError('workload policy changed')
        self._live_closed()
        if self._read_receipt() != self._receipt_value():
            raise AdmissionError('durable workload receipt lost')

    def query_count(self, query):
        if self.poisoned or self.counts is None or query not in self.queries:
            raise AdmissionError('workload observation unavailable')
        self._live_closed()
        return self.counts[self.queries.index(query)]


def admitted_manager(module, policy, *, admission, **kwargs):
    """Corrected candidate; historical default CLI and private sources unchanged."""
    if policy.get('database', {}).get('name') != admission.database:
        raise module.CredstoreError(module.EXIT_QUIESCENCE)

    class GuardedManager(module.CredstoreManager):
        def _require_window(self):
            super()._require_window()
            try:
                admission.ensure(self._database_queries())
            except AdmissionError:
                raise module.CredstoreError(module.EXIT_QUIESCENCE) from None

    manager = confined_manager(GuardedManager, policy, **kwargs)
    original_probe = manager.probe

    class GuardedProbe:
        def __getattr__(self, name):
            return getattr(original_probe, name)

        def query_count(self, policy, query):
            try:
                return admission.query_count(query)
            except Exception:
                raise module.CredstoreError(module.EXIT_QUIESCENCE) from None

    manager.probe = GuardedProbe()
    return manager


def admitted_main(module, argv, *, admission, **kwargs):
    """Explicit corrected command embedding, including the historical apply gate."""
    return module.safe_main(
        argv, manager_factory=lambda policy: admitted_manager(
            module, policy, admission=admission, **kwargs))
