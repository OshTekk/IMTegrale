# W13 — real database observation boundary; fencing NOT certified

Five tests executed successfully on a fresh local PostgreSQL 17.11 cluster;
no test was skipped. PostgreSQL listened only on its newly created private Unix
socket, never TCP. The server was stopped, independently checked stopped, and
only its own newly created synthetic files were removed. No migration, existing
cluster, real credentials, production data or service was accessed.

Exact runtime: private W12 `1286417fe61388a7a07c84d15c94b0dacfd61c6a`,
manager SHA-256 `2165c545706b7d6c04b1fb3e5884212c2d1b683d702c17567c102c154c275985`.
The manager's original five SQL queries executed against PostgreSQL through a
narrow test probe. The schema is deliberately minimal and synthetic, not a claim
about deployed migrations. Systemd and descriptor observations are SIMULATED in
these local tests; W11/W12 real systemd evidence stays in their separate runs.

Observed:
- Empty real counters permit a coherent guarded publication.
- An already connected consumer refuses source publication.
- Each existing running/lease/operation/notification counter refuses publication.
- A SQL observation error refuses source publication.
- Counterexample: after all five real snapshots return zero, a new external
  consumer connects before publication. The unmodified manager returns success,
  target sources are published, and the real activity counter is then ONE.

The passing characterization proves the missing fence, NOT safe global rotation.
The late connection is outside the fixed managed-systemd admission contract;
this does not invalidate the scoped W10.4/W11/W12 Linux results. It forbids
claiming database-wide fencing from those results. There is NO runtime change,
new coordinator, table, lock service or attempted production fix in W13.

Before any broader lifecycle claim, a durable database-admission/termination
boundary must be proved under an existing authorized administrative identity,
including in-flight connection startup, controller death, recovery connectivity,
and continued closure. Do not add speculative role/database mutations or an
irreversible automatic reopening to hide this gap. Deployed unit/source mappings,
old nonparticipating callers, direct/privileged actors and the real administrative
authority remain separate prerequisites. The historical default shell CLI remains
unchanged and is not automatically confined by an opt-in embedding.

No remote W13 run was needed: zero of its three synthetic run slots used.
Evidence, fresh-cluster lifecycle logs and test output are retained locally under
`.git/w13-local-pg-20261109T223941Z/`; never publish private test logs or inputs.
