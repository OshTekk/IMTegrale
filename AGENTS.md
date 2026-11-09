# W10 disposable qualification workspace

- Preserve `w9_linux_window.py` byte-for-byte: SHA-256
  `db7256bb9815c549bdc5364311d99b4c07f7f296dbd53e027725c39216d18366`.
  Changes belong only in explicitly identified derived candidates.
- Never access, search or traverse `IMTegrale-Parcours-Private`. Never import
  private repository history, operational documents or credentials here.
- Remote execution requires user authorization with a bounded run budget.
  Current authorizations do not grant future runs, deployments or merges.
- Require Actions initially disabled, an audited branch-only workflow,
  read-only token, GitHub-hosted runner and only synthetic resources.
- Install a bounded finally/signal cleanup path before temporary activation.
  Restore `enabled=false` on completion, error, timeout or ordinary interrupt;
  independently GET and verify it and restoration of any other changed settings.
  API changes are not atomic; cleanup cannot guarantee recovery after host loss,
  credential loss or network failure. Unverified restoration is urgent and blocks
  further work. Never leave Actions enabled for investigation.
- Never print secrets, environment dumps, arbitrary argv, private file contents
  or unrelated journals. Bound/redact diagnostics to the synthetic namespace;
  observation errors or truncated required evidence must refuse certification.
- Quiescence and expected process outcomes are separate. Failed services can be
  terminated; zero PIDs alone cannot prove quiescence. Require all seven masks,
  no relevant jobs, empty/disappeared cgroups and no synthetic residuals.
- Never reopen admission automatically on failure, erase failed-state evidence,
  increase timeouts to pass, or modify unrelated services. Synthetic Linux
  qualification does not certify production, reboot, database fencing or rollout.

## W10.4 session authorization — 2026-11-09

The user explicitly granted TWO additional remote runs for this session only:
the exact a26b00c7f6f26f90a2dd319186d4c7e48bb6c090 candidate, plus one final
corrective candidate if necessary, regardless of the intermediate failure type.
A narrow correction may proceed after local tests and publication audit without
a new SHA approval, even if signal origin remained unresolved previously.
This supersedes the earlier signal-origin condition, not the restoration, scope,
redaction or integrity invariants. Count actual runs using the saved .git evidence;
never reset this budget in a later prompt. Restore and independently verify
Actions disabled after EACH run. Further runs require new user authorization.

Session status: a26b00c7 completed successfully in run 42695555895. One of the
two additional runs was used; the corrective slot was not needed. No rerun.
The unused slot does not authorize unrelated work or a future session.
The tested/public candidate stays a26b00c7; this budget note is local documentation.
