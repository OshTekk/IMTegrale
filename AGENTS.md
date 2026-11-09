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
