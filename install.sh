#!/usr/bin/env bash
# Install Loam on a fresh Debian 12 (or Ubuntu 24.04) server, as root:
#
#   curl -fsSL https://<where the release is>/install.sh | sudo bash
#
# This file fetches the release, then hands over to its bin/loam-install,
# which does the eleven steps (docs/install.md). Run it again at any time:
# it carries on from the first step that has not finished, and does nothing
# when all of them have.
#
# Your Loam lives on the web in one of two ways; without either option the
# installer asks which:
#   --domain DOMAIN    on a domain you have, e.g. loam.example.com: you add two
#                      DNS records there, and the installer says which and
#                      checks them
#   --name NAME        on a name Loam's makers give you under their domain
#                      (NAME.<their domain>): ask them first; they add the records
#
# Other options (everything else is asked, or passed to bin/loam-install):
#   --owner "NAME"     your first name, as Loam should call you
#   --timezone ZONE    for example Europe/Berlin
#   --version TAG      a release (default: the newest)
#   --source URL       where the release is fetched from (default: Loam's repository)
#
# While Loam's repository is private, the fetch needs an install token: put it
# in the environment as LOAM_INSTALL_TOKEN (sudo LOAM_INSTALL_TOKEN=... bash).
# It is used for this fetch only and written nowhere.
#
# What this checks, and what it trusts (docs/signing.md): the release it
# fetches must be a tag signed by one of the maintainer keys written below,
# and nothing from the release runs until it verifies. This script itself is
# trusted as it reached you: it is what the address above served.
set -Eeuo pipefail

# The maintainer keys a release must be signed with: the key lines of
# keys/release-signers, copied here (tests/test_signing.py keeps the two
# equal). While there are none, a release is not checked, and this says so.
RELEASE_SIGNERS=$(cat <<'RELEASE-SIGNERS'
RELEASE-SIGNERS
)

SOURCE="https://github.com/loamhq/loam.git"
VERSION=""
# Where the install keeps its own state and log (LOAM_INSTALL_STATE and
# LOAM_INSTALL_LOG move them, for rehearsals and tests/test_signing.py).
STATE=${LOAM_INSTALL_STATE:-/var/lib/loam/install}
RELEASE=$STATE/release
LOG=${LOAM_INSTALL_LOG:-/var/log/loam-install.log}

args=("$@")
while [ $# -gt 0 ]; do
  case "$1" in
    --source) SOURCE=$2; shift 2 ;;
    --source=*) SOURCE=${1#*=}; shift ;;
    --version) VERSION=$2; shift 2 ;;
    --version=*) VERSION=${1#*=}; shift ;;
    -h|--help)
      if [ -f "$0" ]; then sed -n '2,/^set -E/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
      else echo "Usage: install.sh [--domain DOMAIN | --name NAME] [--owner \"NAME\"] [--timezone ZONE]"; fi
      exit 0 ;;
    *) shift ;;
  esac
done

say() { printf '%s\n' "$*"; printf '%s SAY %s\n' "$(date -Is)" "$*" >>"$LOG" 2>/dev/null || true; }
stop() {
  echo
  say "The install stopped before it began: $1"
  say "Running the same command again tries again. If it stops here again, send"
  say "$LOG to whoever helps you with Loam."
  exit 1
}

# verify_release DIR TAG: the tag in the clone at DIR is a tag object named
# TAG, signed (SSH) by a key in RELEASE_SIGNERS, and the clone is checked out
# at the commit that object names. Sets WHY on failure. The same check as
# lib/loam_signing.py, which cannot be used here: it is in the clone.
verify_release() {
  local dir=$1 tag=$2 tmp obj line commit principal
  WHY=""
  if ! printf '%s\n' "$RELEASE_SIGNERS" | grep -qv '^[[:space:]]*\(#.*\)\{0,1\}$'; then
    say "  This release is not checked: this installer carries no maintainer key yet."
    return 0
  fi
  obj=$(git -C "$dir" rev-parse --verify --quiet "refs/tags/$tag") || { WHY="there is no tag $tag"; return 1; }
  [ "$(git -C "$dir" cat-file -t "$obj")" = tag ] || { WHY="it is not signed"; return 1; }
  tmp=$(mktemp -d)
  git -C "$dir" cat-file tag "$obj" >"$tmp/raw"
  line=$(grep -n -m1 -x -e '-----BEGIN SSH SIGNATURE-----' "$tmp/raw" | cut -d: -f1 || true)
  if [ -z "$line" ]; then rm -rf "$tmp"; WHY="it is not signed with an SSH key"; return 1; fi
  head -n $((line - 1)) "$tmp/raw" >"$tmp/payload"
  tail -n +"$line" "$tmp/raw" >"$tmp/sig"
  if ! sed -n '1,/^$/p' "$tmp/payload" | grep -qx "tag $tag" \
     || ! sed -n '1,/^$/p' "$tmp/payload" | grep -qx "type commit"; then
    rm -rf "$tmp"; WHY="the signed tag inside names a different release"; return 1
  fi
  commit=$(sed -n 's/^object //p;/^$/q' "$tmp/payload" | head -n1)
  printf '%s\n' "$RELEASE_SIGNERS" | grep -v '^[[:space:]]*\(#.*\)\{0,1\}$' >"$tmp/allowed"
  principal=$(ssh-keygen -Y find-principals -f "$tmp/allowed" -s "$tmp/sig" 2>>"$LOG" | head -n1 || true)
  if [ -z "$principal" ]; then rm -rf "$tmp"; WHY="it is signed, but not by a Loam maintainer key"; return 1; fi
  if ! ssh-keygen -Y verify -f "$tmp/allowed" -I "$principal" -n git -s "$tmp/sig" \
       <"$tmp/payload" >>"$LOG" 2>&1; then
    rm -rf "$tmp"; WHY="its signature does not match its contents, so it was changed after it was signed"; return 1
  fi
  rm -rf "$tmp"
  git -C "$dir" checkout --quiet --detach "$commit" 2>>"$LOG" || { WHY="its commit is missing"; return 1; }
  say "  Loam $tag is signed by $principal; checked."
}

refuse() {
  echo
  say "The install stopped: Loam $VERSION from $SOURCE could not be verified."
  say "  $WHY."
  say "It may have been tampered with, so nothing from it was run and the copy that"
  say "was fetched has been removed. Do not work around this; tell Loam's makers"
  say "what happened, and send them $LOG."
  exit 3
}

if [ "$(id -u)" != 0 ]; then
  echo "The installer needs to run as root: put  sudo  in front of the command."
  exit 2
fi
mkdir -p "$STATE" && chmod 700 "$(dirname "$STATE")" "$STATE"
touch "$LOG" && chmod 600 "$LOG"
printf '%s === install.sh %s\n' "$(date -Is)" "${args[*]:-}" >>"$LOG"

# A re-run carries on with the release it started with.
if [ -z "$VERSION" ] && [ -f "$STATE/answers.json" ]; then
  VERSION=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version") or "")' \
            "$STATE/answers.json" 2>/dev/null || true)
fi

echo
say "Installing Loam. This takes a few minutes; you can watch it go."

# What the fetch itself needs. A fresh server usually has curl and Python;
# git and the certificates are made sure of here (step 3 installs the rest).
need=()
for tool in git curl python3; do command -v "$tool" >/dev/null 2>&1 || need+=("$tool"); done
command -v ssh-keygen >/dev/null 2>&1 || need+=(openssh-client)
[ -f /etc/ssl/certs/ca-certificates.crt ] || need+=(ca-certificates)
if [ ${#need[@]} -gt 0 ]; then
  say "  Getting what the installer needs first (${need[*]})…"
  DEBIAN_FRONTEND=noninteractive apt-get update -q >>"$LOG" 2>&1 || stop "the package lists could not be read (is the internet reachable?)"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${need[@]}" >>"$LOG" 2>&1 || stop "could not install ${need[*]}"
fi

# The install token, for a private repository: an HTTP header that lives in
# this process's environment only (never in a URL, a file or a git config).
gitargs=()
if [ -n "${LOAM_INSTALL_TOKEN:-}" ]; then
  basic=$(printf 'x-access-token:%s' "$LOAM_INSTALL_TOKEN" | base64 -w0)
  export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.extraHeader \
         GIT_CONFIG_VALUE_0="Authorization: Basic $basic"
fi
export GIT_TERMINAL_PROMPT=0

if [ -z "$VERSION" ]; then
  VERSION=$(git ls-remote --tags --refs "$SOURCE" 2>>"$LOG" | sed 's#.*refs/tags/##' \
            | grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -n1 || true)
  [ -n "$VERSION" ] || stop "no release of Loam could be found at $SOURCE (while the repository is private, LOAM_INSTALL_TOKEN must be set)"
fi

have=""
if [ -d "$RELEASE/.git" ]; then
  have=$(git -C "$RELEASE" describe --tags --exact-match 2>/dev/null || true)
fi
if [ "$have" != "$VERSION" ]; then
  say "  Fetching Loam $VERSION…"
  rm -rf "$RELEASE.new"
  git "${gitargs[@]}" clone --quiet --branch "$VERSION" "$SOURCE" "$RELEASE.new" >>"$LOG" 2>&1 \
    || stop "Loam $VERSION could not be fetched from $SOURCE"
  verify_release "$RELEASE.new" "$VERSION" || { rm -rf "$RELEASE.new"; refuse; }
  rm -rf "$RELEASE" && mv "$RELEASE.new" "$RELEASE"
else
  # A re-run checks the copy it kept again before running anything from it.
  verify_release "$RELEASE" "$VERSION" || { rm -rf "$RELEASE"; refuse; }
fi
git -C "$RELEASE" remote set-url origin "$SOURCE"

export LOAM_RELEASE_DIR=$RELEASE PYTHONPYCACHEPREFIX=/var/cache/loam/pycache
exec python3 "$RELEASE/bin/loam-install" "${args[@]}"
