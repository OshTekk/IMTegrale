# W2 disposable Linux qualification snapshot

This independent-root branch contains only the W1 Python worker dependency closure,
selected unchanged W1 regressions, schema definitions for a synthetic PostgreSQL 17
test database, and narrow transient-systemd tests. It is not a complete application
release. Other CLI commands, frontend and deployment assets are intentionally absent.

Among the 90 original files recorded in `W1-IDENTITY.json`, 89 remain byte-identical
to W1. Only `backend/app/config.py` contains the explicitly recorded W2.1 correction
for systemd's root-and-service-user credential ACL. This is a corrected W2.2 candidate,
not certification of the original W1 configuration. The separate ACL regressions
exercise rejection of unsafe access.
No source Git history was imported. The workflow runs only on the dedicated W2 branch
using hosted Ubuntu, a disposable local PostgreSQL database and synthetic credentials.
Its schema setup touches only that runner's disposable `_test` database.

The Linux probes inject a blocked calendar handler or one fatal loop exclusively in
the test launcher. They call the original CLI and runtime and retain the original
15-second shutdown budget. The existing W1 PostgreSQL regressions exercise blocked
scheduler fairness and stale domain-write fencing. The sync heartbeat used by a
readiness assertion is explicitly synthetic; the isolated sync service is not run.

General-worker mode and the three individual calendar/outbox/scheduler services must
be mutually exclusive in a future rollout. This snapshot documents that requirement;
it does not enforce exclusivity or change any deployment contract.

Passing these checks certifies only the observed hosted-runner behavior. Production
units, credential provisioning, isolated-sync deployment, deployment lifecycle,
crash durability, upgrades and database rollback remain outside this qualification.
