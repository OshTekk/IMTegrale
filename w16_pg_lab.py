"""Own a new PostgreSQL-only lab; redacted test reports, bounded verified cleanup."""
from contextlib import contextmanager
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import unittest

import psycopg

_run = subprocess.run  # Keep cluster lifecycle outside simulated systemd transport.


class DisposablePostgres:
    def __init__(self, journal):
        self.journal=Path(journal)
        self.journal.mkdir(mode=0o700,parents=True,exist_ok=False)
        self.bin=Path(os.environ.get('W16_POSTGRES_BIN','/opt/homebrew/opt/postgresql@17/bin'))
        self.root=Path(tempfile.mkdtemp(prefix='w16-pg-',dir='/tmp')).resolve()
        self.data=self.root/'data'
        self.socket=self.root/'socket'
        self.socket.mkdir(mode=0o700)
        self.prefix=[]
        if os.geteuid()==0:
            import pwd
            user=pwd.getpwnam('nobody')
            for p in (self.root,self.socket): os.chown(p,user.pw_uid,user.pw_gid)
            self.prefix=['/usr/sbin/runuser','-u','nobody','--']
        self.params=dict(host=str(self.socket),port=6546,user='w16_admin',dbname='postgres',autocommit=True,connect_timeout=5,options='-c statement_timeout=5000')
        self.serial=0
        self.stopped=False
        self.removed=False
        (self.journal/'resources.json').write_text(json.dumps({'created_root':str(self.root),'tcp':False,'existing_cluster_used':False}))

    def call(self,args,label,timeout=30):
        p=_run(self.prefix+[str(a) for a in args],capture_output=True,timeout=timeout)
        (self.journal/label).write_bytes(p.stdout+p.stderr)
        return p

    def start(self):
        self.serial+=1
        p=self.call([self.bin/'pg_ctl','-D',self.data,'-l',self.root/'server.log','-w','-t','20','-o',
                     "-c listen_addresses='' -k "+str(self.socket)+' -p 6546 -c max_prepared_transactions=4 -c wal_level=logical','start'],f'start-{self.serial}.log')
        if p.returncode: raise RuntimeError('owned PostgreSQL start failed')

    def initialize(self):
        if self.call([self.bin/'initdb','-D',self.data,'--username=w16_admin','--auth=trust','--no-locale','--encoding=UTF8'],'initdb.log').returncode:
            raise RuntimeError('owned PostgreSQL initialization failed')
        (self.data/'pg_hba.conf').write_text('local all w16_consumer scram-sha-256\nlocal all w16_admin trust\n')
        self.start()
        with psycopg.connect(**self.params) as c:
            version=c.execute('SHOW server_version_num').fetchone()[0]
            if version!='170011': raise RuntimeError('exact PostgreSQL 17.11 required')
            c.execute("CREATE ROLE w16_consumer LOGIN PASSWORD 'w16-synthetic-only'")

    def restart(self):
        p=self.call([self.bin/'pg_ctl','-D',self.data,'-m','immediate','-w','-t','20','stop'],f'crash-{self.serial}.log')
        if p.returncode: raise RuntimeError('owned crash stop failed')
        if self.call([self.bin/'pg_ctl','-D',self.data,'status'],f'crash-status-{self.serial}.log').returncode!=3:
            raise RuntimeError('owned stopped state unverified')
        self.start()

    def cleanup(self):
        if (self.data/'PG_VERSION').exists():
            status=self.call([self.bin/'pg_ctl','-D',self.data,'status'],'cleanup-initial-status.log')
            if status.returncode!=3:
                self.call([self.bin/'pg_ctl','-D',self.data,'-m','fast','-w','-t','20','stop'],'cleanup-stop.log')
            status=self.call([self.bin/'pg_ctl','-D',self.data,'status'],'cleanup-final-status.log')
            if status.returncode!=3:
                self.call([self.bin/'pg_ctl','-D',self.data,'-m','immediate','-w','-t','10','stop'],'cleanup-stop-retry.log')
                status=self.call([self.bin/'pg_ctl','-D',self.data,'status'],'cleanup-final-recheck.log')
            if status.returncode!=3: raise RuntimeError('URGENT owned cluster stop unverified; preserve resources')
        self.stopped=True
        if (self.root/'server.log').exists():
            log=(self.root/'server.log').read_bytes()
            if len(log)>2_000_000: raise RuntimeError('required synthetic log too large; refuse certification')
            (self.journal/'server.log').write_bytes(log)
        shutil.rmtree(self.root)
        self.removed=not self.root.exists()


def exercise(journal, window=None, selected=None):
    import test_w16_postgres_admission as tests
    journal=Path(journal)
    journal.mkdir(mode=0o700,parents=True,exist_ok=False)
    report={'passed':False,'production_modified':False,'systemd_observations':'real' if window else 'simulated','postgres_version':'17.11','global_certification':False,
            'tests':0,'failures':[],'errors':[],'skips':0,'cluster_stopped_verified':True,'only_created_resources_removed':True}
    names=unittest.defaultTestLoader.getTestCaseNames(tests.AdmissionCandidate)
    if selected is not None:
        if len(set(selected))!=len(selected) or not set(selected)<=set(names):
            raise RuntimeError('invalid selected qualification cases')
        names=list(selected)
    redo=False
    try:
        for name in names:
            # Fresh cluster PER CASE: slots/subscriptions/origins/prepared work
            # are never resolved just to make the next case pass.
            lab=DisposablePostgres(journal/name)
            try:
                lab.initialize()
                tests.PARAMS=lab.params
                tests.RESTART=lab.restart
                tests.WINDOW=window
                stream=io.StringIO()
                suite=unittest.TestSuite([tests.AdmissionCandidate(name)])
                outcome=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
                report['tests']+=outcome.testsRun
                report['failures'] += [t.id() for t,_ in outcome.failures]
                report['errors'] += [t.id() for t,_ in outcome.errors]
                report['skips']+=len(outcome.skipped)
                if window is None:
                    (lab.journal/'local-test-details.txt').write_text(stream.getvalue())
                if (lab.root/'server.log').exists():
                    redo=redo or 'redo starts at' in (lab.root/'server.log').read_text()
            finally:
                tests.PARAMS=tests.RESTART=tests.WINDOW=None
                lab.cleanup()
                report['cluster_stopped_verified'] &= lab.stopped
                report['only_created_resources_removed'] &= lab.removed
        expected=25 if selected is None else len(selected)
        report['passed']=(report['tests']==expected and not report['failures'] and not report['errors']
                          and not report['skips'] and redo)
        report['crash_recovery_redo_observed']=redo
    except BaseException as e:
        report['failure_type']=type(e).__name__
    finally:
        (journal/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print('W16_ADMISSION_RESULT='+json.dumps(report,sort_keys=True),flush=True)
    if not report['passed']: raise RuntimeError('W16 candidate qualification failed; details redacted')
    return report


if __name__=='__main__':
    def interrupted(signum,_): raise InterruptedError('bounded synthetic interrupt')
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP): signal.signal(sig,interrupted)
    path=Path('.git')/('w16-local-pg-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    try: exercise(path)
    except BaseException:
        print('W16_QUALIFICATION_REFUSED; private details withheld',flush=True)
        raise SystemExit(2) from None
