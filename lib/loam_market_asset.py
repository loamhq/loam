"""Transport for a reviewed Market folder. The Git checkout supplies the proof."""
import hashlib
import http.client
import os
import re
import shutil
import stat
import tarfile
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

NAME = "loam-{pid}.tar"
LIMIT = 72 * 1024 * 1024


def descriptor(raw, repository, pid):
    """Only a named asset of the listed repository can be requested."""
    if not isinstance(raw, dict):
        return None
    tag, digest = raw.get("tag"), raw.get("sha256")
    if (not isinstance(tag, str) or not tag or len(tag) > 255
            or any(p in ("", ".", "..") for p in tag.split("/"))
            or not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest)):
        return None
    name = NAME.format(pid=pid)
    return {"url": f"https://github.com/{repository}/releases/download/{quote(tag, safe='')}/{name}",
            "sha256": digest}


class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme == "https" and target.netloc in (
                "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"):
            return super().redirect_request(req, fp, code, msg, headers, newurl)
        return None


def download(asset, dest):
    digest = hashlib.sha256()
    opener = urllib.request.build_opener(ReleaseRedirect)
    with opener.open(asset["url"], timeout=12) as resp, dest.open("xb") as out:
        while chunk := resp.read(128 * 1024):
            if out.tell() + len(chunk) > LIMIT:
                return False
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest() == asset["sha256"]


def unpack(archive, folder):
    """Only relative plain files and directories. Recreate Git's checkout modes."""
    folder.mkdir()
    with tarfile.open(archive, "r:") as tar:
        for index, member in enumerate(tar):
            if index >= 32768 or len(member.name) > 4096 or member.name.count("/") > 128:
                raise ValueError("archive exceeds checkout limits")
            parts = member.name.split("/")
            if any(part in ("", ".", "..", ".git") for part in parts):
                raise ValueError("invalid archive path")
            path = folder.joinpath(*parts)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            elif member.type in (tarfile.REGTYPE, tarfile.AREGTYPE) and member.sparse is None:
                path.parent.mkdir(parents=True, exist_ok=True)
                mode = 0o777 if member.mode & stat.S_IXUSR else 0o666
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), "wb") as f:
                    with tar.extractfile(member) as src:
                        shutil.copyfileobj(src, f)
            else:
                raise ValueError("unsupported archive member")


def verified(asset, work, expected, fingerprint):
    if not expected:
        return None
    archive, folder = work / "release.tar", work / "release"
    try:
        if download(asset, archive):
            unpack(archive, folder)
            if fingerprint(folder) == expected:
                return folder
    except (OSError, ValueError, EOFError, tarfile.TarError, http.client.HTTPException):
        pass
    return None


def publish(repository, tag, pid, repo, commit, work):
    """Create or resume the release for an already-pushed tag."""
    import json
    import subprocess
    import loam_github
    import loam_github_app as github
    import loam_offline
    name = NAME.format(pid=pid)
    archive = work / name
    subprocess.run(["git", "-C", str(repo / pid), "archive", "--format=tar",
                    "-o", str(archive), commit], check=True, capture_output=True)
    with archive.open("rb") as f:
        digest = "sha256:" + hashlib.file_digest(f, "sha256").hexdigest()
    auth = loam_github.ask_broker("token", {"repository": repository})["token"]
    base = f"/repos/{repository}/releases"
    status, release = github.api("GET", f"{base}/tags/{quote(tag, safe='')}", auth, ok=(200, 404))
    if status == 404:
        _, release = github.api("POST", base, auth, {"tag_name": tag, "name": tag,
                                                      "draft": True, "make_latest": "false"})
    release_id = int(release["id"])
    asset = next((a for a in release.get("assets", []) if a["name"] == name), None)
    if asset and asset.get("state") == "starter" and asset.get("size") == 0:
        # GitHub can leave an empty starter after a failed upload.
        github.api("DELETE", f"{base}/assets/{int(asset['id'])}", auth)
        asset = None
    if asset is None:
        upload = f"https://uploads.github.com/repos/{repository}/releases/{release_id}/assets?name={name}"
        loam_offline.guard_url(upload, "Publish the plugin release file")
        with archive.open("rb") as f:
            req = urllib.request.Request(upload, data=f, method="POST", headers={
                "Authorization": "Bearer " + auth, "Content-Type": "application/x-tar",
                "Content-Length": str(archive.stat().st_size)})
            # The token is scoped to this repository and the upload host.
            opener = urllib.request.build_opener(NoUploadRedirect())
            with opener.open(req, timeout=120) as resp:
                asset = json.load(resp)
    if asset.get("state") != "uploaded" or asset.get("digest") != digest:
        raise ValueError(f"{name} on the release differs from the tagged folder")
    if release["draft"]:
        github.api("PATCH", f"{base}/{release_id}", auth, {"draft": False, "make_latest": "false"})
    return asset["browser_download_url"]


class NoUploadRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None
