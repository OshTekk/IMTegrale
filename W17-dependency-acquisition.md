# W17 dependency acquisition correction

The first two W17 runs stopped before private input reception and before any
Linux/systemd plus PostgreSQL composition. Run 43047345093 reported DNS failure
for azure.archive.ubuntu.com; run 43049271311 timed out during APT update at
45 seconds, without a specific DNS diagnosis. These are infrastructure failures,
not demonstrated manager defects. Two of three W17 runs remain consumed.

The acquisition helper checks required build tools and tests psycopg/libpq with
isolated system Python. A successful import avoids APT entirely. Otherwise it
uses a fresh temporary APT list/cache context with explicit official Ubuntu
HTTPS sources and the Ubuntu archive signing key. Update and install share the
same source overrides; other configured mirrors and stale lists are excluded.
Bison/Flex must already exist, as documented for the image used by both failures;
configure still checks compatibility. Only python3-psycopg is requested for
installation, without upgrading already installed packages.

Both APT operations retain the 45-second limit, zero retries and 10-second
transport limits. An unsuccessful update prevents installation; unsuccessful
installation or import prevents source build and private input reception. Every
subprocess starts in a separate session and its process group is killed on
completion, timeout or interruption. The temporary APT context is removed on
ordinary exits and interrupts. Host loss/SIGKILL cannot guarantee cleanup; the
runner remains disposable. Repository Actions restoration remains the independent
responsibility of the existing external execution driver.

Command output is discarded. Fixed phase/return-code markers are logged. On APT
failure only, bounded DNS and HTTPS checks probe three fixed public hostnames;
no IP, environment, proxy, resolver, private source or unrelated journal is logged.
The helper requires Linux root, Ubuntu 24.04 amd64 and the explicit hosted-runner
marker supplied by the workflow before any acquisition.

Offline tests cover installed dependencies, isolated sources, update timeout,
incomplete indexes, install failure, failed post-install import, missing tool or
keyring, interruption cleanup, allowlisted diagnostics and real process-group
termination. These validate control flow; they do not prove hosted DNS or APT
availability. The local Docker client has no reachable daemon; no real Ubuntu
24.04 acquisition was executed locally. Current public endpoints were reachable
from the Mac only. A final hosted attempt remains a qualification experiment,
with no guarantee that dependency acquisition or compilation fits the existing
step budget. No timeout is increased.

The PostgreSQL source URL/hash, build commands, private input handling, corrected
manager, historical tests, composition and confinement remain unchanged. W16's
25 PostgreSQL cases and 47 historical regressions are carried evidence from
unchanged bytes, not newly executed tests. W17 is still unqualified. No production,
global rotation, deployed caller mapping or privileged authority is certified.

References:
- https://github.com/actions/runner-images/blob/ubuntu24/20261101.360/images/ubuntu/Ubuntu2404-Readme.md
- https://www.postgresql.org/docs/17/install-requirements.html
- https://manpages.ubuntu.com/manpages/noble/man8/apt-get.8.html
- https://manpages.ubuntu.com/manpages/noble/man5/sources.list.5.html
