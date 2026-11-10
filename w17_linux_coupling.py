"""W17: real closed systemd window + W16 candidate + owned PostgreSQL 17.11."""
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
import w11_linux_coupling as historical
import w16_pg_lab

CASES=(
    'test_late_w13_connection_is_refused_before_publication',
    'test_existing_transaction_and_query_are_terminated',
    'test_postauth_startup_invisible_to_activity_is_terminated',
    'test_prepared_work_refuses_publication_and_survives_restart',
    'test_partial_publication_recovers_after_controller_sigkill_and_db_crash',
    'test_lost_admission_midpublication_preserves_journal_and_refuses_recovery',
)


def main():
    historical.require(sys.platform=='linux' and os.geteuid()==0)
    window=historical.window
    original=window.reopen_allowed
    exercised=False
    def at_closed_window(receipt,current,complete,groups):
        nonlocal exercised
        if not exercised:
            exercised=True
            historical.exercise(groups)
            historical.exercise_commands(groups)
            end=time.monotonic()+100
            journal=Path('/tmp')/('w17-pg-evidence-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
            w16_pg_lab.exercise(journal,window=(end,groups,window.OBSERVER),selected=CASES)
            historical.require(historical.obs.namespace_quiescent(groups,end,window.OBSERVER))
            print('W17_REAL_SYSTEMD_POSTGRES_CHAIN_PASS; SIX_CASES; DB_LEFT_CLOSED; SYNTHETIC_ONLY',flush=True)
        return original(receipt,current,complete,groups)
    window.reopen_allowed=at_closed_window
    signal.alarm(240)  # Preserve the existing whole-window limit.
    window.run(Path(window.__file__).resolve())
    historical.require(exercised)
    print('W17_LINUX_COUPLING_QUALIFIED; NO_PRODUCTION_OR_PRIVILEGED_ACTOR_CERTIFICATION',flush=True)


if __name__=='__main__':
    try: main()
    except BaseException as exc:
        print('W17_LINUX_FAILED; type='+type(exc).__name__+'; private details withheld',flush=True)
        raise SystemExit(1) from None
