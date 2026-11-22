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
# Loam's page allows no inline styles (`default-src 'self'`), and style-mod
# puts CodeMirror's base theme in a <style> tag for a document, using a
# constructed stylesheet only for a shadow root. A constructed stylesheet is
# not subject to the policy, so it is used for the document too: drop the
# `!root.head` test. The build fails if the minified test is not found.
node -e '
const fs = require("fs"); const f = process.argv[1]; let s = fs.readFileSync(f, "utf8")
const re = /if\(!(\w)\.head&&\1\.adoptedStyleSheets&&(\w)\.CSSStyleSheet\)/
if (!re.test(s)) { console.error("style-mod patch: test not found"); process.exit(1) }
fs.writeFileSync(f, s.replace(re, "if($1.adoptedStyleSheets&&$2.CSSStyleSheet)"))
' "$out"
echo "wrote $out ($(wc -c < "$out") bytes)"
