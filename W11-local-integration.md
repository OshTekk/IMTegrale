# W11 — offline credential mutation/recovery coupling

Private runtime commit: `eb070dc3bff630c1c83f360f268032382ad2ac9b`.
The private implementation is NOT copied into this independent public history.
`w104_credstore.confined_manager` constructs that manager with the existing
W10.4 observation predicates, only for the exact fixed synthetic unit mapping.
Its caller must already own the closed window and current stop-request evidence.
It does not mask/unmask, start services, change the historical CLI or establish
production mappings. An absolute deadline bounds observations and is never renewed.

The manager's explicit optional guard is checked under its existing exclusive
lock at entry, publication/destruction/mode-change boundaries, recovery and final
confinement. Recovery cannot write after proof loss; its journal remains for a
later proved window. A committed target remains committed. An ordinary mutation
exception attempts existing recovery under fresh proof and preserves its primary
exception. Final proof failure cannot yield a successful operation result. No
path reopens admission. Legacy callers without a guard retain historical behavior.

## Executed locally

Set `W11_CREDSTORE_SOURCE` to the explicitly selected isolated private checkout
before running `python -B -m unittest test_w10_diagnostics test_w104_quiescence
 test_w11_credstore -q` (one command line). Tests pin both private source hashes
and resolved import paths; without that explicit source the 17 integration cases
are visibly skipped, never interpreted as certification. Do not add private
source checkout or these environment requirements to the public workflow.

- 37 tests pass: 20 retained W10.4 and 17 new coupling cases.
- Real temporary credential files, atomic publication, journals and manager lock;
  simulated systemd observations and historical fake DB/descriptor probes.
- Install, rotate, restore, normalization, pre-publication interruption, partial
  publication, committed target, rollback, retryable recovery failure, observation
  loss, missing masks, unexpected signal and final-confinement failure are covered.
- Private credstore baseline/candidate: 253 tests, no failures/errors, 40 skips.
- Controller baseline/candidate: 1113 tests; same 9 failures, 27 errors, 2 skips;
  exact equality of all 36 failure/error identities. The suite remains red.
- Ruff E4/E7/E9/F passes; security S checks pass on new coupling code. Three
  existing manager S603/S608 diagnostics remain identical to baseline. Bandit is
  unavailable; it is not reported as executed.
- Secret/content-boundary checks and diff/syntax checks pass. Original W9 and
  existing qualified observation/runtime/workflow files remain byte-identical.

## Unqualified boundary

No coupled Linux/systemd or real PostgreSQL execution occurred. The previous
W10.4 Linux proof covers the fixed synthetic window, not this new coupling.
Persistent production admission, direct/privileged actors, actual deployed
credential/unit mapping, DB fencing, interruption by process death/reboot, and
production deployment are NOT certified. Polling proofs cannot exclude a trusted
privileged actor removing masks between observations. The next step is a bounded
Linux experiment of this exact local coupling using only synthetic resources,
under the standing synthetic qualification authority in AGENTS.md; production remains unauthorized.

## Prepared Linux coupling

The workflow now receives ONLY three pinned test-source files through a temporary
repository secret; neither their contents nor private Git history enters this
public tree. The secret must not preexist, is removed after every run, and no
private input is uploaded as an artifact. Fixed-name bounded decoding verifies
all source hashes before imports. Private tracebacks and test streams are withheld;
public evidence contains counts, fixed cut labels, observation status and hashes.
The input is removed on the runner with an always step, and the runner is disposable.

The Linux adapter retains the qualified W10.4 fixture and strict observations,
then kills independent real-manager owners at PREPARED, partial publication and
COMMITTED boundaries and during recovery. All five final generations, journals,
start/restart refusal, fresh proof, final confinement and the historical window
cuts are required. The database probe remains explicitly simulated; this stage
cannot certify PostgreSQL fencing or production source mapping. Two adapter
regressions exercise actual process death locally with simulated observations.
