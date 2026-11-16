"""The user's apps, as one list: what the page's Apps section draws.

Built 2026-11-12 for the app's three sections (Chats, Files, Apps; the
design is the vault's ``40_PROJECTS/loam-ui/The app's shape — three
sections (11-12).md``). The broker serves it as ``GET /v1/apps``; the shape
is documented in broker/README.md, "Apps".

Two kinds of row, both read from the deployment layer and nothing else, so
the list is exactly what Caddy serves:

- **site**: an agent's own subdomain, ``state/sites/<name>.toml``, written by
  ``bin/site`` (lib/loam_deploy.sites). Owned by the agent that stood it up.
- **project**: a deployment record with a container, ``deploy/<app>/``, which
  follows the app-deployment conventions. Owned by the owner, marked by its
  project. A record that is a static host of the runtime's own (``files``,
  ``vault``) is not an app anyone built, and a record that says
  ``app_shell`` is Loam's page itself (loam-ui and its staging copy); neither
  is listed.

- **plugin** (2026-11-13): a plugin whose kind includes ``app`` and which is
  in force: a server plugin that also lists itself here, reached at
  ``/x/<id>/`` on the page's own host (lib/loam_plugin_servers.py). No
  second mechanism: its row is read from the plugin host's list, and its
  ``service`` says whether its server runs.

A site's and a project's ``source`` is ``yours``. A plugin's is
``installed`` when it came from a link, ``loam`` when it ships with Loam, and
``yours`` when it was built here.

Read-only: nothing here writes, renders, or asks for root beyond what
``loam_deploy.public_names`` already does for the operator's public file,
and no share token is read (whether a share link is minted is the
generator's business; the record's word is enough to say ``shared``).
"""

from __future__ import annotations

import datetime as dt

import loam_deploy as dep
import loam_config as lc

SOURCES = ("yours", "installed", "loam")
OWNER = lc.OWNER_ID


def _date(value) -> str | None:
    if isinstance(value, dt.datetime):
        value = value.date()
    return value.isoformat() if isinstance(value, dt.date) else None


def _created(rec: dict) -> str | None:
    value = rec.get("created")
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return value if isinstance(value, str) and value else None


def _access(public: bool, app: dep.App) -> str:
    if public:
        return "public"
    return "shared" if app.exposure == "shared" else "private"


def site_row(site: dep.App, framed_by: tuple[str, ...]) -> dict:
    return {
        "name": site.name,
        "url": f"https://{site.fqdn}/",
        "also_at": [],
        "source": "yours",
        "kind": "site",
        "owner": site.site_owner,
        "project": None,
        "access": "public" if site.site_public else "private",
        "expires": _date(site.site_expires),
        "shared_until": None,
        "serves": "static" if site.static_root is not None else "container",
        # Caddy sends the frame policy on a static site only
        # (loam_deploy.frame_header); a proxied one says what its
        # container says, which this cannot know without asking it.
        "frame": bool(framed_by) and site.static_root is not None,
        "created": _created(site.record),
        "icon": None,
    }


def project_row(app: dep.App, public: bool) -> dict:
    return {
        "name": app.name,
        "url": f"https://{app.fqdn}/",
        "also_at": [f"https://{h}/" for h in app.extra_hosts],
        "source": "yours",
        "kind": "project",
        "owner": OWNER,          # a project is his; its engineer builds it
        "project": app.name,
        "access": _access(public, app),
        "expires": None,
        "shared_until": _date(app.share_expires) if app.exposure == "shared" else None,
        "serves": "container",
        "frame": False,
        "created": None,
        "icon": None,
    }


def plugin_rows() -> list[dict]:
    """Every app plugin in force, as an Apps row. Its address is relative
    (``/x/<id>/``), because it is served on whichever host the page is on."""
    import loam_plugins  # noqa: PLC0415
    rows = []
    for row in loam_plugins.listing()["plugins"]:
        if not row.get("effective") or "app" not in (row.get("kind") or []):
            continue
        source = (row.get("source") or {}).get("kind")
        installed = row.get("installed") or {}
        svc = row.get("service") or {}
        rows.append({
            "name": row["name"],
            "url": f"/x/{row['id']}/",
            "also_at": [],
            "source": {"link": "installed", "loam": "loam"}.get(source, "yours"),
            "kind": "plugin",
            "owner": installed.get("installed_by") or (OWNER if source == "link"
                                                       else None),
            "project": None,
            "access": "private",
            "expires": None,
            "shared_until": None,
            "serves": "plugin",
            "frame": True,             # same origin as the page
            "created": installed.get("installed_at"),
            "icon": (f"/v1/plugins/{row['id']}/files/{row['icon']}"
                     if row.get("icon") else None),
            "plugin": row["id"],
            "on": bool(row.get("on")),
            "state": svc.get("state"),
        })
    return sorted(rows, key=lambda r: r["name"].lower())


def listing() -> dict:
    """``{"apps": [...], "sources": [...]}``: the sites by name, then the
    projects by name. Raises loam_deploy.DeployError when a record cannot
    be read, which is also a generator error the drift check names."""
    apps = dep.records()
    public = dep.public_names()
    framed_by = dep.frame_ancestors(apps)
    rows = [site_row(s, framed_by) for s in dep.sites(apps)]
    rows += [project_row(a, a.name in public) for a in apps
             if a.compose is not None and not a.app_shell]
    try:
        rows += plugin_rows()
    except Exception:  # noqa: BLE001 -- the plugin list never takes Apps down
        pass
    return {"apps": rows, "sources": list(SOURCES)}
