#!/usr/bin/env python3
"""W9 one-shot destructive-to-synthetic-resources experiment. NOT a runtime tool.

Run only on an explicitly authorized disposable Linux with systemd PID 1,
cgroup v2 and root. No network, package installation, reboot or real units.
No automatic reopening or cleanup on failure. See w9-maintenance-window.md.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

ROOT = Path('/var/lib/w9-maintenance-proof')
VENDOR = Path('/usr/local/lib/systemd/system')
ETC = Path('/etc/systemd/system')
ROLES = ('general', 'sync', 'legacy', 'web', 'scheduler')
SERVICES = tuple('w9-proof-' + r + '.service' for r in ROLES)
UNITS = (*SERVICES, 'w9-proof-legacy.timer', 'w9-proof-trigger.service')
CGROOT = Path('/sys/fs/cgroup')


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def command(*args, end=None, ok=True):
    remaining = 10 if end is None else end - time.monotonic()
    require(remaining > 0, 'deadline expired; leave masks installed')
    result = subprocess.run(args, text=True, capture_output=True,
                            timeout=min(10, remaining))
    require(not ok or result.returncode == 0,
            'synthetic command failed; rc=' + str(result.returncode))
    return result


def ctl(*args, **kwargs):
    return command('systemctl', *args, **kwargs)


def durable(path, value):
    with path.open('w') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def state(unit, end=None):
    result = ctl('show', unit, '--property=Id,ActiveState,SubState,MainPID,ControlPID,Job,ControlGroup', end=end)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    require(values.get('Id') == unit, 'ambiguous identity')
    return values


def masked():
    # /etc, never /run. Ownership is the experiment's fixed exclusive namespace.
    return all((ETC / u).is_symlink() and os.readlink(ETC / u) == '/dev/null' for u in UNITS)


def require_coverage(configured):
    require(set(configured) == set(SERVICES), 'synthetic consumer set incomplete')


def process_free(groups, end):
    observe('closure', groups, end)
    for unit in SERVICES:
        s = state(unit, end)
        OBSERVER.unit_decision('closure', unit, s)
        if not (s.get('ActiveState') == 'inactive' and s.get('SubState') == 'dead'
                and s.get('MainPID') == '0' and s.get('ControlPID') == '0'
                and s.get('Job') in ('', '0')):
            OBSERVER.decision('closure', unit, 'unit_predicate_false')
            return False
        for group in {groups.get(unit, ''), s.get('ControlGroup', '')} - {''}:
            require(group.startswith('/') and '..' not in group.split('/'), 'invalid cgroup')
            events = CGROOT / group.lstrip('/') / 'cgroup.events'
            try:
                if 'populated 0' not in events.read_text().splitlines():
                    OBSERVER.decision('closure', unit, 'cgroup_populated_not_zero')
                    return False
            except FileNotFoundError:
                # Removed cgroup plus inactive/no-job under durable masks.
                pass
    return True


def close(groups):
    end = time.monotonic() + 60
    ctl('mask', *UNITS, end=end)
    for unit in UNITS:
        fd = os.open(ETC, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(fd)
        os.close(fd)
    ctl('daemon-reload', end=end)
    require(masked(), 'admission not closed BEFORE stop')
    ctl('stop', '--no-block', *UNITS, end=end)
    return end


def wait_free(groups, end):
    try:
        while not process_free(groups, end):
            require(time.monotonic() < end, 'residual/job; closed window required')
            time.sleep(0.05)
        require(masked(), 'lost exclusion')
    finally:
        observe('closure_exit', groups, end, force=True)


def deny_starts():
    for unit in UNITS:
        for verb in ('start', 'restart'):
            require(ctl(verb, unit, ok=False).returncode != 0, 'masked admission: ' + verb + ' ' + unit)


def reopen_allowed(receipt, current, complete, groups):
    # Test assertion only. No exported maintenance protocol or durable receipt.
    return (receipt == current and complete and masked()
            and process_free(groups, time.monotonic() + 10)
            and not (ROOT / 'uncertain').exists()
            and len({(ROOT / ('source-' + str(i))).read_text() for i in range(3)}) == 1)


def consumer(role):
    # No flock: sync and legacy deliberately do not participate in W3.
    credential_dir = Path(os.environ.get('CREDENTIALS_DIRECTORY', str(ROOT)))
    memory_copy = tuple((credential_dir / ('source-' + str(i))).read_text() for i in range(3))
    require(len(memory_copy) == 3, 'synthetic credentials unavailable')
    if role == 'legacy':
        child = os.fork()
        if child == 0:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            durable(ROOT / 'residual', str(os.getpid()))
            while True:
                time.sleep(0.1)
    durable(ROOT / (role + '.entered'), str(os.getpid()))
    while True:
        time.sleep(0.1)


def pre(role):
    durable(ROOT / (role + '.pre'), 'entered')
    while not (ROOT / (role + '.release')).exists():
        time.sleep(0.02)


def mutator(cut):
    require(masked(), 'mutation without masks')
    durable(ROOT / 'uncertain', cut)
    if cut != 'before':
        for i in range(3 if cut in ('committed', 'rollback') else 1):
            temporary = ROOT / 'source.tmp'
            durable(temporary, 'new')
            os.replace(temporary, ROOT / ('source-' + str(i)))
        if cut == 'rollback':
            durable(ROOT / 'source-0', 'old')
        durable(ROOT / 'publication-cut', cut)
    os.kill(os.getpid(), signal.SIGKILL)


def setup(script):
    require(not ROOT.exists(), 'experiment already exists; inspect, do not overwrite')
    for unit in UNITS:
        for folder in (ETC, VENDOR, Path('/run/systemd/system'), Path('/usr/lib/systemd/system')):
            require(not os.path.lexists(folder / unit), 'namespace collision')
    ROOT.mkdir(mode=0o700)
    durable(ROOT / 'experiment.py', script.read_text())
    VENDOR.mkdir(parents=True, exist_ok=True)
    for role, unit in zip(ROLES, SERVICES):
        kill = 'process' if role == 'legacy' else 'control-group'
        durable(VENDOR / unit, '[Unit]\nDescription=W9 synthetic only\n[Service]\nType=exec\n'
                + ''.join(f'LoadCredential=source-{i}:{ROOT}/source-{i}\n' for i in range(3))
                + f'ExecStartPre=/usr/bin/python3 {ROOT}/experiment.py --pre {role}\n'
                + f'ExecStart=/usr/bin/python3 {ROOT}/experiment.py --consumer {role}\n'
                + f'KillMode={kill}\nTimeoutStopSec=2\nRestart=no\n')
        if role != 'general':
            durable(ROOT / (role + '.release'), 'go')
    durable(VENDOR / UNITS[-2], '[Timer]\nOnActiveSec=1h\nUnit=w9-proof-legacy.service\n')
    durable(VENDOR / UNITS[-1], '[Unit]\nRequires=w9-proof-general.service\nAfter=w9-proof-general.service\n[Service]\nType=oneshot\nExecStart=/bin/true\n')
    for i in range(3):
        durable(ROOT / ('source-' + str(i)), 'old')
    ctl('daemon-reload')


OBSERVER = None


def observe(phase, groups, end, force=False):
    try:
        OBSERVER.snapshot(phase, groups, end, force=force)
    except Exception as exc:
        print('W10_DIAG observation_error=' + type(exc).__name__ + '; UNKNOWN', flush=True)


def run(script):
    global OBSERVER
    spec = importlib.util.spec_from_file_location('w10_observe', script.with_name('w10_observe.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    OBSERVER = module.Observer()
    setup(script)
    durable(ROOT / 'general.release', 'go')
    ctl('start', SERVICES[0])
    until = time.monotonic() + 10
    while not (ROOT / 'general.entered').exists():
        require(time.monotonic() < until, 'general not active for omission test')
        time.sleep(0.02)
    require(state(SERVICES[0])['ActiveState'] == 'active', 'general not active')
    configured = set(SERVICES[1:])
    try:
        require_coverage(configured)
    except RuntimeError:
        require(all((ROOT / ('source-' + str(i))).read_text() == 'old' for i in range(3)),
                'omission rejection modified a source')
    else:
        raise RuntimeError('omitted active general admitted')
    print('active general omitted from configuration: admission refused', flush=True)
    require_coverage(SERVICES)  # Independent fixed laboratory set, not a production inventory.
    ctl('stop', SERVICES[0])
    for name in ('general.entered', 'general.pre', 'general.release'):
        (ROOT / name).unlink()
    ctl('start', '--no-block', *SERVICES)
    end = time.monotonic() + 10
    while not all((ROOT / (r + ('.pre' if r == 'general' else '.entered'))).exists() for r in ROLES) or not (ROOT / 'residual').exists():
        require(time.monotonic() < end, 'consumers did not enter controlled boundaries')
        time.sleep(0.02)
    require(not (ROOT / 'general.entered').exists(), 'in-flight test not controlled')
    groups = {u: state(u)['ControlGroup'] for u in SERVICES}
    stop = threading.Event()
    raced = threading.Event()
    attempts = []
    def racer():
        for _ in range(30):
            if stop.is_set():
                break
            began = time.monotonic()
            result = ctl('start', '--no-block', SERVICES[1], ok=False)
            attempts.append((began, time.monotonic(), result.returncode))
            raced.set()
    thread = threading.Thread(target=racer)
    thread.start()
    require(raced.wait(timeout=10), 'racer did not submit a start')
    closing_at = time.monotonic()
    end = close(groups)
    closed_at = time.monotonic()
    stop.set()
    thread.join(timeout=10)
    require(not thread.is_alive() and attempts, 'concurrent start test incomplete')
    require(any(a <= closed_at and b >= closing_at for a, b, _ in attempts),
            'no start overlapped closure; experiment inconclusive, leave closed')
    # KillMode=process deliberately leaves a descendant: MainPID=0 is insufficient.
    until = time.monotonic() + 8
    while state(SERVICES[2])['MainPID'] != '0':
        require(time.monotonic() < until, 'legacy principal did not stop')
        time.sleep(0.05)
    require(not process_free(groups, end), 'residual incorrectly accepted')
    print('residual rejected after principal exit', flush=True)
    ctl('kill', '--kill-who=all', '--signal=SIGKILL', SERVICES[2])
    wait_free(groups, end)
    require(not (ROOT / 'general.entered').exists(), 'in-flight start escaped stop')
    print('ONE_CLOSURE_SCENARIO_PASSED; NO WINDOW CERTIFICATION', flush=True)
    return  # Diagnostic scope ends here; later W9 scenarios are not executed.
    deny_starts()
    # A pre-admitted direct process is outside the contract. Demonstrate, then kill.
    outside = subprocess.Popen([sys.executable, str(ROOT / 'experiment.py'), '--consumer', 'outside'])
    try:
        until = time.monotonic() + 5
        while not (ROOT / 'outside.entered').exists():
            require(time.monotonic() < until, 'outside process did not enter')
            time.sleep(0.02)
        require(masked(), 'masks lost')
        print('direct launch escapes masks: excluded actor, never global proof', flush=True)
    finally:
        outside.kill()
        outside.wait(timeout=5)
    # Simulated loss of volatile *test evidence*, never delete host /run.
    receipt = object()
    current = object()
    require(not reopen_allowed(receipt, current, True, groups), 'stale receipt accepted')
    require(not reopen_allowed(current, current, False, groups), 'incomplete state accepted')
    ctl('daemon-reload')
    require(masked(), 'persistent masks missing after reload; reboot NOT tested')
    for cut in ('before', 'after', 'committed', 'rollback', 'recovery'):
        worker = subprocess.Popen([sys.executable, str(ROOT / 'experiment.py'), '--mutator', cut])
        require(worker.wait(timeout=10) == -signal.SIGKILL, 'mutator death not exercised')
        deny_starts()
        require(not reopen_allowed(current, current, True, groups), 'uncertain state reopened')
        wait_free(groups, time.monotonic() + 10)
        # Explicit synthetic recovery under the same masks, with start attempts.
        for i in range(3):
            deny_starts()
            durable(ROOT / ('source-' + str(i)), 'old')
        (ROOT / 'uncertain').unlink()
        require(reopen_allowed(current, current, True, groups), 'coherent recovery not recognized')
        print('cut passed: ' + cut, flush=True)
    print('BOUNDED_SYSTEMD_ASSERTIONS_PASSED; LEFT MASKED; NO REBOOT OR DB CERTIFICATION', flush=True)


def cleanup():
    # Separate explicit operator action; never finally/unmask after uncertainty.
    require((ROOT / 'experiment.py').is_file(), 'ownership marker absent')
    require(not (ROOT / 'uncertain').exists(), 'uncertain; inspect/recover before cleanup')
    groups = {u: state(u)['ControlGroup'] for u in SERVICES}
    wait_free(groups, close(groups))
    for u in UNITS:
        require((ETC / u).is_symlink() and os.readlink(ETC / u) == '/dev/null', 'foreign mask')
        (VENDOR / u).unlink()  # Remove launch target before explicit unmask.
    ctl('daemon-reload')
    ctl('unmask', *UNITS)
    ctl('daemon-reload')
    # Preserve logs and source artifacts for review. No recursive deletion.
    print('synthetic units removed; evidence retained at ' + str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--authorized-disposable-linux', action='store_true')
    parser.add_argument('--cleanup', action='store_true')
    parser.add_argument('--consumer', choices=(*ROLES, 'outside'))
    parser.add_argument('--pre', choices=ROLES)
    parser.add_argument('--mutator', choices=('before', 'after', 'committed', 'rollback', 'recovery'))
    args = parser.parse_args()
    require(sys.platform == 'linux', 'Linux required; no systemd simulation accepted')
    require(os.geteuid() == 0, 'root required on explicitly authorized disposable host')
    require(Path('/proc/1/comm').read_text().strip() == 'systemd', 'systemd PID1 required')
    require((CGROOT / 'cgroup.controllers').is_file(), 'cgroup v2 required')
    if args.consumer:
        consumer(args.consumer)
    elif args.pre:
        pre(args.pre)
    elif args.mutator:
        mutator(args.mutator)
    else:
        require(args.authorized_disposable_linux, 'explicit disposable Linux authorization flag required')
        signal.alarm(240)  # One-shot experiment bound; failure leaves current masks intact.
        cleanup() if args.cleanup else run(Path(__file__).resolve())


if __name__ == '__main__':
    main()
