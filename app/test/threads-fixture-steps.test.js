import test from 'node:test'
import assert from 'node:assert/strict'
import { createThreadFixture } from '../public/threads-fixture.js'

// The design preview (`?threads=fixture`) has no full steps for its sample
// replies. It must say so the way a runtime does (404), so the page shows each
// reply's summary instead of "Steps couldn't be loaded" under every reply.
test('the thread fixture answers a reply\'s steps with a 404', async () => {
  const { api } = createThreadFixture()
  await assert.rejects(api('GET', '/threads/small-details/steps?message=m3'), (e) => e.status === 404)
  const record = await api('GET', '/threads/small-details')
  assert.equal(record.thread.id, 'small-details')
})
