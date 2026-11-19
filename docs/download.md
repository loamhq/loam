# Download as zip: a folder of the vault or AI Spaces as one file

*Since 2026-11-18. The host's route is `GET /v1/files/archive`
(`lib/loam_files.py`, "Archive"); the app's is `GET /api/files/archive`
(`app/src/server.js`), with its own fallback in `app/src/archive.js`. Tests:
`tests/test_broker_files.py` and `app/test/files-archive.test.js`.*

## What the owner sees

In Files, the ⋯ menu of the vault, of AI Spaces and of any folder in either
offers **Download as zip**. The row says "Preparing zip…" while the app
checks the folder is there, then the browser's own download starts: the
vault as `vault.zip`, AI Spaces as `ai-spaces.zip`, a folder by its name.
Only the signed-in owner can download one; a share link cannot.

The zip is streamed while it is built, so nothing is written to disk on the
server and a folder of any size works; the browser shows how far it has got.

## What is in it, and what is not

Everything that is the owner's, and nothing that is a credential or a copy:

- **In:** every file and folder, empty folders included, the vault's
  `.obsidian/` settings included. Text is compressed; images, audio, PDFs
  and archives are stored as they are.
- **Never in:** git's history (`.git/`: history is what the Git backup
  carries, docs/backup.md), Syncthing's `.stversions/` and `.stfolder`,
  secret-shaped names (`.env*`, `*.pem`, `*.key`, `id_rsa*` and the other
  key names, `credentials.json`, `auth.json`, `token`, `.netrc`,
  `secret(s).*`: `SECRET_NAME` and `ARCHIVE_SECRET`), and symbolic links.
- **In AI Spaces:** the agents' own folders, `commons/` and `README.md` at
  the top, and inside them not the runtime churn the agents do not keep
  (`scratch/`, `logs/`, `cache/`, `.locks/`, `node_modules/`, `venv/`,
  `.venv/`, `__pycache__/`, `*.log`, `*.pyc`).

## An older host

An app talking to a host without `/v1/files/archive` (a broker not yet
restarted onto the release that has it) builds the zip itself, from the
host's folder list and one download per file. That path cannot see
dotfiles, so it has no `.obsidian/`, and the host hands it a file of at
most 50 MiB (`DOWNLOAD_MAX`). A file over that is left out, and the zip ends
with **`Left out of this zip.txt`**, naming each such file and its size
(since 2026-11-19; before, the download stopped part way with the page
already saying "Download started"). Restarting the broker onto the new
release ends the fallback.

## This is not a backup

A zip is a copy of how things are now, taken by hand. The vault and AI
Spaces keep their own history on the server every fifteen minutes, and the
Git backup sends it off the machine: both are in docs/backup.md.
