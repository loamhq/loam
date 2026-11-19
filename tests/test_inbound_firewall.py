#!/usr/bin/env python3
"""The inbound wall (lib/loam_deploy.py, "The inbound wall"), rendered and run.

What earns a case here:

  - the script lets in established traffic first (a running SSH session
    survives), then SSH, 80 and 443 and the owner's `firewall.open`, and
    ends by dropping everything else; the unit raises it before the network;
  - run for real in a throwaway network namespace (needs `sudo -n`; skipped
    without it): it builds one chain and one jump, appended last in INPUT,
    for IPv4 and IPv6, and running it again leaves exactly one of each;
  - it stands aside where the server already has a firewall (ufw active, or
    an INPUT policy of DROP), taking its own chain out, and
    `firewall.manage = false` takes it out too;
  - a bad `firewall.open` entry is refused, naming it.

Run: python3 tests/test_inbound_firewall.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_deploy as dep  # noqa: E402

results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:62}{'  | ' + detail if detail else ''}")


real_get = dep.lc.get
conf: dict = {}
dep.lc.get = lambda key, default=None: conf.get(key, real_get(key, default))
try:
    script = dep.inbound_firewall_script()
    unit = dep.inbound_firewall_unit()
    lines = script.splitlines()

    def at(text: str) -> int:
        return next((i for i, x in enumerate(lines) if text in x), -1)

    check("render: established accepted before any port rule",
          0 < at("--ctstate ESTABLISHED,RELATED -j ACCEPT") < at("--dport \"$P\" -j ACCEPT"))
    check("render: the chain ends by dropping", at("$T -A $CHAIN -j DROP") > at("--dport \"$P\""))
    check("render: 80 and 443 TCP, 443 UDP", 'TCP="80 443"' in script and 'UDP="443"' in script)
    check("render: sshd's own ports read at run time, 22 always",
          "sshd -T" in script and "echo 22" in script)
    check("render: appended last in INPUT, never inserted",
          "$T -A INPUT -j $CHAIN" in script and "-I INPUT" not in script)
    check("render: stands aside for ufw, firewalld and a DROP policy",
          "ufw status" in script and "firewalld" in script and "-P INPUT DROP" in script)
    res = subprocess.run(["sh", "-n"], input=script, capture_output=True, text=True)
    check("render: sh -n accepts it", res.returncode == 0, res.stderr.strip()[:80])
    check("unit: oneshot before the network, runs the script, wanted at boot",
          "Type=oneshot" in unit and "Before=network-pre.target" in unit
          and f"ExecStart={dep.INBOUND_FIREWALL_SCRIPT}" in unit
          and "WantedBy=multi-user.target" in unit)
    check("generated() carries both, script executable",
          dep.INBOUND_FIREWALL_SCRIPT in dep.generated() and dep.INBOUND_FIREWALL_UNIT in dep.generated()
          and dep.MODES.get(dep.INBOUND_FIREWALL_SCRIPT) == 0o755)

    conf["firewall.open"] = ["22000/tcp", "21027/udp", 8443]
    s2 = dep.inbound_firewall_script()
    check("firewall.open: added to the right protocol",
          'TCP="80 443 8443 22000"' in s2 and 'UDP="443 21027"' in s2)
    conf["firewall.open"] = ["22000/sctp"]
    try:
        dep.inbound_firewall_script()
        check("firewall.open: a bad entry is refused", False, "rendered")
    except dep.DeployError as exc:
        check("firewall.open: a bad entry is refused, naming it", "22000/sctp" in str(exc))
    conf.pop("firewall.open")
    conf["firewall.manage"] = False
    check("firewall.manage = false renders MANAGE=no", "MANAGE=no" in dep.inbound_firewall_script())
    conf.pop("firewall.manage")

    check("words: guarded by Loam", dep.inbound_wall_words("inbound: IPv4:loam IPv6:loam")
          .startswith("Inbound firewall on: only SSH, 80 and 443"))
    check("words: another firewall", "(ufw)" in dep.inbound_wall_words("inbound: IPv4:ufw IPv6:ufw"))
    check("words: open is said as NOT on", "NOT on" in dep.inbound_wall_words("inbound: IPv4:open IPv6:loam"))

    # -- for real, in a network namespace of its own -------------------------
    can = (shutil.which("unshare") and shutil.which("iptables") or Path("/usr/sbin/iptables").exists()) \
        and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0
    if not can:
        print("SKIP  live run: needs sudo -n, unshare and iptables")
    else:
        tmp = Path(tempfile.mkdtemp(prefix="inbound-fw-"))
        try:
            fake = tmp / "bin"
            fake.mkdir()
            # No ufw, no firewalld, an sshd on 2222: the namespace's own world.
            (fake / "ufw").write_text("#!/bin/sh\necho 'Status: inactive'\n")
            (fake / "systemctl").write_text("#!/bin/sh\nexit 3\n")
            (fake / "sshd").write_text("#!/bin/sh\necho 'port 2222'\n")
            for f in fake.iterdir():
                f.chmod(0o755)
            sh = tmp / "firewall.sh"
            sh.write_text(script)
            sh.chmod(0o755)
            path = f"{fake}:/usr/sbin:/usr/bin:/sbin:/bin"

            def ns(body: str) -> subprocess.CompletedProcess:
                return subprocess.run(["sudo", "-n", "unshare", "-n", "env", f"PATH={path}",
                                       "sh", "-c", body], capture_output=True, text=True)

            res = ns(f"{sh} && {sh} && iptables -S INPUT && iptables -S {dep.INBOUND_CHAIN} "
                     f"&& ip6tables -S INPUT && {sh} status")
            out = res.stdout
            check("live: applies twice cleanly", res.returncode == 0, res.stderr.strip()[-120:])
            check("live: one jump, IPv4 and IPv6",
                  out.count(f"-A INPUT -j {dep.INBOUND_CHAIN}") == 2, out[-200:])
            check("live: SSH on sshd's port and 22, 80, 443 TCP, 443 UDP open",
                  all(f"-p tcp -m tcp --dport {p} -j ACCEPT" in out for p in (22, 2222, 80, 443))
                  and "-p udp -m udp --dport 443 -j ACCEPT" in out)
            check("live: status says Loam guards both", out.strip().endswith("inbound: IPv4:loam IPv6:loam"),
                  out.strip().splitlines()[-1])
            res = ns(f"{sh} >/dev/null && iptables -P INPUT DROP && {sh} && iptables -S INPUT")
            check("live: an INPUT DROP policy of the server's own: stands aside, chain gone",
                  "IPv4:own-rules" in res.stdout and "IPv6:loam" in res.stdout
                  and f"-j {dep.INBOUND_CHAIN}" not in res.stdout, res.stdout[-200:])
            (fake / "ufw").write_text("#!/bin/sh\necho 'Status: active'\n")
            res = ns(f"{sh}")
            check("live: ufw active: stands aside for both", "IPv4:ufw IPv6:ufw" in res.stdout,
                  res.stdout.strip())
            (fake / "ufw").write_text("#!/bin/sh\necho 'Status: inactive'\n")
            off = tmp / "off.sh"
            off.write_text(script.replace("MANAGE=yes", "MANAGE=no"))
            off.chmod(0o755)
            res = ns(f"{sh} >/dev/null && {off} && iptables -S INPUT")
            check("live: manage = false takes it out", "IPv4:off" in res.stdout
                  and f"-j {dep.INBOUND_CHAIN}" not in res.stdout, res.stdout[-160:])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
finally:
    dep.lc.get = real_get

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
