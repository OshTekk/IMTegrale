# W18 — invocation and mapping audit; synthetic scope only

Audit date: 2026-11-11. Runtime audited at public commit
`5e056ae5600a270f4932461b22ae428de21e57cc`; this follow-up changes dependency
acquisition and documentation only. No manager, PostgreSQL guard, query, test,
systemd observer, pinned input identity or historical default CLI is changed.

## W17 execution and restoration

Run [43047345093](https://github.com/OshTekk/IMTegrale/actions/runs/43047345093)
failed during dependency acquisition: APT could not resolve
`azure.archive.ubuntu.com`, then could not install `python3-psycopg` (exit 100).
The private-input reception, 47 regressions and Linux/systemd + DB experiment
were all SKIPPED. No W17 manager behavior or containment result was exercised.
This is an infrastructure failure, not evidence against the locally tested
admission guard, and it provides no composed Linux/DB certification.

Durable evidence: `.git/w17-run-20261111T151955Z/` contains `result.json`,
`jobs.json`, `run.log`, `restoration.json` and before/after policy records.
Restoration reports zero errors; the ephemeral secret inventory, workflow states,
token/security settings and unrelated branches were checked by the pilot.
A separate GET after pilot exit confirmed Actions `enabled=false`.
W17 has consumed ONE of THREE runs; never reset the ledger or rerun the same
push automatically. The approved commit reached the synthetic public branch.

The follow-up bounds each APT command to 45 seconds plus a 5-second kill grace,
sets zero acquisition retries and 10-second HTTP/HTTPS timeouts, and makes an
incomplete index update fatal before installation. Existing job/step/window
deadlines, official repositories, package set and source SHA pin are unchanged.
This makes acquisition fail promptly; it does not repair external DNS or prove
network availability. Do not increase timeouts or claim a passing qualification.

## Invocation inventory

| Path | Guard and actual evidence | Certification boundary |
| --- | --- | --- |
| Historical shell/default callers | Default implementation is preserved; no global interception or deployment changed. | Their use of the corrected candidate is unproved. No inherited DB fencing claim. |
| `w104_credstore.confined_manager` (line 9) | Fixed seven-unit confinement and current stop evidence via the existing mutation guard. | Does not implement durable DB admission; counts remain observations. |
| `w104_credstore.confined_main` (line 33) | Historical `safe_main` parser and dispatch with explicit confined factory. W12 run 42713353499 passed. | DB probes simulated; default shell unchanged. |
| `w16_postgres_admission.admitted_manager` (line 226) | Explicit subclass checks policy DB name and invokes admission under the existing guarded mutation path; guarded probe reads receipt-bound workload counts. | 25 local PG 17.11 cases passed with simulated systemd. No default/deployed routing implied. |
| `w16_postgres_admission.admitted_main` (line 256) | Historical parser uses an explicit admitted factory. Local rotate `--apply`/refusal test at `test_w16_postgres_admission.py:318` passes; policy loading is mocked there. | Does not prove deployed policy loading or all CLI commands against real DB on Linux. |
| Direct corrected manager operations | Local install, restore and normalization use the same admission (`test_w16_postgres_admission.py:355`); rotate and guarded recovery have independent real DB cases. | Manager API coverage differs from parser/CLI coverage; do not merge these claims. |
| `w17_linux_coupling.main` (line 23) | Intended to exercise W11 cuts, W12 command dispatch, then six DB cases in the closed real systemd window. | W17 failed before this path ran. Historical command subtests still use simulated DB probes; the six real DB cases use the explicit admitted manager. |

## Mapping evidence

| Mapping | Evidence and decision |
| --- | --- |
| Fixture service names → synthetic units | Explicit strict zip in `w11_linux_coupling.py:43` and `test_w16_postgres_admission.py:47`; remaps consumers and stop list, with fixed trigger. This is a fixture mapping, not a deployed source/unit inventory. |
| Synthetic units → closure | `w104_credstore.py:16` accepts exactly the five synthetic application services and the synthetic trigger; observation requires all seven masks, no relevant jobs, empty/disappeared cgroups and no residual synthetic processes. Zero PIDs alone is insufficient. |
| Fixture sources → consumers | Consistency is inherited from the pinned minimal fixture and remapped service policy. Tests mutate only temporary fixture files. Real source paths, aliases, environment files, credentials and consumer deployment are unproved. |
| Policy database → selected DB | Explicit equality in `admitted_manager`; each test creates `w16_lab_<serial>` in its own cluster (`test_w16_postgres_admission.py:54`). Mismatch refuses before closure. |
| DSN → cluster/database identity | Caller supplies control parameters; control connection must report `postgres` and writable primary. Receipt binds system identifier, DB OID/name and query hash (`w16_postgres_admission.py:162`). It is not a deployed DSN/role/HA inventory. |
| Workload queries → receipt | Queries come from the manager policy path, execute on the retained read-only observer after termination, and must produce exact integer zeros. Receipt guards mutation/recovery; queries and counters do not create admission fencing. |
| Admission → startup/session termination | Committed `ALLOW_CONNECTIONS false`; database-object startup locks sampled before activity; explicit termination and independent rechecks (`w16_postgres_admission.py:107`). |
| Privileged actors → authority | Owners, superusers, filesystem/single-user admins, arbitrary privileged background workers, reopen/reclose and HA promotion remain outside the contract. No claim that counters, advisory locks or receipt files fence them. |

## Scope decision and remaining gates

W16 evidence remains `.git/w16-local-pg-20261110T212044Z/result.json`:
25 real PostgreSQL 17.11 cases, zero failures/errors/skips, verified owned-cluster
cleanup and observed WAL redo; the prior 47 historical regressions remain a
separate local result. Those tests are unchanged by this follow-up.

W18 repository invocation/mapping audit is recorded. Overall readiness remains
BLOCKED: W17 composition has no passing run; deployed mappings and old callers
are UNKNOWN. No production, global rotation, privileged-authority, reboot or HA
certification is issued. No production access or private deployment inventory
was attempted to fill those gaps.

Next remote gate: review and explicitly approve the new exact follow-up commit
and destination before publication, as required by the recorded auto-review
boundary. If approved, use the persistent W17 ledger (two runs remaining), with
the last published SHA as BASE; validate the exact audit and restore/independently
GET `enabled=false` after the attempt. Only a passing composed run with complete
evidence can expand the synthetic certificate. Deployed caller/mapping proof
requires a separately authorized, accessible and scoped inventory; missing
evidence must remain an exclusion, never a guessed mapping.
