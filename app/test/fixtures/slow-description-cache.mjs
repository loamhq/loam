// Opt-in preload for the shared-description-budget regression. Delay cache
// reads past the old test's 50ms deadline without changing provider behavior.
// Run only with the named test; see the merge-check evidence for the command.
import fsp from 'node:fs/promises'
import { setTimeout as delay } from 'node:timers/promises'
const readFile = fsp.readFile
fsp.readFile = async function (file, ...args) {
  if (String(file).includes('/descriptions/')) await delay(75)
  return readFile.call(this, file, ...args)
}
