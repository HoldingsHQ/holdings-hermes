import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'

const source = fs.readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
const module = new vm.SourceTextModule(source)
const stub = values => new vm.SyntheticModule(Object.keys(values), function () {
  for (const [key, value] of Object.entries(values)) this.setExport(key, value)
})
await module.link(name => ({
  '@hermes/plugin-sdk': stub({ ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'nav', gatewayActivationEpoch: () => 0, host: {}, useValue() {} }),
  react: stub({ useEffect() {}, useRef() {}, useState() {}, useSyncExternalStore() {} }),
  'react/jsx-runtime': stub({ jsx() {}, jsxs() {} }),
})[name])
await module.evaluate()
const { workspaceModes, workspaceRest, sessionConflictDecision, createWatchClient } = module.namespace

test('exiting or closing the route drops demo chat, alert state, read state and queued work', () => {
  for (const close of [true, false]) {
    const modes = workspaceModes()
    const owner = 'profile with a key'
    assert.equal(modes.read(owner).demo, false)
    modes.select(owner, true)
    const first = modes.read(owner)
    first.memory.read(owner)
    first.memory.update(owner, 'thread', [{ text: 'fictional answer' }])
    first.alerts.replace(owner, 'fictional', [{ id: 'fixture', fictional: true }])
    first.alerts.mark(owner, 'fixture')
    let fire, canceled = false
    modes.queue(owner, first, fn => { fire = fn; return () => { canceled = true } },
      () => assert.fail('obsolete demo callback ran'), 8000)
    if (close) modes.end(owner, first)
    else modes.select(owner, false)
    assert.equal(canceled, true)
    fire()
    assert.equal(modes.read(owner).demo, false)
    assert.equal(modes.read(owner).alerts, undefined)
    modes.select(owner, true)
    assert.equal(modes.read(owner).memory.read(owner).thread.length, 0)
    assert.equal(modes.read(owner).alerts.read(owner).items.length, 0)
    assert.equal(modes.read('new gateway').demo, false)
    assert.equal(workspaceModes().read(owner).demo, false)
  }
})

test('real REST rejects fixtures and demo rejects live reads or changes', async () => {
  const calls = []
  const ctx = { async rest(path) { calls.push(path); return { status: 'ready' } } }
  const real = workspaceRest(ctx, false, () => true)
  const demo = workspaceRest(ctx, true, () => true)
  const fixtureCalls = [
    ['/sample'], ['/briefing/sample'], ['/wallets/security', { body: { fixture: true } }],
    ['/alerts/watch', { body: { action: 'fixture' } }],
    ['/transfers/decisions', { body: { mode: 'combined-sample', action: 'list' } }],
    ['/conflicts/decisions', { body: { mode: 'combined-sample', action: 'list' } }],
  ]
  for (const [path, options] of fixtureCalls) await assert.rejects(real(path, options))
  for (const path of ['/wallets/balances', '/briefing/local', '/binance/balances', '/setup/status', '/alerts/watch']) {
    await assert.rejects(demo(path))
  }
  assert.equal(calls.length, 0)
  await demo('/sample')
  await real('/support')
  assert.deepEqual(calls, ['/sample', '/support'])
})

test('late fixture response is discarded after exit', async () => {
  let release, current = true
  const request = workspaceRest({ rest: () => new Promise(resolve => { release = resolve }) }, true, () => current)
  const pending = request('/sample')
  current = false
  release({ rows: ['fictional'] })
  await assert.rejects(pending, /workspace changed/)
})

test('local demo conflict resolution updates only the supplied in-memory item', () => {
  const pending = { revision: 0, status: 'pending', choices: ['A', 'B'] }
  const preferred = sessionConflictDecision(pending, 'prefer', 'A')
  assert.equal(pending.status, 'pending')
  assert.equal(preferred.status, 'preferred')
  assert.equal(preferred.preferred, 'A')
  const undone = sessionConflictDecision(preferred, 'undo')
  assert.equal(undone.status, 'undone')
  assert.equal(undone.preferred, null)
  assert.equal(undone.history.length, 2)
})

test('a real watch result cannot send a fictional toast or notify from an inactive mode', async () => {
  const emitted = []
  let allowed = true, reads = 0
  const host = { state: { connectionId: { get: () => 'c' }, profile: { get: () => 'p' } },
    notify: toast => emitted.push(toast) }
  const client = createWatchClient({ rest: async () => {
    reads++
    return { status: 'ready', new: [{ id: 'fictional', fictional: true }] }
  } }, { watch() {} }, host, () => 1, () => allowed)
  await client({ action: 'poll' })
  allowed = false
  await client({ action: 'poll' })
  assert.equal(reads, 1)
  assert.deepEqual(emitted, [])
})
