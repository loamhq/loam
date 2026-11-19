// The audio plugins' own servers, started for a test on a loopback port of
// their own: Dictation's (`/transcribe`, `/terms`) and Read aloud's
// (`/status`, `/speech…`), as Loam's plugin gateway reaches them with the
// `/x/<id>` prefix taken off.

import { once } from 'node:events'
import { createDictationServer } from '../../plugins/_listed/dictation/server/app.js'
import { createTranscriber } from '../../plugins/_listed/dictation/server/transcribe.js'
import { createReadAloudServer } from '../../plugins/_listed/read-aloud/server/app.js'
import { dictationConfig } from './key-fixture.js'
import { logInto } from './harness.js'

export async function dictationApp(env = {}, { transcriber, lines = [] } = {}) {
  const config = dictationConfig(env)
  const log = logInto(lines)
  const { server } = createDictationServer({ config, transcriber: transcriber ?? createTranscriber(config), log })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  return { server, config, lines, url: `http://127.0.0.1:${server.address().port}` }
}

export async function readAloudApp({ config, speech, log, agentVoice } = {}) {
  const { server } = createReadAloudServer({ config, speech, log, agentVoice })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  return { server, url: `http://127.0.0.1:${server.address().port}` }
}
