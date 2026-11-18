// The Market's own search, for an agent (2026-11-18): bin/loam-market search
// hands this the list as the page reads it and the words, and gets the
// listings' ids back in the page's order. One matching rule: this runs
// public/market.js's `search`, the function the Market's field runs, so an
// agent and a person asking the same thing see the same answer in the same
// order (test/market-agent-search.test.js holds it).
//
//   echo '{"entries": [...], "q": "sync with my phone", "kind": "all"}' | node tools/market-search.mjs
//   → {"ids": ["pocket-sync", ...]}
import { search, KINDS } from '../public/market.js'

let raw = ''
for await (const chunk of process.stdin) raw += chunk
const { entries = [], q = '', kind = 'all' } = JSON.parse(raw || '{}')
const k = KINDS.includes(kind) ? kind : 'all'
process.stdout.write(JSON.stringify({ ids: search(entries, String(q ?? ''), k).map(e => e.id) }) + '\n')
