import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
const source = fs.readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
const css = source.slice(source.indexOf('const css = `'), source.indexOf('\n`\n', source.indexOf('const css = `')))
const mod = new vm.SourceTextModule(source)
const element = (type, props, key) => ({ type, props, key })
const stub = values => new vm.SyntheticModule(Object.keys(values), function () { for (const [k, v] of Object.entries(values)) this.setExport(k, v) })
await mod.link(name => ({
  '@hermes/plugin-sdk': stub({ ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'nav', gatewayActivationEpoch: () => 0, host: {}, useValue() {} }),
  react: stub({ useEffect() {}, useRef: v => ({ current: v }), useState: v => [v, () => {}], useSyncExternalStore() {} }),
  'react/jsx-runtime': stub({ jsx: element, jsxs: element }),
})[name]); await mod.evaluate()
const ns = mod.namespace
const copyRules = value => {
  assert.doesNotMatch(value, /—|;|\bledger\b/i, value)
  for (const sentence of value.split(/(?<=[.?!])\s+/)) assert.ok(sentence.split(/\s+/).length > 3, 'no fragment sentences: ' + sentence)
}
const hash = '0x' + 'a'.repeat(64)
const rows = [
  { id: 'ethereum:eip155:1:0x' + '3'.repeat(64) + ':0x2:usdc', originalId: 'eip155:1:0x' + '3'.repeat(64) + ':0x2:usdc', source: 'Ethereum and Uniswap',
    at: '2026-01-01T00:00:00+00:00', kind: 'defi_swap', asset: 'eip155:1/erc20:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', quantity: '-1.000000', cost: '0', fee: '0', fee_asset: '',
    evidence: ['ref:swap'], observations: [{ pool: '0xpool' }] },
  { id: 'kraken-spot:kraken:sample:trades:T-SAMPLE', originalId: 'kraken:sample:trades:T-SAMPLE', source: 'Kraken Spot', at: '2026-01-01T00:00:00+00:00',
    kind: 'buy', asset: 'kraken-pair:BTC/USD', quantity: '0.02', cost: '2', fee: '0', fee_asset: '', evidence: ['csv:T'],
    observations: [{ txid: 'T-SAMPLE', ordertxid: 'O-SAMPLE', cost: '2' }] },
  { id: 'kraken-spot:fictional-transfer-1', originalId: 'fictional-transfer-1', source: 'Kraken Spot', at: '2026-01-01T09:00:00+00:00',
    kind: 'withdrawal', asset: 'eip155:1/slip44:60', quantity: '-0.500', cost: '0', fee: '0.010', fee_asset: 'eip155:1/slip44:60',
    transfer: { hash }, evidence: ['sample:fictional-transfer-1'], observations: [{ reference: 'fictional-transfer-1', hash }] },
  { id: 'ethereum:fictional-transfer-2', originalId: 'fictional-transfer-2', source: 'Ethereum and Uniswap', at: '2026-01-01T09:08:00+00:00',
    kind: 'transfer', asset: 'eip155:1/slip44:60', quantity: '0.490', cost: '0', fee: '0', fee_asset: '', evidence: ['sample:fictional-transfer-2'], observations: [] },
]
const portfolio = { positions: [{ name: 'Wallet tokens', assets: [['Ether', '16.00 ETH', 51200], ['Wrapped bitcoin', '0.50 WBTC', 34000], ['Other', '', 2400]] }] }

test('activity types map every record kind to a short icon label', () => {
  assert.deepEqual(rows.map(row => ns.activityType(row).id), ['swap', 'trade', 'send', 'receive'])
  assert.deepEqual(rows.map(row => ns.activityType(row).label), ['Swap', 'Buy', 'Withdrawal', 'Receive'])
  assert.deepEqual(ns.activityType({ kind: 'transfer', quantity: '-0.1' }), { id: 'send', label: 'Send' })
  assert.deepEqual(ns.activityType({ kind: 'fee', quantity: '-0.1' }), { id: 'fee', label: 'Fee' })
  assert.deepEqual(ns.activityType({ kind: 'staking_reward', quantity: '1' }), { id: 'other', label: 'Staking reward' })
  assert.deepEqual(ns.ACTIVITY_TYPE_CHOICES.map(([id]) => id), ['all', 'receive', 'send', 'swap', 'trade', 'fee', 'other'])
})

test('values use the recorded cost, the $1 stablecoin rule or a price already loaded, and never guess', () => {
  const prices = ns.demoActivityPrices(portfolio)
  assert.equal(prices.ETH.price, 3200)
  assert.equal(prices.WETH.price, 3200)
  assert.equal(prices.BTC.price, 68000)
  assert.equal(prices.ETH.basis, 'Fictional demo price')
  assert.deepEqual(ns.activityValue(rows[0], prices), { usd: 1, basis: 'Counted at $1' })
  assert.deepEqual(ns.activityValue(rows[1], prices), { usd: 2, basis: 'Recorded cost' })
  assert.deepEqual(ns.activityValue(rows[2], prices), { usd: 1600, basis: 'Fictional demo price' })
  assert.equal(ns.activityValue(rows[3], {}), null)
  assert.equal(ns.activityValue({ ...rows[3], quantity: '0' }, prices), null)
  const loaded = ns.loadedActivityPrices([{ rows: [{ row: { asset: 'ETH' }, price: 2000 }, { row: { asset: 'UNI' }, price: null }] }])
  assert.deepEqual(loaded, { ETH: { price: 2000, basis: 'Today’s price' } })
  assert.equal(ns.activityValueText({ usd: 2000, basis: 'Today’s price' }), '$2,000.00 at today’s price, not the price at the time')
  for (const basis of ['Recorded cost', 'Counted at $1', 'Fictional demo price', 'Today’s price']) copyRules('This record is worth ' + ns.activityValueText({ usd: 5, basis }) + '.')
})

test('evidence pulls the transaction hash, an explorer link and the exchange record IDs', () => {
  const swap = ns.activityEvidence(rows[0])
  assert.equal(swap.hash, '0x' + '3'.repeat(64))
  assert.deepEqual(swap.explorer, { name: 'Etherscan', url: 'https://etherscan.io/tx/0x' + '3'.repeat(64) })
  const buy = ns.activityEvidence(rows[1])
  assert.equal(buy.hash, null)
  assert.equal(buy.explorer, null)
  assert.deepEqual(buy.ids, [['Holdings record ID', 'kraken:sample:trades:T-SAMPLE'], ['Exchange record ID', 'T-SAMPLE'], ['Order ID', 'O-SAMPLE']])
  assert.deepEqual(buy.raw, [{ txid: 'T-SAMPLE', ordertxid: 'O-SAMPLE', cost: '2' }])
  const withdrawal = ns.activityEvidence(rows[2])
  assert.equal(withdrawal.hash, hash)
  assert.equal(withdrawal.explorer.name, 'Etherscan')
  assert.deepEqual(withdrawal.ids, [['Holdings record ID', 'fictional-transfer-1']])
  assert.equal(ns.activityEvidence({ id: 'x', originalId: 'eip155:8453:' + hash + ':0x1' }).explorer.name, 'Basescan')
})

test('filters combine search, type and account', () => {
  assert.equal(ns.activityFilterRows(rows).length, 4)
  assert.deepEqual(ns.activityFilterRows(rows, { type: 'send' }).map(row => row.originalId), ['fictional-transfer-1'])
  assert.equal(ns.activityFilterRows(rows, { account: 'Kraken Spot' }).length, 2)
  assert.equal(ns.activityFilterRows(rows, { query: 'eth', type: 'receive' }).length, 1)
  assert.equal(ns.activityFilterRows(rows, { query: 'O-SAMPLE' }).length, 1)
  assert.equal(ns.activityFilterRows(rows, { query: 'a'.repeat(20) }).length, 1)
  assert.equal(ns.activityFilterRows(rows, { query: 'nothing here' }).length, 0)
  assert.equal(ns.activityAccount({ id: 'kraken:abc' }), 'Kraken Spot')
  assert.equal(ns.activityAccount({ id: 'x' }, 'Ethereum'), 'Ethereum')
})

test('the summary reconciles the record count with the Needs review badge', () => {
  assert.equal(ns.activitySummary(9, 9, 3), 'Showing all 9 records. 3 items are waiting for your decision under Needs review.')
  assert.equal(ns.activitySummary(2, 9, 1), 'Showing 2 of 9 records. 1 item is waiting for your decision under Needs review.')
  assert.equal(ns.activitySummary(1, 1, 0), 'Showing all 1 record.')
  copyRules(ns.activitySummary(2, 9, 3))
  assert.match(source, /activitySummary\(activityRows\.length, visible\.length, combinedReview \? reviewCount : 0\)/)
  assert.ok(source.indexOf('const reviewCount = [transferReview, conflictReview]') < source.indexOf('const activityPage = activityView'))
})

test('the Activity page is a table with a sticky header, hover and narrow column rules', () => {
  for (const title of ['Date', 'Type', 'Asset', 'Amount', 'Value (USD)', 'Account', 'Status']) assert.ok(source.includes(`['${title}', 'col-`), title)
  assert.match(css, /\.activity-table th \{ position:sticky; top:12px;[^}]*background:var\(--activity-soft\);[^}]*border-bottom:1px solid var\(--activity-rule\); box-shadow:0 -12px 0 var\(--activity-paper\); \}/)
  assert.match(css, /\.activity-table-wrap \{ margin-top:4px; \}/)
  assert.doesNotMatch(css, /\.activity-table-wrap \{[^}]*border:1px/, 'the table sits in the page card with no inner box')
  assert.match(css, /\.activity-table tbody tr:hover td \{/)
  assert.match(css, /@container \(max-width:860px\) \{\n  \.holdings-shell \.activity-table :is\(th,td\)\.col-account \{ display:none; \}/)
  assert.match(css, /@container \(max-width:520px\) \{\n  \.holdings-shell \.activity-table :is\(th,td\)\.col-value \{ display:none; \}/)
  assert.deepEqual(ns.activityDateParts('2026-01-01T09:08:00Z'), ['Jan 01 2026', '09:08'])
  const badge = ns.activityTypeBadge({ id: 'send', label: 'Send' })
  assert.equal(badge.props.className, 'activity-type')
  assert.equal(badge.props['data-type'], 'send')
})

test('empty and no-match states point to the next step', () => {
  copyRules('Your transactions show up here once you add a wallet or connect an exchange. After that, open Sources and choose which history to load.')
  assert.ok(source.includes("text: 'Your transactions show up here once you add a wallet or connect an exchange. After that, open Sources and choose which history to load.'"))
  assert.ok(source.includes("action: 'Add a wallet', onAction: () => navigate('Wallets & DeFi'), howTo: 'Connect an exchange', onHowTo: () => navigate('Sources')"))
  assert.ok(source.includes("'No records match these filters. Clear them to see every record again.'"))
  copyRules('No records match these filters. Clear them to see every record again.')
  copyRules('Times are in UTC. Select a row to see the exact amount, its source record and any transaction hash.')
  copyRules('This hash is fictional, so it has no explorer page.')
})

test('the transfer hint sits in the drawer next to the transfer decision, and the intro is short', () => {
  copyRules(ns.TRANSFER_HINT)
  copyRules(ns.PAGE_INTROS.Activity)
  assert.equal(ns.PAGE_INTROS.Activity, 'See all your transactions from connected wallets and exchanges together.')
  const actions = source.indexOf("button('Decide in Needs review'")
  const hint = source.indexOf("className: 'activity-transfer-hint', children: TRANSFER_HINT")
  assert.ok(actions > 0 && hint > actions && hint - actions < 400)
})

test('the drawer is a focus-trapped dialog that closes by X, Esc or outside click', () => {
  assert.match(source, /role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'holdings-evidence', onKeyDown: trapDrawerFocus/)
  assert.match(source, /className: 'activity-scrim', 'aria-hidden': 'true', onClick: closeSource/)
  assert.match(source, /button\('×', closeSource, \{ className: 'activity-drawer-close', 'aria-label': 'Close details' \}\)/)
  assert.match(source, /if \(event\.key === 'Escape'\) \{ event\.preventDefault\(\); event\.stopPropagation\(\); closeSource\(\); return \}/)
  assert.match(source, /source && !activityRecord && jsxs\('aside'/)
  assert.match(source, /target: '_blank', rel: 'noreferrer noopener'/)
  assert.match(css, /\.holdings-shell \.activity-drawer-layer \{ position:absolute; inset:0; z-index:60; \}/)
  assert.match(css, /\.holdings\.holdings-shell \.activity-drawer \{ width:100%; border-left:0; \}/)
})
