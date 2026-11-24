#!/usr/bin/env python3
"""The deployment layer's refusals and renders, against lib/loam_deploy.

What earns a case here is what the exposure story rests on:

  - a record with a third exposure value, a publish off loopback, a bind
    mount, or a privileged container never renders -- the generator
    refuses, and check-drift refuses the same way;
  - a compose file joins the app's own `default` and `loam-broker` and no
    third network, however the third one is spelled, because Caddy lets the
    browser's subnet past a private host's login; and the running box is
    asked the same question, because a network can be joined by hand;
  - every host block is gated unless the operator file names the app, and
    the parser check-drift uses on the LIVE file agrees with the render;
  - a share is a matcher only while the record says shared, the date has
    not passed and a token exists -- any one missing renders private;
  - the sudoers render passes visudo, one alias per buildable app.

Runs against a throwaway deploy directory, a throwaway operator file and a
throwaway share directory, with the module's paths pointed at them and
restored after. Nothing on the box is read or written.

Run: python3 tests/test_deploy.py
"""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Import the worktree under test so a passing result certifies this revision.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_deploy as dep  # noqa: E402

results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:58}{'  | ' + detail if detail else ''}")


def refuses(label: str, fn, needle: str) -> None:
    try:
        fn()
    except dep.DeployError as exc:
        check(label, needle in str(exc), str(exc)[:90])
        return
    check(label, False, "rendered instead of refusing")


COMPOSE_OK = """\
name: {name}
services:
  web:
    image: {name}:${{LOAM_COMMIT:?set by bin/deploy}}
    ports: ["127.0.0.1:{port}:8000"]
    logging: {{driver: journald, options: {{tag: "{{{{.Name}}}}"}}}}
    volumes: ["{name}-data:/data"]
volumes:
  {name}-data: {{}}
"""

# The shape loam-ui really uses: its own default AND the broker's, which is
# the whole of how an app reaches the session broker. `{nets}` is where each
# third-network spelling below goes.
COMPOSE_NET = """\
name: {name}
services:
  web:
    image: {name}:${{LOAM_COMMIT:?set by bin/deploy}}
    container_name: {name}
    ports: ["127.0.0.1:{port}:8000"]
    logging: {{driver: journald, options: {{tag: "{{{{.Name}}}}"}}}}
    networks: [default, broker{extra_join}]
networks:
  broker:
    name: loam-broker
    external: true
{extra_net}"""

root = Path(tempfile.mkdtemp(prefix="loam-deploy-test-"))
saved = {k: getattr(dep, k) for k in ("DEPLOY", "CONFIG_PATH", "PUBLIC_FILE", "SHARE_DIR", "AGENTS", "SITES_DIR")}
try:
    deploy = root / "deploy"
    deploy.mkdir()
    (deploy / "loam.toml").write_text('domain = "example.test"\nauth_host = "auth"\n')
    dep.DEPLOY, dep.CONFIG_PATH = deploy, deploy / "loam.toml"
    dep.PUBLIC_FILE = root / "public"
    dep.SHARE_DIR = root / "share"
    dep.SHARE_DIR.mkdir()
    dep.AGENTS = root / "agents"           # so no real project registry is read
    (dep.AGENTS / "projects").mkdir(parents=True)
    # Use an isolated sites registry for deterministic host counts.
    dep.SITES_DIR = root / "sites"

    def record(name: str, toml: str, compose: str | None = None) -> Path:
        d = deploy / name
        d.mkdir(exist_ok=True)
        (d / "deploy.toml").write_text(toml)
        if compose is not None:
            (d / "compose.yaml").write_text(compose)
        return d

    def clear() -> None:
        for d in deploy.iterdir():
            if d.is_dir():
                shutil.rmtree(d)

    today = dt.date(2026, 9, 8)

    # -- refusals ---------------------------------------------------------
    record("alpha", 'exposure = "public"\n', COMPOSE_OK.format(name="alpha", port=8101))
    refuses("exposure has no third value", dep.records, "no third value")
    clear()
    record("alpha", 'exposure = "private"\n', COMPOSE_OK.format(name="alpha", port=8101).replace("127.0.0.1:", "0.0.0.0:"))
    refuses("publish off loopback refused", dep.records, "only 127.0.0.1")
    clear()
    record("alpha", 'exposure = "private"\n', COMPOSE_OK.format(name="alpha", port=8101).replace("alpha-data:/data", "/home/loam:/data"))
    refuses("bind mount refused", dep.records, "bind-mounts")
    clear()
    record("alpha", 'exposure = "private"\n', COMPOSE_OK.format(name="alpha", port=8101).replace("    ports:", "    privileged: true\n    ports:"))
    refuses("privileged refused", dep.records, "privileged")
    clear()
    record("alpha", 'exposure = "private"\n', COMPOSE_OK.format(name="alpha", port=8101).replace("    ports:", "    network_mode: host\n    ports:"))
    refuses("host network refused", dep.records, "network_mode")
    clear()
    record("alpha", 'exposure = "shared"\n', COMPOSE_OK.format(name="alpha", port=8101))
    refuses("shared without share.expires refused", dep.records, "without share.expires")
    clear()
    record("alpha", 'exposure = "private"\n', COMPOSE_OK.format(name="alpha", port=8101).replace(
        '    logging: {driver: journald, options: {tag: "{{.Name}}"}}\n', ''))
    refuses("an app service without the log block refused", dep.records, "APP_LOGGING")
    clear()
    record("alpha", 'exposure = "private"\n', COMPOSE_OK.format(name="alpha", port=8101).replace(
        "driver: journald", "driver: json-file"))
    refuses("an app service on json-file refused (a redeploy deletes it)", dep.records, "APP_LOGGING")
    clear()

    # -- networks: the two known ones, and every way to spell a third -------
    def netfile(extra_join: str = "", extra_net: str = "") -> str:
        return COMPOSE_NET.format(name="alpha", port=8101,
                                  extra_join=extra_join, extra_net=extra_net)

    record("alpha", 'exposure = "private"\n', netfile())
    apps = {a.name: a for a in dep.records()}
    check("default and loam-broker pass",
          apps["alpha"].containers == {"alpha": ("alpha_default", "loam-broker")},
          str(apps["alpha"].containers))
    clear()
    # Named: the obvious one. The key says `sight`, the name says the
    # browser's network, and the name is what Docker joins.
    record("alpha", 'exposure = "private"\n',
           netfile(", sight", f"  sight:\n    name: {dep.SIGHT_NETWORK}\n    external: true\n"))
    refuses("a third network by name refused", dep.records, dep.SIGHT_NETWORK)
    clear()
    # External with no `name:`: the KEY is the network's name. Checking the
    # keys against a list would have caught this one and missed the last.
    record("alpha", 'exposure = "private"\n',
           netfile(f", {dep.SIGHT_NETWORK}", f"  {dep.SIGHT_NETWORK}:\n    external: true\n"))
    refuses("a third network, external and unnamed, refused", dep.records, dep.SIGHT_NETWORK)
    clear()
    # `default` pointed somewhere else in three words. The service's list is
    # unchanged; only the table moved.
    record("alpha", 'exposure = "private"\n',
           netfile("", f"  default:\n    name: {dep.SIGHT_NETWORK}\n    external: true\n"))
    refuses("`default` redirected to the browser's network refused",
            dep.records, dep.SIGHT_NETWORK)
    clear()
    # Declared and joined by nobody. `docker compose up` still looks it up,
    # and a declaration that reads as authorised is not left lying there.
    record("alpha", 'exposure = "private"\n',
           netfile("", f"  sight:\n    name: {dep.SIGHT_NETWORK}\n    external: true\n"))
    refuses("a third network declared but unjoined refused",
            dep.records, "networks.sight")
    clear()
    # An app's own second bridge is not one of the two either.
    record("alpha", 'exposure = "private"\n', netfile(", inner", "  inner: {}\n"))
    refuses("a second network of the app's own refused", dep.records, "alpha_inner")
    clear()
    record("alpha", 'exposure = "private"\n',
           netfile().replace("networks: [default, broker]", "networks: [broker]"))
    apps = {a.name: a for a in dep.records()}
    check("naming only the broker drops the default, and is allowed",
          apps["alpha"].containers == {"alpha": ("loam-broker",)},
          str(apps["alpha"].containers))
    clear()

    # -- networks: the running box ------------------------------------------
    record("alpha", 'exposure = "private"\n', netfile())
    record("beta", 'exposure = "private"\n', COMPOSE_OK.format(name="beta", port=8102))
    apps = dep.records()
    clean = {"alpha": ("alpha_default", "loam-broker"),
             "beta": ("beta_default",),
             "loam-authelia": ("loam-authelia_default",),
             "loam-browser": (dep.SIGHT_NETWORK,)}
    check("live state matching the records is no finding",
          dep.network_findings(apps, clean) == [], str(dep.network_findings(apps, clean)))
    joined = dict(clean, alpha=("alpha_default", "loam-broker", dep.SIGHT_NETWORK))
    found = dep.network_findings(apps, joined)
    check("an app attached to the browser's network by hand is a finding",
          len(found) == 1 and found[0][0] == "alpha" and dep.SIGHT_NETWORK in found[0][1],
          str(found))
    stranger = dict(clean, probe=(dep.SIGHT_NETWORK,))
    found = dep.network_findings(apps, stranger)
    check("a container no record owns, on a runtime network, is a finding",
          len(found) == 1 and found[0][0] == "probe", str(found))
    found = dep.network_findings(apps, dict(clean, probe=("probe_default",)))
    check("a container no record owns, on its own bridge, is not this check's",
          found == [], str(found))
    found = dep.network_findings(apps, dict(clean, **{"loam-browser": ("bridge",)}))
    check("the browser itself off its own network is a finding",
          len(found) == 1 and found[0][0] == "loam-browser", str(found))
    clear()
    record("alpha", 'exposure = "private"\n', COMPOSE_OK.format(name="alpha", port=8101))
    record("beta", 'exposure = "private"\nhost = "alpha"\n', COMPOSE_OK.format(name="beta", port=8102))
    refuses("duplicate host refused", dep.records, "already used")
    clear()
    record("alpha", 'exposure = "private"\n', COMPOSE_OK.format(name="alpha", port=8101))
    record("beta", 'exposure = "private"\n', COMPOSE_OK.format(name="beta", port=8101))
    refuses("duplicate port refused", dep.records, "port 8101 already used")
    clear()
    record("alpha", 'exposure = "private"\n', COMPOSE_OK.format(name="alpha", port=dep.AUTHELIA_PORT))
    refuses("authelia's port refused", dep.records, f"port {dep.AUTHELIA_PORT} already used by authelia")
    clear()
    record("alpha", 'exposure = "private"\n')
    refuses("record with neither compose nor static_root", dep.records, "neither compose.yaml, [service] nor static_root")
    clear()
    record("alpha", f'exposure = "private"\nhost = "{dep.SECOND_FACTOR_HOST}"\n', COMPOSE_OK.format(name="alpha", port=8101))
    refuses("the sentinel host refused to a record", dep.records, "reserved")
    clear()
    record("alpha", 'exposure = "private"\nhide = [".git"]\n', COMPOSE_OK.format(name="alpha", port=8101))
    refuses("hide on a container record refused", dep.records, "static_root host only")
    clear()
    record("pages", f'exposure = "private"\nstatic_root = "{root}/www"\nhide = ["a b"]\n')
    refuses("hide with a space refused", dep.records, "without spaces")
    clear()
    # -- hosts on an app's own domain: the refusals -----------------------
    own = 'exposure = "private"\n[domains."{dom}"]\nhosts = [{hosts}]\n'
    record("site", own.format(dom="example.test", hosts='"example.test"'), COMPOSE_OK.format(name="site", port=8101))
    refuses("own domain: the runtime's domain refused", dep.records, "use `host = `")
    clear()
    record("site", own.format(dom="sub.example.test", hosts='"sub.example.test"'), COMPOSE_OK.format(name="site", port=8101))
    refuses("own domain: a name under the runtime's domain refused", dep.records, "use `host = `")
    clear()
    record("site", own.format(dom="owner.me", hosts='"owner.me", "elsewhere.org"'), COMPOSE_OK.format(name="site", port=8101))
    refuses("own domain: a host outside the domain refused", dep.records, "not owner.me or a name under it")
    clear()
    record("site", own.format(dom="owner.me", hosts='"auth.owner.me"'), COMPOSE_OK.format(name="site", port=8101))
    refuses("own domain: the portal's name refused", dep.records, "reserved for the login portal")
    clear()
    record("site", own.format(dom="owner.me", hosts=''), COMPOSE_OK.format(name="site", port=8101))
    refuses("own domain: empty hosts refused", dep.records, "needs hosts")
    clear()
    record("site", 'exposure = "private"\ndomains = ["owner.me"]\n', COMPOSE_OK.format(name="site", port=8101))
    refuses("own domain: a bare list refused (the key is the cookie domain)", dep.records, "table of")
    clear()
    record("site", own.format(dom="owner.me", hosts='"owner.me"'), COMPOSE_OK.format(name="site", port=8101))
    record("twin", own.format(dom="owner.me", hosts='"owner.me"'), COMPOSE_OK.format(name="twin", port=8102))
    refuses("own domain: a host used by two records refused", dep.records, "already used by site")
    clear()

    # -- a good set -------------------------------------------------------
    record("alpha", 'exposure = "private"\ncheckout = "/srv/alpha"\n', COMPOSE_OK.format(name="alpha", port=8101))
    record("beta", 'exposure = "shared"\n[share]\nexpires = 2026-10-08\n', COMPOSE_OK.format(name="beta", port=8102))
    record("gamma", 'exposure = "shared"\n[share]\nexpires = 2026-09-01\n', COMPOSE_OK.format(name="gamma", port=8103))
    record("pages", f'exposure = "private"\nstatic_root = "{root}/www"\nhide = [".git", "private/notes"]\n')
    apps = {a.name: a for a in dep.records()}
    check("four records parse", set(apps) == {"alpha", "beta", "gamma", "pages"}, ", ".join(sorted(apps)))
    check("hide read from the record", apps["pages"].hide == (".git", "private/notes"))
    check("no hide means none", apps["alpha"].hide == ())
    check("port read from compose", apps["alpha"].port == 8101)
    check("checkout read from record", apps["alpha"].checkout == Path("/srv/alpha"))
    check("static host has no port", apps["pages"].port is None and apps["pages"].static_root == root / "www")
    check("fqdn from the domain", apps["alpha"].fqdn == "alpha.example.test")

    (dep.SHARE_DIR / "beta.token").write_text("tok-beta\n")
    (dep.SHARE_DIR / "gamma.token").write_text("tok-gamma\n")
    check("share active: shared + date + token", apps["beta"].share_active(today) == ("tok-beta", dt.date(2026, 10, 8)))
    check("share inactive: date passed", apps["gamma"].share_active(today) is None)
    check("share inactive: private record", apps["alpha"].share_active(today) is None)
    os.unlink(dep.SHARE_DIR / "beta.token")
    check("share inactive: no token minted", apps["beta"].share_active(today) is None)
    (dep.SHARE_DIR / "beta.token").write_text("tok-beta\n")

    # -- the Caddyfile ----------------------------------------------------
    text = dep.caddyfile(today)
    blocks = dict(dep.live_host_blocks(text))
    check("portal block emitted", "auth.example.test" in blocks)
    check("every block gated with no operator file", all(blocks.values()), str(blocks))
    check("share matchers only on the live share",   # matcher defined once, handled once
          text.count("@share_link") == 2 and "tok-beta" in text and "tok-gamma" not in text)
    check("share deadline is end of the expiry date UTC",
          f"< {dep._deadline_unix(dt.date(2026, 10, 8))}" in text
          and dep._deadline_unix(dt.date(2026, 10, 8)) == 1791504000)
    # Twice per block: the cross-origin writes refused, then the rest passed.
    check("sight bypass on every private block", text.count(f"remote_ip {dep.SIGHT_SUBNET}") == 2 * 4
          and text.count("handle @sight_cross {") == text.count("handle @sight {") == 4)
    check("static host is a file_server behind the gate",
          "file_server browse" in text and blocks.get("pages.example.test") is True)
    check("every block writes its own access log", dep.live_hosts_unlogged(text) == []
          and all(f"import access_log {h}" in text for h in blocks), str(dep.live_hosts_unlogged(text)))
    check("access log: one file per host under one directory, rolled",
          f"output file {dep.ACCESS_LOG_DIR}/{{args[0]}}.log" in text
          and "roll_size 50MiB" in text and "roll_keep 14" in text and "roll_keep_for 336h" in text)
    check("access log: query, credentials, Referer and response headers filtered",
          all(f in text for f in ("regexp \\?.* ?redacted", "regexp ^/content/[^/?]* /content/redacted",
                                  "request>headers>Cookie delete",
                                  "request>headers>Authorization delete",
                                  "request>headers>Referer delete", "resp_headers delete")))
    unlogged = text.replace("\timport access_log alpha.example.test\n", "")
    check("parser sees a hand-removed access log", dep.live_hosts_unlogged(unlogged) == ["alpha.example.test"],
          str(dep.live_hosts_unlogged(unlogged)))
    check("parser sees the snippet gone as every host unlogged",
          set(dep.live_hosts_unlogged(text.replace("(access_log) {", "(nolog) {"))) == set(blocks))
    check("hide rendered into the file_server block",
          "file_server browse {\n\t\t\thide .git private/notes\n\t\t}" in text)

    dep.PUBLIC_FILE.write_text("# operator's file\nalpha\nnobody\n")
    text = dep.caddyfile(today)
    blocks = dict(dep.live_host_blocks(text))
    check("operator-named app renders without a gate", blocks.get("alpha.example.test") is False)
    check("the others stay gated", all(v for h, v in blocks.items() if h != "alpha.example.test"))
    check("PUBLIC comment names the operator file", f"# PUBLIC: named in {dep.PUBLIC_FILE}" in text)
    check("unknown public name emits nothing but a comment", "names nobody" in text and "nobody.example.test" not in text)
    dep.PUBLIC_FILE.unlink()
    check("absent operator file means nothing public", dep.public_names() == set())

    # the parser check-drift runs on the LIVE file: a hand-edited block
    # that drops the import must read as ungated.
    edited = dep.caddyfile(today).replace("\t\timport gate\n\t\treverse_proxy 127.0.0.1:8101", "\t\treverse_proxy 127.0.0.1:8101")
    check("parser sees a hand-removed gate", dict(dep.live_host_blocks(edited)).get("alpha.example.test") is False)

    # -- the other renders ------------------------------------------------
    browser = dep.browser_compose()
    check("browser: no origin allow-list (the web is open)", "--allowed-origins" not in browser)
    blocked = dep.browser_blocked_origins()
    check("browser: blocked origins flag carries the render", "--blocked-origins" in browser and blocked in browser)
    check("browser: loopback blocked on the default port and every other",
          all(h in blocked.split(";") and f"{h}:*" in blocked.split(";")
              for h in ("localhost", "127.0.0.1", "[::1]", "0.0.0.0")))
    check("browser: the internal ranges as host globs",
          all(g in blocked.split(";") for g in ("10.*", "192.168.*", "169.254.*", "127.*"))
          and "172.{16,17," in blocked and ",31}.*" in blocked and "100.{64," in blocked)
    check("browser: the domain is not blocked", "example.test" not in blocked)
    check("browser allowed hosts both loopback spellings", dep.BROWSER_ALLOWED_HOSTS in browser)
    check("browser on its own subnet", dep.SIGHT_SUBNET in browser and dep.SIGHT_NETWORK in browser)
    check("browser: a page's WebMCP tools never become MCP tools", "- --no-webmcp\n" in browser)
    check("browser: image pinned, never latest", ":latest" not in dep.BROWSER_IMAGE and ":v0.0." in dep.BROWSER_IMAGE)
    browser_doc = dep.yaml.safe_load(browser)["services"]["browser"]
    out_dir = str(dep.BROWSER_OUTPUT)
    check("browser: mounts shared output directory at the same path and sets working_dir",
          browser_doc.get("working_dir") == out_dir
          and f"{out_dir}:{out_dir}" in (browser_doc.get("volumes") or [])
          and browser_doc.get("ulimits", {}).get("core") == 0, str(browser_doc))
    cmd = browser_doc.get("command") or []
    check("browser: writes absolute paths into the shared output dir across every provider",
          "--ignore-roots" in cmd
          and "--output-dir" in cmd and cmd[cmd.index("--output-dir") + 1] == out_dir
          and "--file-paths" in cmd and cmd[cmd.index("--file-paths") + 1] == "absolute"
          and "--output-max-size" in cmd
          and cmd[cmd.index("--output-max-size") + 1] == str(dep.BROWSER_OUTPUT_MAX_BYTES), str(cmd))
    subuid = root / "subuid"
    subgid = root / "subgid"
    subuid.write_text("loam:100000:65536\ndockremap:200000:65536\n")
    subgid.write_text("loam:100000:65536\ndockremap:300000:65536\n")
    check("browser: output owner maps node (1000:1000) through dockremap",
          dep.browser_output_owner(subuid, subgid) == (201000, 301000)
          and dep.browser_output_owner(root / "no-subuid", root / "no-subgid") == (1000, 1000))

    # -- the wall -----------------------------------------------------------
    wall = dep.sight_firewall_script()
    rules = dep.sight_wall_rules()
    check("wall: every internal range rejected in both chains",
          all(f"$IPT -A {c} -d {cidr} -j REJECT" in wall
              for c in (dep.SIGHT_WALL_CHAIN, dep.SIGHT_WALL_HOST_CHAIN)
              for cidr, _ in dep.SIGHT_INTERNAL_RANGES))
    check("wall: the ranges are the six named",
          {c for c, _ in dep.SIGHT_INTERNAL_RANGES} == {
              "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
              "100.64.0.0/10", "169.254.0.0/16", "127.0.0.0/8"})
    host = rules[dep.SIGHT_WALL_HOST_CHAIN]
    check("wall: host chain returns established first", host[0].startswith("-m conntrack --ctstate ESTABLISHED,RELATED -j RETURN"))
    check("wall: host chain rejects the ranges before opening 80/443",
          host.index("-p tcp -m multiport --dports 80,443 -j RETURN") > max(
              host.index(f"-d {cidr} {dep._REJECT}") for cidr, _ in dep.SIGHT_INTERNAL_RANGES))
    check("wall: host chain ends by rejecting everything else", host[-1] == dep._REJECT)
    check("wall: forward chain rejects only the ranges", all(r.startswith("-d ") for r in rules[dep.SIGHT_WALL_CHAIN]))
    check("wall: jumps keyed on the browser subnet from DOCKER-USER and INPUT",
          dep.sight_wall_jumps() == [("DOCKER-USER", f"-s {dep.SIGHT_SUBNET} -j {dep.SIGHT_WALL_CHAIN}"),
                                     ("INPUT", f"-s {dep.SIGHT_SUBNET} -j {dep.SIGHT_WALL_HOST_CHAIN}")]
          and f"jump DOCKER-USER -s {dep.SIGHT_SUBNET} -j {dep.SIGHT_WALL_CHAIN}" in wall
          and f"jump INPUT -s {dep.SIGHT_SUBNET} -j {dep.SIGHT_WALL_HOST_CHAIN}" in wall)
    check("wall: script is sh, set -eu, generated header", wall.startswith("#!/bin/sh\n# GENERATED") and "\nset -eu\n" in wall)
    if shutil.which("sh"):
        res = subprocess.run(["sh", "-n"], input=wall, capture_output=True, text=True)
        check("wall: sh -n accepts the script", res.returncode == 0, res.stderr.strip()[:80])
    unit = dep.sight_firewall_unit()
    check("wall: unit after and part of docker, oneshot, runs the script",
          "After=docker.service" in unit and "PartOf=docker.service" in unit
          and "Type=oneshot" in unit and f"ExecStart={dep.SIGHT_FIREWALL_SCRIPT}" in unit)
    check("wall: script mode executable, unit default", dep.MODES.get(dep.SIGHT_FIREWALL_SCRIPT) == 0o755
          and dep.SIGHT_FIREWALL_UNIT not in dep.MODES)
    authelia = dep.authelia_config()
    check("authelia cookie domain and portal from the domain",
          "domain: example.test" in authelia and "authelia_url: https://auth.example.test" in authelia)
    check("authelia default rule one_factor on *.domain", "domain: '*.example.test'" in authelia)
    # Authelia needs a two-factor rule to offer enrollment. Only the sentinel host should
    # match this rule.
    rules = authelia.split("rules:", 1)[1].split("session:", 1)[0]
    check("authelia: sentinel two_factor rule first, apps one_factor",
          rules.strip().startswith(f"- domain: {dep.SECOND_FACTOR_HOST}.example.test\n    policy: two_factor")
          and rules.count("two_factor") == 1)
    check("no secret in the authelia render", "SECRET" not in authelia and "ENCRYPTION" not in authelia)

    sud = dep.sudoers()
    # alpha names its checkout; beta and gamma have none and no project
    # registry entry under the throwaway agents dir; pages is static.
    check("sudoers: one alias per buildable app", sud.count("Cmnd_Alias") == 1 and "LOAM_DEPLOY_ALPHA" in sud)
    check("sudoers: no checkout, no line", "BETA" not in sud and "GAMMA" not in sud and "PAGES" not in sud)
    check("sudoers: the command and the app, nothing else", f"{dep.DEPLOY_BIN} alpha *" in sud)
    if shutil.which("visudo"):
        tmp = root / "sudoers"
        tmp.write_text(sud)
        os.chmod(tmp, 0o440)
        res = subprocess.run(["visudo", "-c", "-q", "-f", str(tmp)], capture_output=True, text=True)
        check("sudoers: visudo accepts the render", res.returncode == 0, res.stderr.strip()[:80])

    # -- hosts on an app's own domain: the renders ------------------------
    clear()
    record("site", 'exposure = "private"\n[domains."owner.me"]\nhosts = ["owner.me", "www.owner.me"]\n',
           COMPOSE_OK.format(name="site", port=8101))
    record("pages", f'exposure = "private"\nstatic_root = "{root}/www"\n')
    apps = {a.name: a for a in dep.records()}
    check("own domain: parsed as cookie domain -> hosts",
          apps["site"].domains == {"owner.me": ("owner.me", "www.owner.me")}
          and apps["site"].hostnames == ["site.example.test", "owner.me", "www.owner.me"])
    check("own domain: a record without one has none", apps["pages"].domains == {} and apps["pages"].hostnames == ["pages.example.test"])
    check("own domain: extra_domains sorted and unique", dep.extra_domains(list(apps.values())) == ["owner.me"])
    text = dep.caddyfile(today)
    blocks = dict(dep.live_host_blocks(text))
    check("own domain: one block per hostname, all gated",
          {h: blocks.get(h) for h in ("owner.me", "www.owner.me", "site.example.test")} == {"owner.me": True, "www.owner.me": True, "site.example.test": True})
    check("own domain: a portal block on the extra domain, read as gated",
          blocks.get("auth.owner.me") is True and "auth.owner.me {\n\timport access_log auth.owner.me\n\timport compress\n" in text
          and dep.caddy_portal_block("auth.owner.me") in text)
    hosts = [h for h, _ in dep._live_blocks(text)]
    check("compress: every host block imports it, once",
          all(body.splitlines().count("import compress") == 1 for _, body in dep._live_blocks(text))
          and len(hosts) >= 4, ", ".join(hosts))
    check("compress: zstd and gzip, and never an event stream",
          "(compress) {\n\tencode zstd gzip {" in text
          and "header Content-Type application/json*" in text
          and "event-stream" not in text and "header Content-Type text/*" not in text)
    # Three public blocks, two handles each, and one more on the previews'
    # internal route (caddy_preview_block).
    check("own domain: every block proxies the same upstream", text.count("reverse_proxy 127.0.0.1:8101") == 3 * 2 + 1)
    check("own domain: sight bypass on the extra hosts too", text.count(f"remote_ip {dep.SIGHT_SUBNET}") == 2 * 4)
    authelia = dep.authelia_config()
    check("own domain: a second session cookie with its own portal",
          "- domain: owner.me\n    authelia_url: https://auth.owner.me" in authelia
          and "- domain: example.test\n    authelia_url: https://auth.example.test" in authelia)
    check("own domain: one rule naming the hosts at the record's policy",
          "- domain:\n    - owner.me\n    - www.owner.me\n    policy: one_factor" in authelia)
    check("own domain: the sentinel is still the only two_factor", authelia.count("two_factor") == 1)
    dep.PUBLIC_FILE.write_text("site\n")
    text = dep.caddyfile(today)
    blocks = dict(dep.live_host_blocks(text))
    check("own domain: the operator file ungates every hostname at once",
          {blocks.get(h) for h in ("owner.me", "www.owner.me", "site.example.test")} == {False}
          and blocks.get("pages.example.test") is True and blocks.get("auth.owner.me") is True)
    dep.PUBLIC_FILE.unlink()
    check("own domain: unresolved check names hosts and portal",
          {n for n, _ in dep.unresolved_hosts(list(apps.values()))} >= {"owner.me", "www.owner.me", "auth.owner.me"}
          or not dep.box_addresses())   # offline box: getaddrinfo may still answer
    # Request routing after sign-in
    for label, toml, needle in [
        ("bypass: a path with a dot segment", '[[bypass]]\nmethod = "POST"\npath = "/api/../x"\n', "literal path"),
        ("bypass: a path with a query", '[[bypass]]\nmethod = "POST"\npath = "/api/x?a=1"\n', "literal path"),
        ("bypass: a prefix with a trailing slash", '[[bypass]]\nmethod = "POST"\npath = "/api/"\n', "literal path"),
        ("bypass: a path with a quote", '[[bypass]]\nmethod = "POST"\npath = "/a\\""\n', "literal path"),
        ("bypass: a method outside the list", '[[bypass]]\nmethod = "post"\npath = "/api/x"\n', "not one of"),
        ("bypass: a key beyond method and path", '[[bypass]]\nmethod = "POST"\npath = "/api/x"\nhost = "y"\n', "exactly method and path"),
        ("bypass: not an array of tables", 'bypass = "POST /api/x"\n', "array of tables"),
    ]:
        clear()
        record("alpha", 'exposure = "private"\n' + toml, COMPOSE_OK.format(name="alpha", port=8101))
        refuses(label, dep.records, needle)
    clear()
    record("alpha", 'exposure = "private"\n[[bypass]]\nmethod = "POST"\npath = "/api/dictate"\n',
           COMPOSE_OK.format(name="alpha", port=8101))
    record("pages", f'exposure = "private"\nstatic_root = "{root}/www"\n')
    apps = {a.name: a for a in dep.records()}
    check("bypass: parsed as (method, path)", apps["alpha"].bypass == (("POST", "/api/dictate"),)
          and apps["pages"].bypass == ())
    text = dep.caddyfile(today)
    block = text[text.index("alpha.example.test {"):text.index("pages.example.test {")]
    matcher = '@bypass0 expression `{http.request.method} == "POST" && {http.request.orig_uri} == "/api/dictate"`'
    check("bypass: one exact matcher, ahead of the gate", matcher in block
          and block.index(matcher) < block.index("import gate"))
    check("bypass: the host still reads as gated", dict(dep.live_host_blocks(text))["alpha.example.test"] is True)
    check("bypass: only on the record that asks", "@bypass" not in text[text.index("pages.example.test {"):])
    check("bypass: Authelia's configuration does not change", "bypass" not in dep.authelia_config())

    # Content origin
    for label, toml, needle, static in [
        ("content host: not a label", 'content_host = "Bad.Host"\n', "not a valid label", False),
        ("content host: on a static host", 'content_host = "pages-content"\n', "static host", True),
        ("content host: the sentinel", f'content_host = "{dep.SECOND_FACTOR_HOST}"\n', "reserved", False),
        ("content host: another app's host", 'content_host = "pages"\n', "already used", False),
    ]:
        clear()
        record("pages", f'exposure = "private"\nstatic_root = "{root}/www"\n')
        if static:
            record("pages", f'exposure = "private"\nstatic_root = "{root}/www"\n' + toml)
        else:
            record("alpha", 'exposure = "private"\n' + toml, COMPOSE_OK.format(name="alpha", port=8101))
        refuses(label, dep.records, needle)
    clear()
    record("alpha", 'exposure = "private"\ncontent_host = "alpha-content"\n',
           COMPOSE_OK.format(name="alpha", port=8101))
    record("pages", f'exposure = "private"\nstatic_root = "{root}/www"\n')
    apps = {a.name: a for a in dep.records()}
    check("content host: parsed, and not one of the app's own names",
          apps["alpha"].content_fqdn == "alpha-content.example.test"
          and "alpha-content.example.test" not in apps["alpha"].hostnames
          and apps["pages"].content_fqdn is None)
    text = dep.caddyfile(today)
    block = text[text.index("alpha-content.example.test {"):text.index("pages.example.test {")]
    check("content host: gated and logged",
          dict(dep.live_host_blocks(text))["alpha-content.example.test"] is True
          and "alpha-content.example.test" not in dep.live_hosts_unlogged(text))
    check("content host: everything but /content/* is a 404 before the login",
          "@not_content not path /content/*" in block
          and block.index('respond "not found" 404') < block.index("import gate"))
    check("content host: Host kept, Cookie and Authorization stripped, on every proxy",
          block.count("reverse_proxy 127.0.0.1:8101 {") == 2
          and block.count("header_up Host {http.request.hostport}") == 2
          and block.count("header_up -Cookie") == 2 and block.count("header_up -Authorization") == 2)
    check("content host: no cookie is set from it", "\t\t-Set-Cookie\n\t\tdefer" in block)
    (dep.PUBLIC_FILE).write_text("alpha\n")
    text = dep.caddyfile(today)
    check("content host: stays gated when the operator makes the app public",
          dict(dep.live_host_blocks(text))["alpha-content.example.test"] is True
          and dict(dep.live_host_blocks(text))["alpha.example.test"] is False)
    dep.PUBLIC_FILE.unlink()
    check("content host: Authelia's configuration does not change",
          "content" not in dep.authelia_config())

    clear()
    record("alpha", 'exposure = "private"\ncheckout = "/srv/alpha"\n', COMPOSE_OK.format(name="alpha", port=8101))
    record("beta", 'exposure = "shared"\n[share]\nexpires = 2026-10-08\n', COMPOSE_OK.format(name="beta", port=8102))
    record("gamma", 'exposure = "shared"\n[share]\nexpires = 2026-09-01\n', COMPOSE_OK.format(name="gamma", port=8103))
    record("pages", f'exposure = "private"\nstatic_root = "{root}/www"\nhide = [".git", "private/notes"]\n')

    check("generated() covers the broker wall, fifteen other live paths and the sign-in page's four", set(dep.generated(today)) == {
        dep.loam_broker.FIREWALL_SCRIPT, dep.CADDYFILE, dep.DAEMON_JSON, dep.AUTHELIA_CONFIG, dep.AUTHELIA_COMPOSE,
        dep.BROWSER_COMPOSE, dep.BROWSER_PROXY_SOCKET, dep.BROWSER_PROXY_UNIT, dep.DOCKER_IDLE_UNIT,
        dep.SIGHT_FIREWALL_SCRIPT, dep.SIGHT_FIREWALL_UNIT,
        dep.INBOUND_FIREWALL_SCRIPT, dep.INBOUND_FIREWALL_UNIT, dep.SUDOERS,
        dep.PREVIEW_SYSCTL, dep.SERVICE_UNIT,
        *(dep.LOGIN_DIR / n for n in ("index.html", "login.css", "login.js", "icon.svg"))})

    # Sign-in page routing
    real_get = dep.lc.get

    def login_as(**values):
        dep.lc.get = lambda key, default=None: (
            values.get(key.removeprefix("login."), default) if key.startswith("login.")
            else real_get(key, default))
    try:
        login_as()
        files = dep.login_files()
        html = files[dep.LOGIN_DIR / "index.html"]
        css = files[dep.LOGIN_DIR / "login.css"]
        check("login: every mark in the page is filled", "@@" not in html)
        check("login: the fonts are inside the stylesheet, nothing fetched",
              css.count("data:font/woff2;base64,") == 2 and "/*@@FONTS@@*/" not in css
              and "url(/" not in css and "http" not in css)
        check("login: the script and stylesheet carry the render's version",
              html.count("?v=") == 2 and len(set(re.findall(r"\?v=([0-9a-f]{12})", html))) == 1)
        check("login: an install's page offers no password reset; a box set up by hand keeps it",
              ('data-reset="off"' in html) == bool(dep.EDGE))
        check("login: the page makes no request off the portal",
              not re.search(r"https?://", files[dep.LOGIN_DIR / "login.js"].replace(
                  "http://www.w3.org/2000/svg", ""))
              and "fetch(path" in files[dep.LOGIN_DIR / "login.js"])
        block = dep.caddy_portal_block("auth.example.test")
        lines = [ln.strip() for ln in block.splitlines()]
        check("login (default 'loam'): the portal serves Loam's page on its own routes",
              "path / /2fa /2fa/* /authenticated /logout /settings /settings/*" in lines
              and "not query authelia=*" in lines and "rewrite * /index.html" in lines
              and "expression" not in block)
        check("login: everything else, the API above all, is still Authelia's",
              block.rstrip().endswith("handle {\n\t\treverse_proxy 127.0.0.1:9091\n\t}\n}"))
        check("login: the page is sent with its own policy, framed by nobody",
              f'Content-Security-Policy "{dep.LOGIN_CSP}"' in lines and "X-Frame-Options DENY" in lines
              and "script-src 'self'" in dep.LOGIN_CSP and "frame-ancestors 'none'" in dep.LOGIN_CSP
              and "unsafe" not in dep.LOGIN_CSP)
        check("login: the portal still reads as gated to check-drift",
              dict(dep.live_host_blocks(block)) == {"auth.example.test": True})
        login_as(page="authelia")
        check("login 'authelia' (the rollback): the portal block is what it was before",
              dep.caddy_portal_block("auth.example.test")
              == "auth.example.test {\n\timport access_log auth.example.test\n\timport compress\n"
                 "\treverse_proxy 127.0.0.1:9091\n}\n")
        check("login 'authelia': the page is still rendered, so going back is the Caddyfile alone",
              set(dep.login_files()) == set(files))
        login_as(page="staged", staged_host="next")
        staged = dep.caddy_portal_block("auth.example.test")
        check("login 'staged': Loam's page only for the staging host's sign-ins or page=loam",
              'expression `{query.rd}.startsWith("https://next.example.test/") || {query.page} == "loam"`' in staged)
        check("login 'staged': its links keep asking for Loam's page",
              'data-keep="loam"' in dep.login_files()[dep.LOGIN_DIR / "index.html"])
        for bad in ({"page": "pretty"}, {"page": "staged", "staged_host": "next.evil"}):
            login_as(**bad)
            try:
                dep.caddy_portal_block("auth.example.test")
                check(f"login: {bad} is refused", False)
            except dep.DeployError:
                check(f"login: {bad} is refused", True)
        # Caddy itself reads all three, when it is on the box (the expression
        # matcher's placeholder syntax is checked by nothing else).
        if shutil.which("caddy"):
            blocks = []
            for i, mode in enumerate(("loam", "staged", "authelia")):
                login_as(page=mode, staged_host="next")
                blocks.append(dep.caddy_portal_block(f"auth{i}.example.test")
                              .replace(f"\timport access_log auth{i}.example.test\n\timport compress\n", ""))
            cf = root / "Caddyfile.login"
            cf.write_text("{\n\tadmin off\n}\n" + "\n".join(blocks), encoding="utf-8")
            res = subprocess.run(["caddy", "adapt", "--config", str(cf), "--adapter", "caddyfile"],
                                 capture_output=True, text=True, check=False)
            check("login: Caddy adapts the portal block in all three modes", res.returncode == 0,
                  res.stderr[-600:] if res.returncode else "")
    finally:
        dep.lc.get = real_get
    check("is_loopback", dep.is_loopback("127.0.0.1:8000") and dep.is_loopback("[::1]:22")
          and not dep.is_loopback("*:443") and not dep.is_loopback("0.0.0.0:22"))
finally:
    for k, v in saved.items():
        setattr(dep, k, v)
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
