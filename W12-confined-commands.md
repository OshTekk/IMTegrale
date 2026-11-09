# W12 — historical commands, explicitly confined synthetic embedding

Private candidate: `1286417fe61388a7a07c84d15c94b0dacfd61c6a`.
The exact private manager SHA-256 is
`2165c545706b7d6c04b1fb3e5884212c2d1b683d702c17567c102c154c275985`.
It differs from W11 only by the optional constructor argument to `safe_main`;
its journal format, locks, recovery logic, checks, parser and dispatch are retained.
The corresponding installation digest is updated in the PRIVATE isolated branch.
No private source or private Git history is published.

`confined_main` embeds the historical `safe_main` with the existing guarded
constructor. It never copies command dispatch. The shell CLI and old callers
remain unchanged; this is NOT default confinement or production activation.
Guard diagnostics go to bounded redacted stderr so stdout remains the original
single JSON report. All reports, exit codes, `--apply` and `--recover-to` remain.

Local coverage: 47 cases, including eight command regressions and the prior 39.
The runner receives only three pinned ephemeral input files through a temporary
secret, with no private tracebacks, content logs or artifacts. Inputs and the
secret are removed, Actions is restored disabled, and unrelated settings/states
are checked after every run. Standing authority permits up to three W12 runs.

The prepared Linux test exercises real policy-file security and the exact parser,
install/rotate/restore, normalization/check, refusal without apply, killed partial
publication and recovery through historical dispatch. The prior five real-manager
process-death cuts and the original window experiment also remain required.
Database probes remain simulated: no PostgreSQL fencing, production source/unit
mapping, default historical CLI enforcement, reboot or deployment is certified.
Any claimed result must be supported by the exact run and local `.git` proof.

Validated before publication: 47 local regressions pass; credstore 253 tests,
0 failures/errors, 40 skips; controller 1113 tests, baseline-identical 9 failures,
27 errors, 2 skips (all 36 failure/error identities compared exactly). Ruff and
syntax checks pass for new code; inherited security-lint diagnostics are unchanged.

Linux result: public `d02a6caf565cf9f446b27c4467ea3323791f39a7`,
https://github.com/OshTekk/IMTegrale/actions/runs/42713353499, success.
All 47 regressions passed without skips; real policy parsing and historical
command dispatch, killed publication/recovery, all five W11 owner-death cuts,
the retained W10.4 experiment and final confinement passed. Actions was restored
disabled, source secret removed, historical states and unrelated settings verified.
One W12 run used; no corrective run. This certifies only the explicit confined
embedding and fixed synthetic Linux scope, never the default shell invocation.
