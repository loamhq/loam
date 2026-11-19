// Run on the deployment with a document supplied at runtime. The default is
// offline; --synthesise explicitly starts paid TTS via the production service.
// Reports counts and checks, never document contents or credentials.
import fs from 'node:fs/promises'
import { setTimeout as sleep } from 'node:timers/promises'
import { readConfig as appConfig } from '../src/config.js'
import { readConfig as readAloudConfig } from '../../plugins/_listed/read-aloud/server/config.js'
import { createBroker } from '../src/broker.js'
import { brokerSecret } from '../src/plugin-secrets.js'
import { chunkText, chunkBlocks, createSpeech, jobTimeoutMs } from '../../plugins/_listed/read-aloud/server/speech.js'
import { documentSpeakable, documentSpeechBlocks } from '../../plugins/_listed/read-aloud/page/document-speech.js'
import { spokenWords } from '../../plugins/_listed/read-aloud/server/spoken-length.js'

const [file, time = 'full', flag] = process.argv.slice(2)
if (!file || !/^(full|\d+(?:\.\d+)?)$/.test(time) || (flag && flag !== '--synthesise') || process.argv.length > 5) {
  throw new Error('Usage: node tools/measure-speech.mjs <document.md> [full|minutes] [--synthesise]')
}
const text = await fs.readFile(file, 'utf8')
// The broker's address from the app's config; speech's own from Read aloud's.
const config = { ...appConfig(), speech: readAloudConfig().speech }
const spoken = documentSpeakable(text)
const before = chunkText(spoken, config.speech.chunkChars)
const after = chunkBlocks(documentSpeechBlocks(text), config.speech.chunkChars)
console.log(JSON.stringify({
  event: 'speech-document-comparison',
  words: spoken.match(/\S+/g)?.length ?? 0,
  spoken_words: spokenWords(spoken),
  chunk_chars: config.speech.chunkChars,
  full_chunks_before: before.length,
  full_chunks_after: after.length,
  full_chunk_lengths_before: before.map((c) => c.length),
  full_chunk_lengths_after: after.map((c) => c.length),
  concurrency_per_job: config.speech.concurrency,
}))
if (flag === '--synthesise') {
  const getKey = brokerSecret(createBroker(config), 'read-aloud')
  if (!process.env.SPEECH_DIR || !await getKey()) throw new Error('Synthesis requires a read-aloud plugin key and an explicit SPEECH_DIR.')
  const log = { info: (line) => console.log(JSON.stringify(line)), warn: (line) => console.log(JSON.stringify(line)), error: (line) => console.log(JSON.stringify(line)) }
  const speech = createSpeech(config, { log, getKey })
  try {
    const input = { source: 'document', mode: time === 'full' ? 'verbatim' : 'brief', minutes: time === 'full' ? 'full' : Number(time), text }
    const out = await speech.request(input)
    if (out.status !== 200 && out.status !== 202) throw new Error(out.body.error)
    const deadline = Date.now() + jobTimeoutMs(text.length) + 10_000
    let result = out.body
    while (result?.status === 'preparing' && Date.now() < deadline) {
      await sleep(250)
      result = await speech.lookup(out.body.id)
    }
    if (result?.status !== 'ready') throw new Error(result?.error ?? 'Synthesis did not finish in its time budget.')
    console.log(JSON.stringify({ event: 'speech-measurement-ready', id: result.id, duration_s: result.duration_s, cached: out.status === 200 }))
    // Independently listen to SPEECH_DIR/<id>.mp3 against the document or
    // the brief_text in its sidecar before treating timings as faithful.
  } finally {
    await speech.stop()
  }
}
