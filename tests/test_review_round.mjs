import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'

// Stage 12 review fixes. The plugin is loaded with small SDK and React stubs, as in the other suites.
const source = fs.readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
const mod = new vm.SourceTextModule(source)
const element = (type, props, key) => ({ type, props, key })
const atom = value => ({ get: () => value, subscribe: () => () => {} })
const host = { state: { profile: atom('fictional-profile'), connectionId: atom('local'), gateway: atom('open') } }
const stub = values => new vm.SyntheticModule(Object.keys(values), function () { for (const [k, v] of Object.entries(values)) this.setExport(k, v) })
await mod.link(name => ({
  '@hermes/plugin-sdk': stub({ ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'nav', gatewayActivationEpoch: () => 0, host, useValue: value => value?.get?.() }),
  react: stub({ useEffect() {}, useRef: v => ({ current: v }), useState: v => [v, () => {}], useSyncExternalStore: (subscribe, read) => read() }),
  'react/jsx-runtime': stub({ jsx: element, jsxs: element }),
})[name]); await mod.evaluate()
const ns = mod.namespace

test('the Holdings settings page renders the ticker editor for this connection and profile', () => {
  const pages = []
  const values = new Map()
  const ctx = {
    storage: { get: (key, fallback) => values.get(key) ?? fallback, set: (key, value) => values.set(key, value) },
    registerSettingsPage: page => { pages.push(page); return () => {} },
    registerMany: () => () => {}, setInterval: () => () => {}, rest: async () => ({}),
  }
  ns.default.register(ctx)
  assert.equal(pages.length, 1)
  const page = pages[0].render()
  const wrapper = page.type(page.props)
  assert.equal(wrapper.props.className, 'holdings-ticker-settings')
  const [style, editor] = wrapper.props.children
  assert.equal(style.type, 'style')
  assert.match(style.props.children, /var\(--ui-text-primary\)/)
  assert.doesNotMatch(style.props.children, /#[0-9a-f]{3,6}\b/i)
  assert.equal(editor.props.owner, JSON.stringify(['local', 'fictional-profile']))
})

test('token transfers sort by direction and network fees count as fees', () => {
  assert.deepEqual(ns.activityType({ kind: 'token_transfer', quantity: '-5' }), { id: 'send', label: 'Send' })
  assert.deepEqual(ns.activityType({ kind: 'token_transfer', quantity: '5' }), { id: 'receive', label: 'Receive' })
  assert.deepEqual(ns.activityType({ kind: 'network_fee', quantity: '0' }), { id: 'fee', label: 'Network fee' })
})

test('an explorer link is built only from a well-formed transaction hash', () => {
  const good = '0x' + 'b'.repeat(64)
  const id = chain => `eip155:${chain}:x`
  assert.equal(ns.activityEvidence({ id: id(1), transfer: { hash: good } }).explorer.url, 'https://etherscan.io/tx/' + good)
  for (const hash of ['../address/0x1', good + '/../x', 'javascript:alert(1)', 12])
    assert.equal(ns.activityEvidence({ id: id(1), transfer: { hash } }).explorer, null, String(hash))
})

test('leaving a tab clears request lanes that would otherwise stay loading', () => {
  const start = source.indexOf('function discardPending()')
  const body = source.slice(start, source.indexOf('\n  }\n', start))
  for (const setter of ['setZerion', 'setCostBasis', 'setBriefing']) assert.match(body, new RegExp(setter + '\\('), setter)
})

test('every market listed on the Charts page is fetched even when the ticker hides it', () => {
  const start = source.indexOf('function useMarketSnapshots(')
  const body = source.slice(start, source.indexOf('\n}\n', start))
  assert.match(body, /\[\.\.\.DEFAULT_WATCHLIST, \.\.\.watchIds\]/)
})
