"""Real W10.4 observations + pinned private manager; fixed disposable lab only."""
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import signal
import sys
import time

# -I deliberately omits script paths; use only this audited checkout directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import w10_observe as obs
import w104_credstore as bridge
from w11_ephemeral_input import verify
import w9_linux_window_diagnostic as window


def require(value):
    if not value:
        raise RuntimeError('W11 fixed synthetic assertion failed')


def exercise(groups):
    source = Path(os.environ['W11_CREDSTORE_SOURCE'])
    verify(source)
    sys.path.insert(0, str(source / 'credstore'))
    manager = importlib.import_module('manager')
    Fixture = importlib.import_module('tests.helpers').Fixture
    obs.STOP_STARTED_US = window.OBSERVER_MODULE.STOP_STARTED_US
    end = time.monotonic() + 100  # Never refresh this bound within a test cut.
    require(obs.namespace_quiescent(groups, end, window.OBSERVER))
    require(obs.general_stop_evidence(end)['expected_pre_sigterm'])
    cases = []
    for cut in ('before', 'partial', 'committed', 'rollback', 'recovery'):
        fixture = Fixture()
        try:
            old = fixture.write_sources()
            target = fixture.write_staging()
            services = fixture.policy['services']
            mapping = dict(zip(services['application_services'], obs.UNITS[:5], strict=True))
            services['application_services'] = list(obs.UNITS[:5])
            services['consumers'] = {key: [mapping[name] for name in names]
                                    for key, names in services['consumers'].items()}
            services['stop_for_mutation'] = [mapping[name] for name in services['stop_for_mutation']]
            services['operations_check'] = 'w9-proof-trigger.service'
            runtime = bridge.confined_manager(
                manager.CredstoreManager, fixture.policy, groups=groups,
                deadline=end, observer=window.OBSERVER,
                expected_uid=os.getuid(), expected_gid=os.getgid(),
                probe=fixture.probe, xattr_reader=lambda fd: ())
            # Kill an independent owner exactly at a durable journal boundary.
            # No private traceback crosses the public logging boundary.
            pid = os.fork()
            if pid == 0:
                try:
                    write = runtime._write_journal
                    def killed(journal):
                        write(journal)
                        at = journal['step']
                        if ((cut == 'before' and at == 'PREPARED')
                                or (cut in ('partial', 'rollback', 'recovery') and at == 'REPLACING')
                                or (cut == 'committed' and at == 'COMMITTED')):
                            os.kill(os.getpid(), signal.SIGKILL)
                    runtime._write_journal = killed
                    runtime.rotate()
                except BaseException:
                    os._exit(2)
                os._exit(3)
            owner, status = os.waitpid(pid, 0)
            require(owner == pid and os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL)
            require(obs.namespace_quiescent(groups, end, window.OBSERVER))
            window.deny_starts()
            # Recovery cut kills a second owner during actual recovery publication.
            if cut == 'recovery':
                child = os.fork()
                if child == 0:
                    try:
                        atomic = runtime.io.atomic_write
                        def killed_recovery(parent, name, data):
                            result = atomic(parent, name, data)
                            if parent == str(fixture.sources):
                                os.kill(os.getpid(), signal.SIGKILL)
                            return result
                        runtime.io.atomic_write = killed_recovery
                        runtime.recover('previous')
                    except BaseException:
                        os._exit(2)
                    os._exit(3)
                _, status = os.waitpid(child, 0)
                require(os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL)
            runtime.recover('target' if cut == 'partial' else 'previous')
            require(fixture.source_values() == (target if cut in ('partial', 'committed') else old))
            require(runtime.final_confinement_verified is True)
            require(not (fixture.journal / 'active.json').exists())
            require(obs.namespace_quiescent(groups, end, window.OBSERVER))
            cases.append(cut)
            print('W11_REAL_MANAGER_CUT_PASS=' + cut, flush=True)
        finally:
            fixture.close()
    require(obs.namespace_quiescent(groups, end, window.OBSERVER))
    print(json.dumps({'w11_real_linux_manager_cuts': cases,
                      'systemd_observations': 'real', 'private_source_identity': 'pinned',
                      'database_probe': 'synthetic_fake_NOT_DB_FENCING_CERTIFIED',
                      'final_confinement': True}), flush=True)



def exercise_commands(groups):
    manager = sys.modules['manager']
    Fixture = sys.modules['tests.helpers'].Fixture
    fixture = Fixture()
    end = time.monotonic() + 60
    try:
        services = fixture.policy['services']
        mapping = dict(zip(services['application_services'], obs.UNITS[:5], strict=True))
        services['application_services'] = list(obs.UNITS[:5])
        services['consumers'] = {key: [mapping[name] for name in names]
                                for key, names in services['consumers'].items()}
        services['stop_for_mutation'] = [mapping[name] for name in services['stop_for_mutation']]
        services['operations_check'] = 'w9-proof-trigger.service'
        policy = fixture.root / 'policy.json'
        policy.write_text(json.dumps(fixture.policy))
        policy.chmod(0o400)
        fixture.write_staging()

        def command(operation, *, apply=True):
            output = io.StringIO()
            args = [operation, '--policy', str(policy)]
            if apply:
                args.append('--apply')
            with contextlib.redirect_stdout(output):
                code = bridge.confined_main(manager, args, groups=groups,
                                            deadline=end, observer=window.OBSERVER,
                                            probe=fixture.probe, xattr_reader=lambda fd: ())
            report = json.loads(output.getvalue())
            require(set(report) == manager.OUTPUT_FIELDS)
            require(report['operation'] == operation)
            return code, report

        for operation in ('verify-input', 'install', 'rotate', 'restore'):
            if operation in ('rotate', 'restore'):
                fixture.write_staging(prefix=operation.encode())
            code, report = command(operation)
            require(code == 0 and report['status'] == 'COMPLIANT')
            print('W12_HISTORICAL_COMMAND_PASS=' + operation, flush=True)
        coherent = fixture.source_values()
        for path in fixture.paths:
            path.chmod(manager.CURRENT_LEGACY_MODE)
        require(command('normalize-mode')[0] == 0)
        require(fixture.source_modes() == [manager.FINAL_MODE] * 3)
        require(command('check')[0] == 0)
        require(command('rotate', apply=False)[0] == manager.EXIT_APPLY_REQUIRED)
        require(fixture.source_values() == coherent)
        fixture.write_staging(prefix=b'next')
        child = os.fork()
        if child == 0:
            try:
                original = manager.CredstoreManager._write_journal
                def killed(instance, journal):
                    original(instance, journal)
                    if journal['step'] == 'REPLACING':
                        os.kill(os.getpid(), signal.SIGKILL)
                manager.CredstoreManager._write_journal = killed
                command('rotate')
            except BaseException:
                os._exit(2)
            os._exit(3)
        _, status = os.waitpid(child, 0)
        require(os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL)
        require(command('recover')[0] == 0)
        require(fixture.source_values() == coherent)
        require(not (fixture.journal / 'active.json').exists())
        require(obs.namespace_quiescent(groups, end, window.OBSERVER))
        print('W12_COMMANDS_LINUX_QUALIFIED; REAL_POLICY_PARSE_AND_DISPATCH; DB_PROBE_SIMULATED; DEFAULT_CLI_UNCHANGED', flush=True)
    finally:
        fixture.close()


def main():
    require(sys.platform == 'linux' and os.geteuid() == 0)
    original = window.reopen_allowed
    exercised = False
    def at_closed_window(receipt, current, complete, groups):
        nonlocal exercised
        if not exercised:
            exercised = True
            exercise(groups)
            exercise_commands(groups)
        return original(receipt, current, complete, groups)
    window.reopen_allowed = at_closed_window
    signal.alarm(240)
    window.run(Path(window.__file__).resolve())
    require(exercised)
    print('W11_LINUX_COUPLING_QUALIFIED; FIXED_SYNTHETIC_SCOPE_ONLY; NO_DB_OR_PRODUCTION_CERTIFICATION', flush=True)


if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:
        print('W11_LINUX_FAILED; type=' + type(exc).__name__ + '; private details withheld', flush=True)
        raise SystemExit(1) from None
