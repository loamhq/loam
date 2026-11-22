# CodeMirror 6, bundled

`codemirror.js` is CodeMirror 6 and the Lezer Markdown parser, bundled once
into one ES module (minified) so the page imports it with no build step of
its own. It holds only what `tools/vendor/entry.js` names. Rebuild it with
`tools/vendor/build.sh`, which installs the pinned versions in
`tools/vendor/package.json` in a temporary folder:

| package | version |
|---|---|
| @codemirror/state | 6.7.6 |
| @codemirror/view | 6.43.15 |
| @codemirror/commands | 6.11.1 |
| @codemirror/language | 6.13.1 |
| @lezer/markdown | 1.8.1 |
| @lezer/highlight | 1.2.5 |
| @lezer/common | 1.5.5 |
| esbuild (to bundle) | 0.28.2 |

One change after bundling, made by the build script: style-mod's base theme
goes into a constructed stylesheet (`adoptedStyleSheets`) for the document
too, not a `<style>` tag, because Loam's page allows no inline styles and a
constructed stylesheet is not subject to that policy.

## Licence

CodeMirror and Lezer are MIT-licensed:

> MIT License
>
> Copyright (C) 2018-2021 by Marijn Haverbeke <marijn@haverbeke.berlin> and others
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in
> all copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
> THE SOFTWARE.
