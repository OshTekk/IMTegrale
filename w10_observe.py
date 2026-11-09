"""Bounded read-only observations of the fixed W9 synthetic namespace only."""
import json
import os
from pathlib import Path
import re
import subprocess
import time

UNITS = tuple('w9-proof-' + r + '.service' for r in
              ('general', 'sync', 'legacy', 'web', 'scheduler', 'trigger')) + ('w9-proof-legacy.timer',)
PROPS = ('Id', 'LoadState', 'ActiveState', 'SubState', 'MainPID', 'ControlPID',
         'Job', 'ControlGroup', 'Result', 'ExecMainCode', 'ExecMainStatus',
         'TimeoutStopUSec', 'KillMode', 'SendSIGKILL', 'KillSignal', 'FinalKillSignal')
CGROOT = Path('/sys/fs/cgroup')
PROC = Path('/proc')
ETC = Path('/etc/systemd/system')
SAFE_WORDS = set(('failed timeout timed out stopping stopped start stop running dead '
                  'inactive active activating deactivating masked not found permission '
                  'denied killed signal SIGKILL SIGTERM exit code result job cancelled').split())


def redacted(text):
    # Never relay arbitrary journal/stderr text, paths, argv or file contents.
    words = re.findall(r'[A-Za-z]+', str(text)[:2048])
    return {'tokens': [w for w in words if w in SAFE_WORDS][:24],
            'redacted': bool(text), 'input_truncated': len(str(text)) > 2048}


def emit(kind, **fields):
    print('W10_DIAG ' + json.dumps({'kind': kind, **fields}, sort_keys=True), flush=True)


class Observer:
    def __init__(self):
        self.last_units = {}
        self.last_residuals = None
        self.decisions = set()
        self.previous = None
        self.next_at = 0
        self.bytes = 0
        self.journal_seen = set()
        self.limit_reported = False

    def decision(self, phase, unit, reason):
        key = (phase, unit, reason)
        if key not in self.decisions:
            self.decisions.add(key)
            emit('actual_predicate_false', phase=phase, unit=unit, reason=reason)

    def unit_decision(self, phase, unit, values):
        wanted = [('ActiveState', 'inactive'), ('SubState', 'dead')]
        if unit.endswith('.service'):
            wanted.extend((('MainPID', '0'), ('ControlPID', '0')))
        predicates = {key: values.get(key) == value if key in values else None for key, value in wanted}
        predicates['Job'] = values.get('Job') in ('', '0') if 'Job' in values else None
        key = (phase, unit)
        if self.last_units.get(key) != predicates:
            self.last_units[key] = predicates
            self.output({'phase': phase, 'source': 'actual_unit_decision',
                         'unit': unit, 'predicates': predicates,
                         'terminal_quiescence_state': unit_quiescent(unit, values),
                         'exit_state_fields_are_diagnostic': True})

    def residuals(self, pids):
        bounded = tuple(sorted(set(pids))[:32])
        if bounded != self.last_residuals:
            self.last_residuals = bounded
            self.output({'phase': 'final_confinement', 'source': 'actual_process_scan',
                         'processes': [self.process(pid) for pid in bounded], 'truncated': len(pids) > 32})

    def command(self, args, end):
        remaining = end - time.monotonic()
        if remaining <= 0:
            return None, {'status': 'NOT_OBSERVED_budget', 'rc': None, 'stderr': redacted('')}
        try:
            # stdout is capped at parse time; systemd properties/jobs are fixed-size;
            # journal is restricted to three bounded synthetic messages.
            p = subprocess.run(args, capture_output=True, text=True,
                               timeout=min(0.15, remaining), check=False)
            return p.stdout[:16384] if p.returncode == 0 else None, {
                'status': 'ok' if p.returncode == 0 else 'error', 'rc': p.returncode,
                'stderr': redacted(p.stderr), 'stdout_truncated': len(p.stdout) > 16384}
        except Exception as exc:
            return None, {'status': type(exc).__name__, 'rc': None,
                          'stderr': redacted(getattr(exc, 'stderr', '') or '')}

    def process(self, pid):
        try:
            # stat comm is deliberately omitted; start ticks detect PID reuse.
            raw = (PROC / str(pid) / 'stat').read_text()[:4096]
            fields = raw[raw.rfind(')') + 2:].split()
            uid = (PROC / str(pid)).stat().st_uid
            again = (PROC / str(pid) / 'stat').read_text()[:4096]
            if again[again.rfind(')') + 2:].split()[19] != fields[19]:
                return {'pid': pid, 'status': 'PID_REUSED_during_observation'}
            return {'pid': pid, 'uid': uid, 'ppid': int(fields[1]),
                    'state': fields[0] if fields[0] in 'RSDZTWtXxIKP' else 'unknown',
                    'start_ticks': int(fields[19]), 'identity': 'proc-stat-start-ticks'}
        except FileNotFoundError:
            return {'pid': pid, 'status': 'disappeared'}
        except Exception as exc:
            return {'pid': pid, 'status': type(exc).__name__}

    def group(self, group):
        # Only the exact systemd slice/unit trees owned by this experiment.
        if group not in {'/system.slice/' + u for u in UNITS}:
            return {'status': 'OUT_OF_SCOPE', 'empty': None}
        root = CGROOT / group.lstrip('/')
        try:
            if root.is_symlink():
                return {'status': 'OUT_OF_SCOPE_symlink', 'empty': None}
            events = (root / 'cgroup.events').read_text()[:1024]
            populated = dict(line.split() for line in events.splitlines()).get('populated')
            pids = set()
            folders = [root]
            visited = 0
            pid_truncated = False
            while folders and visited < 16:
                folder = folders.pop()
                visited += 1
                lines = (folder / 'cgroup.procs').read_text().splitlines()
                pid_truncated |= len(lines) > 32
                pids.update(int(p) for p in lines[:32])
                folders.extend(p for p in folder.iterdir() if p.is_dir() and not p.is_symlink())
            return {'status': 'present', 'populated': populated,
                    'empty': populated == '0' if populated in ('0', '1') else None,
                    'pids': sorted(pids)[:32], 'truncated': bool(folders) or pid_truncated or len(pids) > 32}
        except FileNotFoundError:
            # Distinguish a removed root from an incomplete/racing observation.
            try:
                root.lstat()
            except FileNotFoundError:
                return {'status': 'disappeared', 'empty': None}
            return {'status': 'incomplete', 'empty': None}
        except Exception as exc:
            return {'status': type(exc).__name__, 'empty': None}

    def snapshot(self, phase, groups, end, force=False):
        now = time.monotonic()
        if not force and now < self.next_at:
            return
        self.next_at = now + 1
        # Observation shares the existing deadline, never extends it. At expiry
        # no new commands: emit the last snapshot explicitly marked stale.
        if now >= end:
            self.output({'phase': phase, 'remaining': 0, 'status': 'EXPIRED',
                         'last_observation': self.previous, 'last_is_stale': True})
            return
        observation_end = min(end, now + 0.6)
        units = {}
        pids = set()
        for unit in UNITS:
            out, command = self.command(('/usr/bin/systemctl', 'show', unit,
                                        '--property=' + ','.join(PROPS)), observation_end)
            entry = {'command': command}
            if out is not None:
                values = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
                # Strict field values; no arbitrary property or path is emitted.
                selected = {}
                for key in PROPS:
                    value = values.get(key)
                    if value is None:
                        continue
                    if key == 'Id':
                        selected[key] = unit if value == unit else 'INVALID'
                    elif key == 'ControlGroup':
                        selected[key] = value if value in ('', '/system.slice/' + unit) else 'OUT_OF_SCOPE'
                    else:
                        selected[key] = value if re.fullmatch(r'[A-Za-z0-9_. -]{0,80}', value) else 'REDACTED'
                entry['properties'] = selected
                entry['unit_status'] = ('disappeared' if values.get('LoadState') == 'not-found'
                                        else 'present' if values.get('Id') == unit and values.get('LoadState')
                                        else 'UNKNOWN_incomplete_identity')
                entry['predicates'] = {key: values.get(key) == wanted if key in values else None
                                       for key, wanted in (('ActiveState', 'inactive'), ('SubState', 'dead'),
                                                           ('MainPID', '0'), ('ControlPID', '0'))}
                entry['predicates']['Job'] = values.get('Job') in ('', '0') if 'Job' in values else None
                if not unit.endswith('.service'):
                    entry['predicates'].pop('MainPID')
                    entry['predicates'].pop('ControlPID')
                for key in ('MainPID', 'ControlPID'):
                    if values.get(key, '').isdigit() and int(values[key]):
                        pids.add(int(values[key]))
                candidates = {groups.get(unit, ''), values.get('ControlGroup', ''), '/system.slice/' + unit} - {''}
                entry['cgroups'] = {}
                for group in sorted(candidates):
                    key = group if group == '/system.slice/' + unit else 'OUT_OF_SCOPE'
                    item = self.group(group)
                    entry['cgroups'][key] = item
                    pids.update(item.get('pids', []))
                blocking = (not all(v is True for v in entry['predicates'].values()) or
                            any(item.get('status') != 'disappeared' and item.get('empty') is not True
                                for item in entry['cgroups'].values()))
                if blocking and unit not in self.journal_seen:
                    journal, status = self.command(('/usr/bin/journalctl', '--unit=' + unit,
                                                   '--no-pager', '-n', '3', '--output=cat'), observation_end)
                    entry['journal'] = {'command': status, 'messages': redacted(journal or '')}
                    if journal is not None:
                        self.journal_seen.add(unit)
            else:
                entry['cgroups'] = {('/system.slice/' + unit): self.group('/system.slice/' + unit)}
                pids.update(entry['cgroups']['/system.slice/' + unit].get('pids', []))
                entry['predicates'] = None  # Never equate observation failure with empty.
            units[unit] = entry
        out, status = self.command(('/usr/bin/systemctl', 'list-jobs', '--no-legend', '--no-pager'), observation_end)
        jobs = None
        if out is not None:
            rows = [line.split() for line in out.splitlines()]
            if status.get('stdout_truncated') or any(len(row) != 4 for row in rows):
                status['status'] = 'INVALID_OR_TRUNCATED_jobs; UNKNOWN'
            else:
                matching = [row for row in rows if row[1] in UNITS]
                if len(matching) > 16 or any(not re.fullmatch(r'[0-9]+', row[0]) or
                        not all(re.fullmatch(r'[A-Za-z0-9_.@-]+', x) for x in row)
                        for row in matching):
                    status['status'] = 'INVALID_OR_TRUNCATED_jobs; UNKNOWN'
                else:
                    jobs = matching
        masks = {}
        for unit in UNITS:
            try:
                p = ETC / unit
                masks[unit] = p.is_symlink() and os.readlink(p) == '/dev/null'
            except Exception as exc:
                masks[unit] = type(exc).__name__
        data = {'units': units, 'jobs': jobs, 'jobs_command': status, 'masks': masks,
                'processes': [self.process(pid) for pid in sorted(pids)[:64]],
                'processes_truncated': len(pids) > 64}
        if force or data != self.previous:
            self.output({'phase': phase, 'remaining': round(max(0, end - time.monotonic()), 3),
                         'observation': data})
        self.previous = data

    def output(self, data):
        encoded = json.dumps(data, sort_keys=True)
        limit = 196608 if data.get('status') == 'EXPIRED' or data.get('phase', '').endswith(('exit', 'failure')) else 131072
        if self.bytes + len(encoded) > limit:
            if not self.limit_reported:
                emit('observation_limit', status='NOT_OBSERVED_output_bound')
                self.limit_reported = True
            return
        self.bytes += len(encoded)
        emit('snapshot', **data)


def unit_quiescent(unit, values):
    """Termination only; failed outcomes remain evidence, never become success."""
    return (unit in UNITS and values.get('Id') == unit
            and (values.get('ActiveState'), values.get('SubState')) in
                {('inactive', 'dead'), ('failed', 'failed')}
            and values.get('Job') in ('', '0')
            and (not unit.endswith('.service') or
                 (values.get('MainPID') == '0' and values.get('ControlPID') == '0')))


def synthetic_processes(end):
    found = []
    for entry in PROC.iterdir():
        if time.monotonic() >= end:
            raise RuntimeError('process observation deadline expired')
        if not entry.name.isdecimal() or int(entry.name) == os.getpid():
            continue
        try:
            args = (entry / 'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError, ProcessLookupError):
            continue
        if any(b'/var/lib/w9-maintenance-proof' in arg or arg.rsplit(b'/', 1)[-1] in
               (b'w9_linux_window.py', b'w9_linux_window_diagnostic.py') for arg in args):
            found.append(int(entry.name))
    return found  # Only PID metadata may be emitted, never process arguments.


def masks_present():
    return all((ETC / unit).is_symlink() and (ETC / unit).lstat().st_uid == 0
               and os.readlink(ETC / unit) == '/dev/null' for unit in UNITS)


def namespace_quiescent(groups, end, observer):
    """Fresh fixed-namespace proof under seven persistent masks; errors refuse."""
    def read(*args):
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('quiescence observation deadline expired')
        result = subprocess.run(args, capture_output=True, text=True,
                                timeout=min(10, remaining), check=False)
        if result.returncode != 0 or len(result.stdout) > 16384:
            raise RuntimeError('quiescence observation failed')
        return result.stdout

    if not masks_present():
        return False
    free = True
    for unit in UNITS:
        text = read('/usr/bin/systemctl', 'show', unit, '--property=' + ','.join(PROPS))
        values = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        required = {'Id', 'LoadState', 'ActiveState', 'SubState', 'Job', 'ControlGroup'}
        if unit.endswith('.service'):
            required |= {'MainPID', 'ControlPID'}
        if not required <= values.keys() or values['Id'] != unit:
            raise RuntimeError('incomplete quiescence observation')
        observer.unit_decision('quiescence', unit, values)
        free &= unit_quiescent(unit, values)
        for group in {groups.get(unit, ''), values['ControlGroup'], '/system.slice/' + unit} - {''}:
            evidence = observer.group(group)
            if evidence['status'] == 'present':
                free &= (evidence.get('empty') is True and not evidence.get('pids')
                         and evidence.get('truncated') is False)
            elif evidence['status'] != 'disappeared':
                raise RuntimeError('cgroup observation incomplete')
    rows = [line.split() for line in read('/usr/bin/systemctl', 'list-jobs',
                                        '--no-legend', '--no-pager').splitlines()]
    if any(len(row) != 4 or not row[0].isdigit() for row in rows):
        raise RuntimeError('invalid job observation')
    free &= not any(row[1] in UNITS for row in rows)
    residuals = synthetic_processes(end)
    observer.residuals(residuals)
    free &= not residuals
    return bool(free and masks_present())


def general_stop_evidence(end):
    """Capture only exit codes and a bounded redacted journal, never Exec argv."""
    unit = 'w9-proof-general.service'
    def read(*args):
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('signal observation deadline expired')
        result = subprocess.run(args, capture_output=True, text=True,
                                timeout=min(10, remaining), check=False)
        if result.returncode or len(result.stdout) > 16384:
            raise RuntimeError('signal observation failed')
        return result.stdout
    text = read('/usr/bin/systemctl', 'show', unit,
                '--property=Id,ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,ExecStartPre,KillSignal')
    values = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
    if values.get('Id') != unit:
        raise RuntimeError('signal unit identity invalid')
    # ExecStartPre includes paths/argv: extract only its result fields.
    pre = re.findall(r'code=([a-z]+)\s*;?\s*status=([0-9]+)', values.get('ExecStartPre', ''))
    evidence = {key: values.get(key) for key in
                ('ActiveState', 'SubState', 'Result', 'ExecMainCode', 'ExecMainStatus', 'KillSignal')}
    if any(value is None or not re.fullmatch(r'[A-Za-z0-9_-]{0,40}', value)
           for value in evidence.values()):
        raise RuntimeError('signal fields incomplete')
    evidence['pre_exits'] = [{'code': code, 'status': int(status)} for code, status in pre]
    evidence['journal'] = redacted(read('/usr/bin/journalctl', '--unit=' + unit,
                                        '--no-pager', '-n', '8', '--output=cat'))
    evidence['expected_pre_sigterm'] = (values.get('KillSignal') == '15'
                                       and pre == [('killed', '15')]
                                       and values.get('ExecMainCode') == '0'
                                       and values.get('ExecMainStatus') == '0'
                                       and values.get('Result') == 'signal')
    emit('general_stop_signal', unit=unit, **evidence)
    return evidence
