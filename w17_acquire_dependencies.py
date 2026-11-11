"""Bounded public dependency acquisition, strictly before private inputs."""
import os
from pathlib import Path
import shutil
import signal
import platform
import subprocess
import tempfile

ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C',
       'DEBIAN_FRONTEND': 'noninteractive'}
HOSTS = ('archive.ubuntu.com', 'security.ubuntu.com', 'ftp.postgresql.org')
PROBE = ('/usr/bin/python3', '-I', '-B', '-c',
         'import psycopg; from psycopg import pq; assert pq.version() > 0')


def emit(phase, rc):
    print('W17_DEPENDENCY phase=' + phase + ' rc=' + str(rc), flush=True)


def bounded(args, seconds):
    """No command output; always terminate the process group on interruption."""
    p = subprocess.Popen(args, env=ENV, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        return p.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        return 124
    finally:
        # Include descendants even if their parent exited first.
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait(timeout=5)


def diagnose():
    # Fixed public names only; neither resolver configuration nor IPs are logged.
    for host in HOSTS:
        emit('dns_' + host, bounded(['/usr/bin/getent', 'ahostsv4', host], 3))
        emit('https_' + host, bounded([
            '/usr/bin/curl', '--head', '--fail', '--silent', '--show-error',
            '--proto', '=https', '--connect-timeout', '3', '--max-time', '5',
            'https://' + host + '/'], 6))


def apt_options(folder):
    return [
        '-o', 'Dir::Etc::sourcelist=' + str(folder / 'ubuntu.sources'),
        '-o', 'Dir::Etc::sourceparts=-',
        '-o', 'Dir::State::lists=' + str(folder / 'lists'),
        '-o', 'Dir::Cache=' + str(folder / 'cache'),
        '-o', 'Acquire::Retries=0',
        '-o', 'Acquire::http::Timeout=10', '-o', 'Acquire::https::Timeout=10',
        '-o', 'Acquire::Languages=none',
        '-o', 'Acquire::IndexTargets::deb::DEP-11::DefaultEnabled=false',
        '-o', 'APT::Update::Error-Mode=any',
        '-o', 'APT::Get::AllowUnauthenticated=false',
        '-o', 'Acquire::AllowInsecureRepositories=false',
        '-o', 'Acquire::AllowDowngradeToInsecureRepositories=false',
    ]


def acquire():
    for name in ('bison', 'flex', 'gcc', 'make', 'tar', 'bzip2', 'perl', 'curl'):
        if shutil.which(name, path=ENV['PATH']) is None:
            emit('missing_' + name, 1)
            return 1
    # configure enforces actual compiler/build tool version compatibility later.
    rc = bounded(PROBE, 10)
    emit('installed_psycopg_libpq', rc)
    if rc == 0:
        return 0
    keyring = Path('/usr/share/keyrings/ubuntu-archive-keyring.gpg')
    if not keyring.is_file():
        emit('ubuntu_keyring', 1)
        return 1
    with tempfile.TemporaryDirectory(prefix='w17-apt-', dir='/tmp') as directory:
        folder = Path(directory)
        folder.chmod(0o755)  # _apt must be able to traverse the isolated context.
        for name in ('lists', 'cache'):
            (folder / name).mkdir(mode=0o755)
        (folder / 'ubuntu.sources').write_text(
            'Types: deb\nURIs: https://archive.ubuntu.com/ubuntu\n'
            'Suites: noble noble-updates\nComponents: main universe\n'
            'Architectures: amd64\nSigned-By: ' + str(keyring) + '\n\n'
            'Types: deb\nURIs: https://security.ubuntu.com/ubuntu\n'
            'Suites: noble-security\nComponents: main universe\n'
            'Architectures: amd64\nSigned-By: ' + str(keyring) + '\n')
        (folder / 'ubuntu.sources').chmod(0o644)
        options = apt_options(folder)
        for phase, command in (
                ('apt_update', ['update', '-qq']),
                ('apt_install', ['install', '-y', '-qq', '--no-install-recommends',
                                 '--no-upgrade', 'python3-psycopg'])):
            emit(phase + '_start', 0)
            rc = bounded(['/usr/bin/apt-get', *options, *command], 45)
            emit(phase, rc)
            if rc != 0:
                diagnose()
                return rc
        rc = bounded(PROBE, 10)
        emit('acquired_psycopg_libpq', rc)
        return rc


def interrupted(signum, _frame):
    raise InterruptedError('dependency acquisition interrupted')


if __name__ == '__main__':
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, interrupted)
    if os.environ.get('W17_RUNNER_ENVIRONMENT') != 'github-hosted':
        raise SystemExit('GitHub-hosted runner marker required')
    if os.geteuid() != 0 or os.uname().sysname != 'Linux':
        raise SystemExit('disposable Linux root required')
    release = platform.freedesktop_os_release()
    if release.get('ID') != 'ubuntu' or release.get('VERSION_ID') != '24.04' or platform.machine() != 'x86_64':
        raise SystemExit('Ubuntu 24.04 required')
    raise SystemExit(acquire())
