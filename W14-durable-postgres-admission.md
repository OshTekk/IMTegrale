# W14 — durable synthetic PostgreSQL admission; no rotation certification

PostgreSQL 17.11 (Homebrew), a newly initialized UTF-8 cluster, private Unix
socket only, no TCP or existing database. The disposable administrative identity
is created by initdb; this does not establish any deployed administrative authority.
No manager/CLI/runtime source was changed, and W13's manager race remains unfixed.

Eleven assertions passed in `.git/w14-local-pg-20261110T072601Z/`.
The exact runner is retained there as `proof.py`, SHA-256
`93694006cea8c87a73e11c572fa8577fb43886302ef2b65de6ce6ae2070b4e0e`.

Observed in order:
- Rolled-back ALTER DATABASE does not close admission; commit acknowledgement is required.
- A consumer has an uncommitted INSERT, another executes pg_sleep, and a third
  startup has received a real SCRAM authentication challenge but has not answered it.
- A separate controller commits `ALTER DATABASE w14_lab ALLOW_CONNECTIONS false`,
  then is killed with SIGKILL. An independent administrative connection observes
  datallowconn=false; new ordinary and superuser connections to that database fail
  with the specific database-admission refusal.
- The startup held before closure completes authentication after closure and is
  refused. This is one deterministic in-flight interleaving, not an exhaustive
  enumeration of PostgreSQL startup races.
- The previously admitted transaction can still execute SQL after closure.
  Closure alone is not session eviction. Explicit pg_terminate_backend(pid,5000)
  acknowledges exit of both known backends, and the old transaction cannot commit.
- A fresh administrator connects to postgres, observes continued closure, and
  never reopens the target. An immediate server stop followed by restart exercises
  real WAL crash recovery (redo observed), with the target still closed.
- Offline single-user inspection finds zero committed marker rows. This is a
  privileged diagnostic bypass, not a normal recovery connection or a security
  boundary against filesystem administrators. A final normal restart again
  refuses target connections without any reopening.

All owned cluster files were removed only after pg_ctl independently reported no
server running. The full assertion list, lifecycle logs and result.json remain in
the journal. Two prior harness failures are preserved, not discarded: SQL_ASCII
bytes affected the initial comparison/result serialization; psycopg/libpq does
not expose sqlstate for the tested initial-connection error, so the corrected
assertion checks the specific target-database refusal. Three LOCAL attempts;
ZERO remote runs, with a durable `.git/w14-remote-run-ledger.json` (0/3).

Database admission and observed quiescence are separate properties. No counter,
including repeated zero observations, is presented as fencing. No global rotation,
Linux/systemd, deployed mappings, historical binary, privileged actor, production,
HA/replica or rollout qualification is claimed. A database owner/superuser can
change admission again; filesystem/single-user administration is outside it.
No automatic reopen or automatic transaction resolution was introduced.

The documented admission setting is described by PostgreSQL's
[CREATE DATABASE documentation](https://www.postgresql.org/docs/17/sql-createdatabase.html).
W15 separately tests work that survives disappearance of all client sessions.
