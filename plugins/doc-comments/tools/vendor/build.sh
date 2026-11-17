#!/bin/sh
# Rebuild page/vendor/codemirror.js from the pinned versions in package.json.
# Run from anywhere; it works in a temporary folder and writes only the one
# file. The page itself has no build step: this file is committed.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
out="$here/../../page/vendor/codemirror.js"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cp "$here/package.json" "$here/entry.js" "$work/"
cd "$work"
npm install --no-audit --no-fund --loglevel=error >/dev/null
npx esbuild entry.js --bundle --format=esm --minify --legal-comments=eof \
  --target=es2020 --outfile="$out"
echo "wrote $out ($(wc -c < "$out") bytes)"
