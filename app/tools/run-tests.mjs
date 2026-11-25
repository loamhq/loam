// Size `node --test` concurrency to the box's CPU count rather than letting
// Node's default (`availableParallelism() - 1`) saturate a small server or
// pile multiple headless Chromium runs onto the same cores.
//
// Run (from app/):
//   node tools/run-tests.mjs                 # full suite (test/*.test.js + tools/*.test.js)
//   node tools/run-tests.mjs test/foo.test.js
//
// Override with APP_TEST_CONCURRENCY=<positive integer>.
import { spawnSync } from 'node:child_process'
import { readdirSync } from 'node:fs'
import { availableParallelism } from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const APP_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

export function testConcurrency({ cpus = availableParallelism(), env = process.env } = {}) {
  const raw = env?.APP_TEST_CONCURRENCY
  if (raw !== undefined && raw !== '') {
    const parsed = Number.parseInt(String(raw), 10)
    if (Number.isFinite(parsed) && parsed >= 1) return parsed
  }
  const n = Math.max(1, Number.isFinite(cpus) ? Math.floor(cpus) : 1)
  const browser = Boolean(env?.LOAM_CHROME || env?.LOAM_PLAYWRIGHT || env?.APP_PREVIEW_CHROME)
  if (browser) return Math.max(1, Math.min(2, Math.floor(n / 4)))
  return Math.max(1, Math.min(8, Math.floor(n / 2)))
}

function listTests(dir) {
  return readdirSync(path.join(APP_ROOT, dir))
    .filter((name) => name.endsWith('.test.js'))
    .sort()
    .map((name) => path.join(dir, name))
}

function runGroup(files, { concurrency, timeoutMs = 0 }) {
  if (!files.length) return 0
  const args = ['--test', `--test-concurrency=${concurrency}`]
  if (timeoutMs > 0) args.push(`--test-timeout=${timeoutMs}`)
  args.push(...files)
  const res = spawnSync(process.execPath, args, { cwd: APP_ROOT, stdio: 'inherit' })
  if (res.error) throw res.error
  return res.status ?? 1
}

function main(argv = process.argv.slice(2)) {
  const concurrency = testConcurrency()
  if (argv.length > 0) {
    return runGroup(argv, { concurrency, timeoutMs: 20000 })
  }
  const first = runGroup(listTests('test'), { concurrency, timeoutMs: 20000 })
  if (first !== 0) return first
  return runGroup(listTests('tools'), { concurrency })
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  process.exit(main())
}
