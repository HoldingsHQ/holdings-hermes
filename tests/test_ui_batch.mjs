import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'

// UI batch for 0.1.2: Ask chat pack, setup copy, wallets, DeFi, charts and the Ask button. Fictional data only.
const source = fs.readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
const css = source.slice(source.indexOf('const css = `'), source.indexOf('`', source.indexOf('const css = `') + 13))
const module = new vm.SourceTextModule(source)
const stub = values => new vm.SyntheticModule(Object.keys(values), function () {
  for (const [name, value] of Object.entries(values)) this.setExport(name, value)
})
const element = (type, props) => ({ type, ...props })
await module.link(name => ({
  '@hermes/plugin-sdk': stub({ ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'nav', gatewayActivationEpoch: () => 0, host: {}, useValue() {} }),
  react: stub({ useEffect() {}, useRef: value => ({ current: value }), useState: value => [value, () => {}], useSyncExternalStore() {} }),
  'react/jsx-runtime': stub({ jsx: element, jsxs: element }),
})[name])
await module.evaluate()
const ns = module.namespace
const copyRules = value => {
  assert.doesNotMatch(value, /—|;|·|\bledger\b/i, value)
  for (const sentence of value.split(/(?<=[.?!])\s+/)) assert.ok(sentence.split(/\s+/).length > 3, 'no fragment sentences: ' + sentence)
}
const texts = node => node == null || typeof node === 'boolean' ? [] : typeof node !== 'object' ? [String(node)]
  : Array.isArray(node) ? node.flatMap(texts) : texts(node.children)

test('setup boxes use the requested plain copy and the demo link sits at the right edge', () => {
  assert.equal(ns.SETUP_COPY.wallet, "Paste a public address to see its balances. You'll never be asked for a private key or recovery phrase.")
  assert.ok(ns.SETUP_COPY.exchange.startsWith('Add read-only API keys from your exchange in Hermes settings. Holdings then shows those balances next to your wallets.'))
  assert.match(ns.SETUP_COPY.exchange, /open Sources once and choose Read balances/)
  assert.match(ns.SETUP_COPY.alerts, /Holdings key/)
  for (const text of Object.values(ns.SETUP_COPY)) copyRules(text)
  const tree = ns.SetupHoldings({ onDemo() {}, onWallet() {}, onKeys() {}, onAlert() {} })
  const words = texts(tree).join(' ')
  for (const text of Object.values(ns.SETUP_COPY)) assert.ok(words.includes(text))
  assert.match(css, /button\.setup-demo-link \{ align-self:flex-end; justify-self:end; margin-inline-start:auto; width:auto;/)
})

test('one address expands to network pairs, Solana stays single and the cap is twelve', () => {
  const evm = '0x' + 'ab'.repeat(20)
  const sol = '1'.repeat(31) + '2'
  assert.deepEqual(ns.DEFAULT_WALLET_CHAINS, [1, 8453])
  assert.deepEqual(ns.EVM_WALLET_NETWORKS.map(([, name]) => name), ['Ethereum', 'Base', 'Arbitrum One', 'BNB Chain', 'Robinhood Chain'])
  assert.deepEqual(ns.walletPairs([{ address: ' ' + evm + ' ', chains: [8453, 1] }, { address: '', chains: [1] }]),
    [{ address: evm, chainId: 1 }, { address: evm, chainId: 8453 }])
  assert.deepEqual(ns.walletPairs([{ address: evm, chains: [1] }, { address: sol, chains: [1, 8453, 56] }]),
    [{ address: evm, chainId: 1 }, { address: sol, chainId: 'solana' }])
  assert.equal(ns.walletKind(sol), 'solana')
  assert.equal(ns.walletKind('0x12'), 'evm')
  assert.equal(ns.MAX_WALLET_PAIRS, 12)
  const all = [1, 8453, 42161, 56, 4663]
  assert.equal(ns.walletPairs([{ address: evm, chains: all }, { address: '0x' + 'cd'.repeat(20), chains: all }, { address: '0x' + 'ef'.repeat(20), chains: [1, 8453, 56] }]).length, 13)
  assert.match(source, /if \(wallets\.length > MAX_WALLET_PAIRS\) \{\n\s+setWalletBalances\(\{ status: 'too-many' \}\)/)
  assert.match(source, /disabled: !checked && counted && pairCount >= MAX_WALLET_PAIRS/)
  assert.ok(!source.includes('pick its network'))
  copyRules(ns.PAGE_INTROS['Wallets & DeFi'])
})

test('DeFi shows the Zerion preview without a key and says positions stay out of the total', () => {
  assert.equal(ns.PAGE_INTROS.DeFi, "See money you've lent, borrowed or added to DeFi apps, read through your Zerion key.")
  let opened = false
  const card = ns.ZerionPreview({ onSteps: () => { opened = true } })
  const words = texts(card).join(' ')
  for (const part of ["What you'll see with Zerion", 'Every token', 'DeFi positions', 'NFTs', 'Profit and loss', 'value charts',
    'Free key, takes about 2 minutes.', 'not added to your total', 'Ethereum-style networks only']) assert.ok(words.includes(part), part)
  card.children.find(child => child?.children === ns.ZERION_PREVIEW.action).onClick()
  assert.ok(opened)
  for (const text of [ns.ZERION_PREVIEW.note, ns.ZERION_PREVIEW.scope, ns.ZERION_PREVIEW.total, ...ns.ZERION_PREVIEW.items]) copyRules(text)
  assert.match(source, /!demo && setupStatus\?\.zerionConfigured === false && jsx\(ZerionPreview, \{ onSteps: openKeyGuide \}\)/)
})

test('chart price is plain and its basis explains the gap to the last completed close', () => {
  const chart = { market: 'demo', name: 'Fictional market', provider: 'Fictional feed', description: 'Fictional market' }
  const data = { status: 'ready', market: 'demo', fetchedAt: '2026-01-02T01:31:00Z', closedAt: '2026-01-02T01:00:00Z', lastTrade: '680.004',
    candles: [['2026-01-01T23:00:00Z', '1', '1', '1', '679.10'], ['2026-01-02T00:00:00Z', '1', '700', '1', '680.29']] }
  const tree = ns.MarketChart({ chart, entry: { data }, refresh() {}, chartStyle: 'Line', setChartStyle() {} })
  const head = tree.children.find(child => child?.className === 'market-chart-head')
  const price = head.children.find(child => child?.className === 'market-price')
  assert.equal(price.children[0].children, '$680.00')
  assert.equal(price.children[0].type, 'strong')
  const basis = head.children.find(child => child?.className?.includes('market-price-basis')).children
  assert.match(basis, /^Last trade at .* UTC\. The chart ends with the hour that closed at /)
  copyRules(basis)
  const controls = tree.children.find(child => child?.className === 'market-chart-controls')
  assert.deepEqual(controls.children.map(group => group['aria-label']), ['Chart style', 'Chart window'])
  assert.match(css, /\.market-chart-controls \{ display:grid; gap:10px;/)
  assert.ok(texts(tree).join(' ').includes('Hour starting'))
  const close = ns.priceBasisText({ basis: 'Completed hourly close', at: '2026-01-02T01:00:00Z' }, 'x')
  assert.match(close, /^Close of the last completed hour/)
  assert.match(source, /const WATCH_LIMIT = 12/)
  assert.match(source, /railKind === 'markets' && typeof ctx\.registerSettingsPage === 'function' && button\('\+ Add market', openWatchlist/)
})

test('Ask button sits in its own band and evidence panels end above it', () => {
  assert.match(css, /\.holdings-shell \{ --ask-band:64px; \}/)
  assert.match(css, /\.grid:has\(> #holdings-chat\[hidden\]\) > \.center \{ padding-bottom:var\(--ask-band\); \}/)
  assert.match(css, /\.grid:has\(> #holdings-chat\[hidden\]\) > \.center > \.evidence \{ bottom:var\(--ask-band\); max-height:none; \}/)
  assert.match(css, /button\.ask-fab \{ position:absolute; right:14px; bottom:11px; z-index:35;/)
})

test('model labels name the provider and model that answered', () => {
  assert.equal(ns.modelLabel('openrouter', 'demo-model'), 'demo-model from openrouter')
  assert.equal(ns.modelLabel('auto', 'demo-model'), 'demo-model, picked by Hermes automatic provider routing')
  assert.equal(ns.modelLabel('', ''), 'your configured Hermes model')
})

test('the Ask pack is bounded, keeps a selected record and links transfer pairs', () => {
  const rows = Array.from({ length: 400 }, (_, index) => ({ id: 'fictional-' + index, kind: 'withdrawal', asset: 'eip155:1/slip44:60',
    quantity: '-0.1', at: '2026-01-01T00:00:00Z', source: 'Fictional exchange', status: 'ready',
    label: 'x'.repeat(500) }))
  const pack = ns.buildAskPack({ rows, record: 'fictional-3', transferItems: [{ outgoing: 'fictional-3', incoming: 'fictional-399' }],
    alerts: Array.from({ length: 90 }, (_, index) => ({ id: 'alert-' + index, title: 'Fictional alert', group: index % 2 ? 'wallet-security' : 'other' })),
    quotes: Array.from({ length: 20 }, (_, index) => ({ market: 'm' + index, name: 'Market ' + index, quote: { price: 1, at: 'x', basis: 'b' } })) })
  assert.ok(pack.activity.length <= ns.ASK_PACK_LIMITS.activity)
  assert.ok(pack.activity.some(item => item.id === 'fictional-3'))
  assert.deepEqual(pack.activity.find(item => item.id === 'fictional-3').related, ['fictional-399'])
  assert.ok(pack.security.length <= ns.ASK_PACK_LIMITS.security && pack.alerts.length <= ns.ASK_PACK_LIMITS.alerts)
  assert.equal(pack.markets.length, ns.ASK_PACK_LIMITS.markets)
  assert.ok(JSON.stringify(pack).length <= 96_000)
  for (const item of Object.values(pack).flat()) for (const value of Object.values(item))
    if (typeof value === 'string') assert.ok(value.length <= 160)
})

test('model answers need known citations and the selected record, otherwise the local answer stays', () => {
  assert.match(source, /cited\.length && cited\.every\(id => known\.has\(id\)\)\n\s+&& \(!record \|\| cited\.includes\(record\)\)\)/)
  assert.match(source, /fallback\(\)\n\s+if \(answer\?\.status === 'consent-required'\) setPrivacyPrompt\(true\)/)
  assert.match(source, /name: 'holdings-ask-model'/)
  assert.match(source, /Until then, Holdings answers on this device and sends nothing to the model\./)
})

test('Activity copy and transfer hint follow the copy rules', () => {
  copyRules(ns.TRANSFER_HINT)
  copyRules(ns.PAGE_INTROS.Activity)
  copyRules('To ask about your transactions, first load a wallet or exchange history in Sources. Open Charts for public prices or Support for setup help.')
})

test('DeFi reads the setup status itself, so the Zerion card shows when Holdings reopens on DeFi', () => {
  // 0.1.5 read it only on Portfolio and dropped the reply when the page changed, so DeFi showed no Zerion card.
  for (const tab of ['Overview', 'Wallets & DeFi', 'DeFi', 'Settings']) assert.ok(ns.SETUP_STATUS_TABS.has(tab), tab)
  assert.ok(!ns.SETUP_STATUS_TABS.has('Activity'))
  const effect = source.slice(source.indexOf('useEffect(() => () => { setupStatusRequest.current += 1 }'), source.indexOf("ctx.rest('/setup/status')") + 400)
  assert.match(effect, /if \(demo \|\| !SETUP_STATUS_TABS\.has\(tab\)\) return/)
  assert.match(effect, /if \(setupStatusRequest\.current === request\) setSetupStatus\(result\)/)
  assert.doesNotMatch(effect, /tab !== 'Overview'|active = false/)
})

test('the Ask note says plainly when Hermes has no model it can reach', () => {
  const noModel = 'Hermes has no model it can reach right now, so Holdings answered on this device. Set up a model in Hermes to get model answers.'
  assert.equal(ns.modelFallbackNotice('no-model'), noModel)
  copyRules(noModel)
  // A model that exists but timed out or failed keeps the earlier note, and so does any other fallback.
  for (const status of ['provider-error', 'busy', 'no-data', 'invalid', 'local-required', undefined])
    assert.equal(ns.modelFallbackNotice(status), 'Your Hermes model did not answer, so this answer was calculated on this device.', String(status))
  assert.match(ns.modelFallbackNotice('ungrounded'), /^Your Hermes model gave an answer Holdings could not check/)
  assert.match(source, /if \(answer\?\.status === 'consent-required'\) setPrivacyPrompt\(true\)\n\s+else setModelNotice\(modelFallbackNotice\(answer\?\.status\)\)/)
})
