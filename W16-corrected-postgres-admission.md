# W16 — corrected synthetic manager candidate

The W13 late-connection publication gap is corrected in the explicitly derived
`w16_postgres_admission.admitted_manager` candidate, including the historical
command parser through `admitted_main`. The original private manager, W9 and the
historical default shell entrypoint remain preserved; this is not a deployment.

Twenty-five real PostgreSQL 17.11 cases pass, zero failures/errors/skips, plus
47/47 historical local regressions. Each final case owns a fresh UTF-8 cluster
with a private Unix socket and no TCP. No pre-existing database is used. Exact
final evidence: `.git/w16-local-pg-20261110T212044Z/result.json`; source snapshots and SHA-256
identities are retained alongside it. All clusters were independently checked
stopped before removal of their own generated resources. All earlier local
attempts, including harness failures, remain in the durable local ledger.

Correction order under the existing manager exclusive lock:
1. Verify the real fixed systemd confinement guard. Bind policy database.name
   to the explicitly selected target; refuse postgres/template control targets.
2. Connect independently to postgres, require a writable primary, identify the
   cluster system identifier and database OID, and take a cooperative advisory
   lock. This lock coordinates controllers; it is not the durable admission gate.
3. Keep one read-only administrative target observer, commit ALTER DATABASE
   ALLOW_CONNECTIONS false and independently observe the committed setting.
4. Scan database-object startup locks FIRST, then target activity; terminate all
   other observed backends with a positive bounded termination wait. Recheck
   both representations, refusing incomplete observations or surviving backends.
5. Refuse prepared transactions, target replication slots (even inactive), target
   subscriptions (even disabled), and replication origins. Origins have no database
   identity, so their refusal is conservatively cluster-wide; nothing is resolved.
6. Execute all original workload queries through the retained read-only target
   observer after termination. Any nonzero/unknown result refuses publication.
   Write the identity/query-bound all-zero workload receipt in the protected
   journal directory with file fsync, atomic replacement and directory fsync.
7. Close the observer and independently require durable closure, no target
   backend/startup lock, no persistent PostgreSQL work and an intact receipt.
   These checks run through the manager's existing before_mutation hook before
   source writes, journal writes, rollback, recovery, mode changes and cleanup.

Database admission is the committed database setting, not a repeated counter.
Quiescence observations and startup-lock coverage remain separate checks.
PostgreSQL 17.11's startup ordering explains why pg_stat_activity alone misses
post-authentication startup: see the pinned
[InitPostgres source](https://github.com/postgres/postgres/blob/REL_17_11/src/backend/utils/init/postinit.c).
The lock-to-activity handover is sampled in that order. Normal connection startup
held at SCRAM is refused after closure; a startup already past admission is
terminated via its database-object lock before publication. Buffered protocol
bytes can still look like connect success, so the test also requires any write
to fail and independently verifies that no backend remains.

Recovery uses a fresh control connection to postgres and a matching fsynced
receipt while the target stays closed. Actual controller SIGKILL at a durable
publication boundary, immediate server crash, WAL redo and guarded source
recovery to the previous generation pass. Lost administrative connectivity,
reopening, missing/corrupt/unsafe receipt, query error, persistent work or policy
identity mismatch refuses mutation and preserves the active journal. A crash
before the workload receipt deliberately requires explicit investigation; it
never reopens admission to obtain a fresh snapshot. No automatic reopening,
COMMIT PREPARED, ROLLBACK PREPARED, slot deletion or subscription cleanup exists.

The receipt is protected by the trusted filesystem owner, not a hostile-owner
signature. Its continuity assumes excluded owners/superusers do not reopen and
reclose admission behind the controller. Database owners, filesystem/single-user
administrators, arbitrary privileged background workers and HA promotion remain
outside this synthetic contract. Counts do not fence those actors. Deployed
source mappings, old binaries, real administrative authority and production are
not certified. W17 validates the composed candidate inside the real Linux
systemd window; it has its own three-run durable budget.
