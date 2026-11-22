"""Local, read-only projections for plugins. Never return file or message bodies."""
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import loam_agents as agents
import loam_apps as apps
import loam_config as config
import loam_files as files
import loam_plugins as plugins
import loam_starter as starter
import loam_threads as threads

GROUPS = ("agents", "projects", "files", "chats", "apps", "local-plugins", "installation", "public-work")


def count_files(root, base, path, excluded=()):
    """Count visible regular files, without following directory or file links."""
    total = 0
    for dirname, dirs, names in os.walk(path):
        directory = Path(dirname)
        parts = list(directory.relative_to(base).parts)
        def include(name):
            child = directory / name
            return child not in excluded and files.visible(root, parts, name) and not child.is_symlink()
        dirs[:] = [name for name in dirs if include(name)]
        total += sum(1 for name in names if include(name) and (directory / name).is_file())
    return total


def project_folders():
    root = config.VAULT / config.vault_folder("projects")
    if not root.is_dir() or root.is_symlink():
        return []
    return sorted(p for p in root.iterdir() if not p.is_symlink() and p.is_dir()
                  and files.visible("vault", list(root.relative_to(config.VAULT).parts), p.name))


def db():
    root = config.STATE / "activity-facts"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "index.sqlite3", timeout=30)
    conn.execute("CREATE TABLE IF NOT EXISTS app_use (url TEXT, day INTEGER, opens INTEGER, PRIMARY KEY(url,day))")
    conn.execute("CREATE TABLE IF NOT EXISTS chat (id TEXT PRIMARY KEY, stamp TEXT, title TEXT, messages INTEGER, updated TEXT)")
    return conn


def app_totals(conn):
    """Seed once and count writes from an older API that is still serving during rollout."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_opens'").fetchone():
        return
    conn.execute("BEGIN IMMEDIATE")
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_opens'").fetchone():
        conn.execute("CREATE TABLE app_opens (url TEXT PRIMARY KEY, opens INTEGER NOT NULL)")
        conn.execute("INSERT INTO app_opens SELECT url,sum(opens) FROM app_use GROUP BY url")
        for event, delta in (("INSERT", "NEW.opens"), ("UPDATE OF opens", "NEW.opens-OLD.opens")):
            conn.execute(f"""CREATE TRIGGER app_use_{event.split()[0]} AFTER {event} ON app_use
                             BEGIN INSERT INTO app_opens VALUES (NEW.url, {delta})
                             ON CONFLICT(url) DO UPDATE SET opens=opens+excluded.opens; END""")
    conn.commit()


def record_open(url):
    if not isinstance(url, str) or url not in {a["url"] for a in apps.listing()["apps"]}:
        raise ValueError("Choose an app in the catalog.")
    with closing(db()) as conn, conn:
        app_totals(conn)
        conn.execute("INSERT INTO app_opens VALUES (?, 1) ON CONFLICT(url) DO UPDATE SET opens=opens+1", (url,))
    return {"recorded": True}


def chat_rows():
    rows = []
    with closing(db()) as conn, conn:
        for tid, filename in (threads.load_state("index.json") or threads.rebuild_index(save=False)).items():
            try:
                path = threads.VAULT / threads.CONVERSATIONS / filename
                st = path.stat()
                stamp = f"{st.st_ino}:{st.st_mtime_ns}:{st.st_size}"
                cached = conn.execute("SELECT title,messages,updated FROM chat WHERE id=? AND stamp=?", (tid, stamp)).fetchone()
                if cached is None:
                    meta, body = threads.split_front(path.read_text(encoding="utf-8"))
                    messages = threads.parse_messages(body)
                    seen = [m for m in messages if m.get("kind") != "system"]
                    cached = (meta["title"], len(seen), (seen[-1]["at"] if seen else meta.get("created")) or "")
                    conn.execute("INSERT OR REPLACE INTO chat VALUES (?,?,?,?,?)", (tid, stamp, *cached))
                rows.append(dict(id=tid, name=cached[0], messages=cached[1], updated=cached[2]))
            except (FileNotFoundError, threads.ThreadError):
                continue
        conn.execute("CREATE TEMP TABLE present(id TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO present VALUES (?)", ((row["id"],) for row in rows))
        conn.execute("DELETE FROM chat WHERE id NOT IN (SELECT id FROM present)")
    return sorted(rows, key=lambda r: (r["updated"], r["id"]), reverse=True)


def collect(groups):
    out = {}
    vault = config.VAULT
    if "installation" in groups:
        marker = starter.status() or {}
        out["installation"] = {"first_weeks_at": marker.get("at") if marker.get("first_weeks") is True else None}
    if "projects" in groups or "files" in groups:
        folders = project_folders()
    if "projects" in groups:
        out["projects"] = [{"name": p.name, "path": str(p.relative_to(vault)),
                            "files": count_files("vault", vault, p)} for p in folders]
    if "files" in groups:
        excluded = {*folders, *(vault / config.vault_folder(k) for k in ("conversations", "system"))}
        from loam_thread_files import owned_paths
        excluded.update(vault / p for p in owned_paths())
        out["files"] = count_files("vault", vault, vault, excluded)
    if "agents" in groups:
        root = agents.ai_labs()
        out["agents"] = [{"id": who, "name": agents.card(who).get("display") or agents.card(who).get("name") or who.title(),
                          "archived": agents.is_archived(who), "files": count_files("spaces", root, root / who)}
                         for who in agents.every() if not (root / who).is_symlink()]
    if "chats" in groups:
        out["chats"] = chat_rows()
    if "apps" in groups:
        with closing(db()) as conn:
            app_totals(conn)
            use = dict(conn.execute("SELECT url, opens FROM app_opens"))
        out["apps"] = [{"name": a["name"], "url": a["url"], "opens": use.get(a["url"], 0)}
                       for a in apps.listing()["apps"] if a["source"] == "yours"]
    if "local-plugins" in groups:
        out["local-plugins"] = [{"id": p["id"], "name": p["name"]} for p in plugins._rows()
                                if p.get("effective") and p["source"]["kind"] == "built" and "app" not in p["kind"]]
    if "public-work" in groups:
        import loam_public_work
        out["public-work"] = loam_public_work.read()
    return out


def for_plugin(pid):
    row = next((p for p in plugins._rows() if p["id"] == pid and p.get("effective") and p.get("on")), {})
    groups = row.get("touches", {}).get("facts")
    if not groups:
        raise ValueError("An enabled plugin must declare touches.facts.")
    return collect(groups)
