# W5: local qualification candidate for the opt-in general-worker unit

This independent public-history workspace extends the Linux-qualified W4 candidate
`b26204fa69b2bf36e1401d8cde789f925321e7c5`. It is a qualification snapshot, not an
application release or an installed deployment. No canonical unpublished ancestry,
operational reports or credentials are imported.

## Source identity

All application source bytes, including the corrected credential ACL validator,
worker-mode flock, worker runtime, business logic and PostgreSQL fencing, remain
unchanged from W4. `W1-IDENTITY.json` retains the original W1 evidence and explicit
W2/W3 overrides. It additionally records the exact hashes and source commit of the
two opt-in W5 templates. The canonical source commit is provenance only and is not
reachable from this independent history.

## Prepared Linux qualification

The only workflow triggers on push to `codex/w5-systemd-profile`. It uses pinned
Actions, read-only contents permission, checkout without persisted credentials,
GitHub-hosted Ubuntu 24.04 and disposable synthetic PostgreSQL 17. No deployment,
manual, PR or broad branch trigger is present. Fresh exact-SHA authorization is
required before publication and temporary Actions activation.

The 78 existing configuration/security/process tests and 37 PostgreSQL tests remain
in the workflow. Two new systemd cases precede the preserved W4 smoke gate and all
17 W4/W2.3 systemd regressions. Their business assertions and heartbeat/shutdown
bounds are unchanged. All 19 systemd cases must execute and pass for this candidate.

The new cases exercise the unchanged real CLI with every Service sandbox directive
from `deploy/botnote-general-worker.service`, including PrivateTmp, ProtectHome,
MemoryDenyWriteExecute and the system-call filter. The actual service identity is
non-root `botnote:botnote`. PostgreSQL heartbeats must identify its current MainPID;
calendar/outbox work, cgroup limits and bounded SIGTERM completion are observed.

Synthetic installation paths replace WorkingDirectory, EnvironmentFile, the
credential source/directory and ExecStart's installed wrapper. `/usr/bin/env`
launches the mapped venv Python with `-B -m app.cli worker general`; no application
function is patched. A private read-only source bind, local-only network filter,
180-second runtime cap and RemainAfterExit for post-exit inspection are test-only
additions. No writable source/state exception is added to the service sandbox.
These differences do not certify installed executable paths or dependency ordering.

The session fixture runs only the exact W5 tmpfiles rule before any workers start.
A second case reruns that same rule twice while the general worker holds its lock,
requiring unchanged ownership, modes, device/inode and continued exclusion. Workers
never create, replace or remove the lock. Cleanup stops exact synthetic units before
removing their disposable users and paths; no host-wide service restart is used.

## Limits

Local collection is not Linux execution. W5 remains uncertified until the approved
remote run completes. W4's successful qualification does not certify this stronger
sandbox. This snapshot does not certify production unit installation, credentials,
service cutover, old nonparticipating binaries, full sync deployment, capacity,
release/rollback durability or power-loss recovery. No real data is used.
