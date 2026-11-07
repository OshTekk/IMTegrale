# W10.3 — one synthetic closure diagnostic, local preparation

**Not published or executed. New exact-commit authorization required.**
`WINDOW_CERTIFIED=false`. W10.2's authorization is consumed.

The previous run was attempt 1 of [42303017229](https://github.com/OshTekk/IMTegrale/actions/runs/42303017229),
commit `187d3cc39606613ad9086f33baeb3f2e70dd9879`, workflow
`.github/workflows/w10-maintenance-window.yml`. It passed preflight, failed
W9's first closure wait, then failed final containment verification. The exact
blocking state was not recorded. See [W10.3 review](W10.3-review.md).

## Candidate identity and scope

`w9_linux_window.py` is the unchanged original candidate:
`db7256bb9815c549bdc5364311d99b4c07f7f296dbd53e027725c39216d18366`.
The workflow now invokes **a different diagnostic candidate**, once:
`w9_linux_window_diagnostic.py`, SHA-256
`d7d1eb126ee2e0d892223a2a6c587c125e71f3f2dfe7cf973524651d5d87cc3c`.
Both hashes are checked before namespace ownership. The diagnostic module is
`w10_observe.py`; the exact publication commit binds all files. The reviewed
original-to-diagnostic delta is [w9-diagnostic.patch](w9-diagnostic.patch).

Only the first closure scenario executes: omission rejection, concurrent start,
legacy residual rejection, targeted SIGKILL, unchanged complete closure wait,
and unchanged in-flight assertion. A successful wait returns before direct
launch/mutation/recovery scenarios. It cannot certify those scenarios or a
maintenance window. No automatic retry or second experiment.

## Fixed synthetic boundary

Five consumers: `w9-proof-{general,sync,legacy,web,scheduler}.service`.
Also `w9-proof-legacy.timer` and `w9-proof-trigger.service`.
Files are confined to `/var/lib/w9-maintenance-proof`, seven vendor units under
`/usr/local/lib/systemd/system`, and seven persistent `/etc/systemd/system`
masks. Credentials are synthetic `old`/`new`; no application data, DB or network.
No accounts, permission, KillMode, consumer, masking or reopening changes.
No `--cleanup` invocation, unmask, mask deletion, production service operation,
package install, artifact upload, release, deployment, cache or private history.
`daemon-reload` retains its original manager-wide reload effect.

Preflight remains read-only and rejects collisions before acquiring ownership.
Its existing inventories and the wrapper's existing no-synthetic-process
assertion are retained, including their original host process scan. They are
not added diagnostics and their contents/argv are never emitted. New diagnostics
never enumerate host processes or host journals: only exact unit properties,
exact unit journals, exact synthetic cgroup trees and their associated PID
metadata. This retains the existing termination assertion without weakening it.

## Observation and failure handling

At first useful observation, on sampled change (at most once per second), and
on closure/confinement exit, emit JSON with phase, remaining monotonic budget,
independent unit predicates, selected properties, exact remaining synthetic jobs,
masks, cgroup populated/PIDs and PID UID/PPID/state/start ticks. Actual W9 and wrapper unit predicates are also emitted from their existing
decision reads; W9 short-circuit rejection is marked with its unit and reason. Snapshots
are independent reads, not atomic witnesses and not inputs to the decision.
Missing/error/truncated reads are explicit unknowns. Removed cgroups are labelled
as removed, never counted as a newly observed empty cgroup. The original decision
still accepts disappearance only with its original unit/no-job checks.

Observation uses at most 0.6 s per sample and at most 0.15 s per command, within
the current phase deadline. No new sleep, retry or deadline extension. This adds
measurement overhead and may perturb a race; local tests cannot validate Linux
behavior. Expiration emits the last observation explicitly stale and starts no
new command. A lost hard-killed wrapper cannot promise an expiry record.

Selected properties: Id, LoadState, ActiveState, SubState, MainPID, ControlPID,
Job, ControlGroup, Result, ExecMainCode, ExecMainStatus, TimeoutStopUSec, KillMode,
SendSIGKILL. Journal: at most three messages per blocking exact unit, once per
observer. Free journal/stderr text is reduced to a fixed vocabulary (24 tokens,
first 2048 input characters); paths, argv, credentials and arbitrary message
values are withheld. Command rc, timeout/error type and redaction/truncation
status remain explicit. No environment, secret, source-file content, descriptor,
full command line or unrelated path is printed. PID stat comm is omitted; start
ticks are reread to identify observed PID reuse. No signals are sent by diagnostics.

Cgroup observation is limited to exact `/system.slice/<unit>` trees, at most 16
folders and 32 PIDs per tree, 64 process records per snapshot. Unexpected paths
are withheld and marked out of scope. Regular snapshots have a 128 KiB payload
budget per observer, with a total 192 KiB including reserved exit/expiry records;
JSON framing adds small fixed overhead. Output exhaustion is explicit unknown,
never success. The driver and wrapper have separate observers.

Wrapper output separates initial driver result, termination failure, containment
attempt, command results (including tolerated kill rc), fsync, mask observations,
residual predicates and verified/non-verified containment. Cleanup exceptions
are caught and reported; an active initial driver exception is preserved. A
nonzero driver result remains recorded before cleanup, and the job fails even
when containment succeeds. Containment failure also always fails the job.
No runner disposability inference substitutes for verification.

## Existing execution bounds and future authorization

Unchanged: preflight 45 s, W9 alarm 240 s, driver wait 255 s, driver termination
5 s, first closure 60 s, containment 75 s, command ceiling 10 s; step 7 min,
checkout 2 min, job 10 min. The workflow keeps `contents: read`, standard
GitHub-hosted `ubuntu-24.04`, fixed dedicated branch and repository guard,
ordinary non-forced/non-deleted push only, `run_attempt == 1`, no matrix,
checkout pinned to `3d3c42e5aac5ba805825da76410c181273ba90b1`, triggering SHA,
no persisted credentials/tags/LFS/submodules. No workflow_dispatch or rerun route.

Before any publication, obtain explicit approval of the new exact commit for
ordinary push only to `codex/w10-maintenance-window` and ONE diagnostic run,
including temporary Actions activation and verified restoration to `enabled=false`.
Recheck remote head/settings/read-only run inventory first; preserve and restore
all affected prior settings and workflow states. No prior run rerun. No implicit
activation or retry. A diagnostic PASS is not maintenance-window qualification.

Local validation is documented in the review. Tests run only formatting,
redaction, error handling and static guards; no Linux or systemd run occurred.
