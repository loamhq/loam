"""The installer's starter Market plugins, pinned by the reviewed release."""
import re
import loam_plugins as plugins

# Each tuple is (plugin id, owner/repository, plugin folder, reviewed full SHA).
# Only the fresh-install starter step consumes this list.
PINS = ()


def install(pins=PINS):
    for pid, repo, folder, commit in pins:
        if not re.fullmatch("[a-f0-9]{40}", commit):
            raise ValueError(f"{pid} needs a full reviewed commit")
        request = {"url": repo, "ref": commit, "path": folder, "via": "market"}
        preview = plugins.install(request)
        if (preview.get("id") != pid or preview.get("source", {}).get("commit") != commit
                or preview["source"]["repo"] != repo or preview["source"]["path"] != folder
                or preview.get("installable") is False):
            raise ValueError(f"The reviewed pin for {pid} does not match the package")
        if preview.get("replaces"):
            # A retry or an owner's copy. Preserve both files and switch.
            continue
        plugins.install({**request, "confirm": preview["confirm"]})
