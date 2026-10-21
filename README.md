# W4 replacement: local Linux worker-lock qualification candidate

This is a NEW preparation from the public W2.3 baseline, not recovery of the lost
W4 commit and not a claim of identical test bytes. It contains only the Python
worker dependency/test closure. It is not an application release or deployment.
No canonical W1/W3 commits or unpublished ancestors have been imported.

## Source identity

`W1-IDENTITY.json` retains all 90 original W1 hashes. Of those files, 87 remain
unchanged. The explicit overrides are the qualified ACL correction in `config.py`,
the exact W3 worker-runtime lock integration, and W3's general-worker PostgreSQL
test fixture adjustments. Five copied W3 source/test blobs have recorded SHA-256
hashes and exact commit provenance. The new lock module is included in that set.
The corrected config is byte-identical to W2.3. PostgreSQL claim/fencing and shared
calendar/outbox/scheduler business logic remain unchanged. No deployment notes,
operational reports, archives or credentials from another repository are included.

## Prepared Linux qualification

The only workflow triggers on push to `codex/w4-linux-flock-v2`, with read-only
contents permission, pinned Actions, checkout credentials persistence disabled,
hosted Ubuntu 24.04 and synthetic disposable PostgreSQL 17. There is no manual,
PR, broad branch or deployment trigger. A fresh approval of the final replacement
SHA is required before push or temporary Actions activation. Nothing is executed
remotely by local preparation.

Seventeen systemd cases are collected: all five W2.3 regressions plus twelve rebuilt
W4 cases. W2.3 assertions, real LoadCredential checks, business work, PostgreSQL
fencing, readiness and 45-second periodic heartbeat waits remain unchanged. Their
only fixture adjustment is `SupplementaryGroups=botnote`. Their readiness assertion
still uses an explicitly synthetic sync heartbeat rather than a real sync service.

A session fixture refuses existing lock/group/user resources, then provisions the
root:botnote directory `/run/botnote-worker-mode` at 0750 and a single regular
root:botnote `mode.lock` at 0440. Service users have a different primary group;
authorized admission uses actual systemd supplementary-group assignment. An outsider
has no such assignment. The same inode must survive all ordinary worker lifecycles.
Only test-admin fault cases replace or mutate metadata, with no competing holders,
and restore the original inode before cleanup. Workers never provision the lock.

New probes call unchanged public W3 runtime entrypoints and pause only the post-lock
domain boundary. This permits controlled concurrency/rejection observations without
owners mutating PostgreSQL during assertions. They retain real flock, UID/path
checks, SIGTERM handlers and the 15-second shutdown budget. They deliberately keep
a process alive after return/daemon timeout to prove that admission remains owned
until actual process death. Restart, SIGKILL and rejection before domain entry are
observed with distinct systemd processes. The sync case proves only lock independence.

Transient units use NoNewPrivileges, ProtectSystem=strict, KillMode=control-group,
and bounded memory/tasks/lifetime. A private read-only bind mount exposes the public
candidate and test venv under a disposable traversable path; it avoids widening
runner-home or lock permissions for distinct service UIDs. Only each synthetic state
directory is writable. Actual process UID/groups, NoNewPrivs and cgroup membership
are observed. All exact units are stopped before removing the disposable resources.
Tests require a hosted Actions/Linux/systemd/cgroup-v2 environment; unavailable
features must fail, not silently certify or skip.

## Evidence and limits

Local macOS validation: 55 configuration/ACL tests, 23 independent-process lock tests,
and 37 disposable PostgreSQL tests pass. Ruff, security lint, syntax and publication
checks are run separately. Collection is not execution: none of the seventeen Linux
systemd cases has passed for this replacement before its authorized remote run.

A future PASS would cover only the tested candidate and hosted-runner environment.
Old binaries which never acquire this lock remain outside exclusion. Trusted root
must preserve the inode after admission on a reliable local filesystem; an injected
replacement during acquisition does not prove safety against arbitrary later root
replacement. Production cutover, production units/credentials, full sync deployment,
zero-downtime migration, rollback/durability, power-loss recovery and database/OS
updates remain outside certification. No production service contract is changed.
