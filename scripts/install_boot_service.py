"""Install Ubuntu boot startup for already-built Ramon containers.

Run as the normal Docker user; sudo is used only for systemd installation.
"""
import argparse
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tempfile


SERVICES = ("model", "dashboard", "training-dashboard", "market-dashboard",
            "trade-outbox", "kronos-advisor")


def quoted(value, *, command=False):
    value = str(value)
    if any(c in value for c in "\n\r\x00"):
        raise ValueError("unsupported newline in systemd path")
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if command:
        value = value.replace("$", "$$")
    return '"' + value + '"'


def render_unit(repo, user, home, docker):
    prefix = " ".join(quoted(v, command=True) for v in
                      (docker, "compose", "--project-directory", repo, "--profile", "advisors"))
    services = " ".join(SERVICES)
    return f"""[Unit]
Description=Ramon dashboards and model services
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
RequiresMountsFor={quoted(repo)}
StartLimitIntervalSec=0

[Service]
Type=oneshot
RemainAfterExit=yes
User={user}
WorkingDirectory={str(repo).replace('%', '%%')}
Environment={quoted('HOME=' + str(home))}
# Keep the optional synchronous TimesFM loader off during boot.
Environment=RAMON_TIMESFM3_EXPERIMENTAL_ENABLED=0
ExecStart={prefix} up -d --no-build --pull never --wait --wait-timeout 600 {services}
ExecStop={prefix} stop --timeout 30 {services}
TimeoutStartSec=660
TimeoutStopSec=240
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    account = pwd.getpwuid(os.getuid())
    docker = shutil.which("docker")
    unit = render_unit(repo, account.pw_name, account.pw_dir, docker or "/usr/bin/docker")
    if args.dry_run:
        print(unit, end="")
        return
    if os.getuid() == 0:
        parser.error("Run as your normal Docker user, without sudo; the installer requests sudo itself.")
    if not docker or not shutil.which("systemctl"):
        parser.error("Docker Compose and systemd must already be installed.")
    subprocess.run([docker, "compose", "version"], check=True)
    # Fail before installing a boot job if either local image is missing.
    subprocess.run([docker, "image", "inspect", "ramon-ai-trader:local",
                    "ramon-ai-trader-advisors:local"], check=True, stdout=subprocess.DEVNULL)
    subprocess.run([docker, "compose", "--project-directory", str(repo),
                    "--profile", "advisors", "config", "--quiet"], check=True)
    with tempfile.TemporaryDirectory(prefix="ramon-boot-") as temp:
        source = Path(temp) / "ramon-ai-trader.service"
        source.write_text(unit)
        subprocess.run(["systemd-analyze", "verify", str(source)], check=True)
        subprocess.run(["sudo", "install", "-m", "0644", str(source),
                        "/etc/systemd/system/ramon-ai-trader.service"], check=True)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    subprocess.run(["sudo", "systemctl", "enable", "--now", "docker.service"], check=True)
    subprocess.run(["sudo", "systemctl", "enable", "ramon-ai-trader.service"], check=True)
    subprocess.run(["sudo", "systemctl", "restart", "ramon-ai-trader.service"], check=True)
    print("Boot startup installed. Check: systemctl status ramon-ai-trader.service")


if __name__ == "__main__":
    main()
