// Run after both board browser drivers to refresh the retained review artifacts.
import { readdir, readFile, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
const out = process.argv[2] || 'docs/evidence/board-lists'
const files = (await readdir(out)).filter((file) => file.endsWith('.png')).sort()
const captures = await Promise.all(files.map(async (file) => {
  const bytes = await readFile(path.join(out, file))
  return { file, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') }
}))
await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
const source = ['public/board-drag.js', 'public/thread-record.js', 'public/threads.js', 'public/threads.css', 'public/ui.js']
await writeFile(path.join(out, 'implementation.diff'), execFileSync('git', ['diff', '--no-color', '--unified=0', '96f3343', '--', ...source]))
console.log(`${files.length} captures hashed; production diff refreshed against integration base 96f3343.`)
