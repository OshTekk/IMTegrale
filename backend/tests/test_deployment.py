import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def test_internal_proxy_requires_mutual_tls_and_a_trusted_identity_header() -> None:
    nginx = read("deploy/pve/botnote-nginx.conf")
    service = read("deploy/botnote-web.service")
    firewall = read("deploy/security/lxc-nftables.conf")

    assert "proxy_pass https://botnote_backend" in nginx
    assert "proxy_ssl_verify on" in nginx
    assert "proxy_ssl_certificate /etc/botnote-backend-mtls/client.crt" in nginx
    assert "proxy_ssl_name botnote-lxc.internal" in nginx
    assert "X-BotNote-Client-Identity $botnote_client_identity" in nginx
    assert "Tailscale-Funnel-Request".casefold().replace("-", "_") in nginx.casefold()
    assert '"internet:$http_x_forwarded_for"' in nginx
    assert 'default "peer:$remote_addr";' in nginx
    assert '~^443\\| "lan:$remote_addr";' in nginx
    assert 'default "lan:$remote_addr";' not in nginx
    assert "$botnote_private_ingress = 0" in nginx
    assert "~^443\\|\\|$ 1;" in nginx
    assert "~^18080\\|\\|.+$ 1;" in nginx
    assert "--port 8443" in service
    assert "tcp dport 8443" in firewall
    assert "tcp dport 8080" not in firewall


def test_admin_console_uses_api_limits_while_admin_login_stays_strict() -> None:
    nginx = read("deploy/pve/botnote-nginx.conf")
    login_block = nginx.split("location = /api/v1/admin/auth/login", 1)[1].split(
        "location ~ ^/(?:api/v1/admin(?:/|$)|admin(?:/|$))",
        1,
    )[0]
    console_block = nginx.split(
        "location ~ ^/(?:api/v1/admin(?:/|$)|admin(?:/|$))",
        1,
    )[1].split("location ~ ^/api/v1/auth/login", 1)[0]

    assert "limit_req zone=botnote_auth " in login_block
    assert "limit_req zone=botnote_api " in console_block
    assert "limit_req zone=botnote_auth " not in console_block


def test_services_share_the_atomic_release_runtime() -> None:
    web_service = read("deploy/botnote-web.service")
    legacy_worker_service = read("deploy/botnote-worker.service")
    worker_service = read("deploy/botnote-job-worker@.service")
    scheduler_service = read("deploy/botnote-scheduler.service")
    cli = read("deploy/botnote-cli")
    runtime_env = read("deploy/botnote-runtime.env")

    for config in (
        web_service,
        legacy_worker_service,
        worker_service,
        scheduler_service,
        cli,
    ):
        assert "/opt/botnote/runtime/bin/botnote" in config
        assert "/opt/botnote/venv/bin/botnote" not in config
        assert "/etc/botnote/botnote-runtime.env" in config

    assert "cd /opt/botnote/current" in cli
    assert "ExecStart=/opt/botnote/runtime/bin/botnote sync-due" in legacy_worker_service
    assert "ConditionPathExists=/etc/botnote/enable-legacy-worker" in legacy_worker_service
    assert "ExecStart=/opt/botnote/runtime/bin/botnote worker %i" in worker_service
    assert "ExecStart=/opt/botnote/runtime/bin/botnote worker scheduler" in scheduler_service
    timer = read("deploy/botnote-worker.timer")
    assert "OnCalendar=*-*-* *:00/15:00" in timer
    assert "OnUnitActiveSec" not in timer
    assert "OnUnitInactiveSec" not in timer
    assert "BOTNOTE_BIND_HOST=192.168.50.18" in runtime_env
    assert "BOTNOTE_TRUSTED_PROXY_IPS='[\"192.168.50.5\"]'" in runtime_env
    assert "BOTNOTE_BACKEND_TLS_CERT=/etc/botnote/mtls/server.crt" in runtime_env


def test_api_process_does_not_own_background_schedulers() -> None:
    main = read("backend/app/main.py")

    assert "BackgroundTasks" not in read("backend/app/routers/sync.py")
    assert "automatic_sync_scheduler" not in main
    assert "calendar_sync_scheduler" not in main


def test_tailnet_management_filter_runs_before_tailscale_acceptance() -> None:
    helper = read("deploy/security/botnote-tailnet-firewall")
    service = read("deploy/security/botnote-tailnet-firewall.service")

    assert "-t raw -I PREROUTING 1 -i tailscale0" in helper
    assert '/etc/default/botnote-network' in helper
    assert ': "${ADMIN_V4:?ADMIN_V4 is required}"' in helper
    assert '-d "$LXC_LAN_V4"/32 -s "$ADMIN_V4"/32' in helper
    assert "botnote-tailnet-lan-snat" in helper
    assert "codex-tailnet-lan-snat" not in helper
    assert "--dport 22 -j DROP" in helper
    assert "--dports 22,3128,8006 -j DROP" in helper
    assert "--dport 18080" in helper
    assert "--uid-owner 0" in helper
    assert "ExecStart=/bin/sh /usr/local/sbin/botnote-tailnet-firewall apply" in service


def test_deployment_shell_helpers_are_syntactically_valid() -> None:
    helpers = [
        "deploy/security/botnote-tailnet-firewall",
        "deploy/pve/botnote-renew-backend-mtls",
        "deploy/pve/botnote-renew-cert",
        "deploy/backup.sh",
        "deploy/restore-test.sh",
    ]
    for helper in helpers:
        result = subprocess.run(
            ["/bin/sh", "-n", str(ROOT / helper)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{helper}: {result.stderr}"


def test_database_backups_are_streamed_directly_to_recipient_encryption() -> None:
    backup = read("deploy/backup.sh")
    service = read("deploy/botnote-backup.service")

    assert "| age --recipient" in backup
    assert "botnote-${STAMP}.dump.age" in backup
    assert "--file=\"${TEMP}\"" not in backup
    assert "pg_restore --list \"${TEMP}\"" not in backup
    assert "/etc/botnote/backup-age-recipient" in service
    assert 'latest.dump.age' in backup
    assert 'LATEST_TEMP' in backup
    assert 'mv -Tf "${LATEST_TEMP}" "${LATEST}"' in backup
    assert 'ExecStart=/usr/local/libexec/botnote-backup' in service


def test_encrypted_backup_restore_is_isolated_and_never_writes_plaintext() -> None:
    restore = read("deploy/restore-test.sh")
    service = read("deploy/botnote-restore-test.service")
    timer = read("deploy/botnote-restore-test.timer")

    assert 'ACTUAL_DATABASE' in restore
    assert 'botnote_restore_test' in restore
    assert 'RESTORE_TARGET_NOT_ISOLATED' in restore
    assert 'age --decrypt --identity "${IDENTITY_FILE}" "${BACKUP_FILE}" \\' in restore
    assert '| pg_restore --clean --if-exists --exit-on-error' in restore
    assert '.dump"' not in restore
    assert 'current --check-heads' in restore
    assert 'User=botnote-restore' in service
    assert 'ReadWritePaths=/var/lib/botnote-restore' in service
    assert 'OnCalendar=monthly' in timer


def test_operational_alert_timer_uses_only_stable_aggregate_codes() -> None:
    service = read("deploy/botnote-operations-check.service")
    timer = read("deploy/botnote-operations-check.timer")

    assert 'botnote operations-check' in service
    assert 'OnUnitActiveSec=5min' in timer
    assert 'Persistent=true' in timer


def test_alembic_paths_are_anchored_to_the_config_file() -> None:
    config = read("alembic.ini")

    assert "script_location = %(here)s/backend/alembic" in config
    assert "prepend_sys_path = %(here)s/backend" in config


def test_runtime_dependencies_are_exactly_pinned() -> None:
    requirements = [
        line.strip()
        for line in read("deploy/requirements.lock").splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert "fastapi==0.139.0" in requirements
    assert "SQLAlchemy==2.0.51" in requirements
    assert "psycopg-binary==3.3.4" in requirements
    assert all(re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+", line) for line in requirements)
