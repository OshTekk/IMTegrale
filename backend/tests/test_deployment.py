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
    assert "$botnote_admin_ingress = 0" in nginx
    assert "--port 8443" in service
    assert "tcp dport 8443" in firewall
    assert "tcp dport 8080" not in firewall


def test_services_share_the_atomic_release_runtime() -> None:
    web_service = read("deploy/botnote-web.service")
    worker_service = read("deploy/botnote-worker.service")
    cli = read("deploy/botnote-cli")
    runtime_env = read("deploy/botnote-runtime.env")

    for config in (web_service, worker_service, cli):
        assert "/opt/botnote/runtime/bin/botnote" in config
        assert "/opt/botnote/venv/bin/botnote" not in config
        assert "/etc/botnote/botnote-runtime.env" in config

    assert "cd /opt/botnote/current" in cli
    assert "ExecStart=/opt/botnote/runtime/bin/botnote sync-due" in worker_service
    timer = read("deploy/botnote-worker.timer")
    assert "OnCalendar=*-*-* *:00/15:00" in timer
    assert "OnUnitActiveSec" not in timer
    assert "OnUnitInactiveSec" not in timer
    assert "BOTNOTE_BIND_HOST=192.168.50.18" in runtime_env
    assert "BOTNOTE_TRUSTED_PROXY_IPS='[\"192.168.50.5\"]'" in runtime_env
    assert "BOTNOTE_BACKEND_TLS_CERT=/etc/botnote/mtls/server.crt" in runtime_env


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
