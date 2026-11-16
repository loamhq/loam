import test from 'node:test'
import { execFileSync } from 'node:child_process'

test('standalone claim Latin fonts have no drift from the theme assets', () => {
  execFileSync(process.execPath, ['tools/embed-claim-fonts.js', '--check'], { stdio: 'pipe' })
})
