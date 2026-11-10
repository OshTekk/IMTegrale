# W15 — prepared work survives closed admission and zero sessions

Fifteen assertions passed on another fresh Unix-socket-only PostgreSQL 17.11
cluster. Exact evidence: `.git/w15-local-pg-20261110T072852Z/`, runner `proof.py`,
SHA-256 `e07d0681840d5ed87c8d819907537fc3fd7d569349911ba1ab9b2f3c3576bb9b`.
This derives the W14 synthetic experiment with max_prepared_transactions=4,
including the offline inspection process. No existing database was accessed.

A synthetic consumer inserts a marker, executes PREPARE TRANSACTION with one
fixed synthetic GID, then disconnects. After durable admission closure and
explicit termination of both remaining sessions, pg_stat_activity reports zero
target backends, but pg_prepared_xacts still contains the prepared transaction.
It survives real crash recovery and remains present at the final observation.
The ordinary uncommitted marker from the terminated session is not committed;
offline inspection finds zero visible marker rows. Admission is never reopened.

The PASS is a characterization of a remaining boundary, not safe-rotation
certification: `database_quiescent=false`, `prepared_transactions_observed=1`.
Neither zero sessions nor the historical five work counters establishes the
absence of prepared work. The observation must fail closed for any future
broader quiescence decision. No COMMIT PREPARED, ROLLBACK PREPARED, production
migration, manager modification, or automatic recovery policy was added.
Prepared-work evidence is retained in the journal; the owned disposable cluster
was stopped, independently checked stopped, and its generated files removed.

Two LOCAL attempts are retained. The first reached the durable prepared-work
counterexample but the offline inspector lacked the server's explicit
max_prepared_transactions=4 setting; the corrected second attempt passed.
No timeout was increased. ZERO remote runs; budget 0/3 is recorded durably in
`.git/w15-remote-run-ledger.json`. No remote repetition is needed for this local
PostgreSQL-only characterization; no Linux/systemd result is claimed.

This behavior is consistent with PostgreSQL's
[PREPARE TRANSACTION documentation](https://www.postgresql.org/docs/17/sql-prepare-transaction.html)
and [pg_prepared_xacts view](https://www.postgresql.org/docs/17/view-pg-prepared-xacts.html).
The historical manager and CLI remain unchanged. A next integration proof must
keep privileged admission authority, termination, prepared-work refusal,
controller recovery and publication ordering distinct, with no automatic reopen.
Deployed authority, mappings, old binaries and privileged callers remain unproved.
