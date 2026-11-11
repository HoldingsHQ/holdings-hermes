import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'

const module = new vm.SourceTextModule(fs.readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8'))
const stub = values => new vm.SyntheticModule(Object.keys(values), function () {
  for (const [key, value] of Object.entries(values)) this.setExport(key, value)
})
let hookValue
const render = (type, props) => ({ type, ...props })
await module.link(name => ({
  '@hermes/plugin-sdk': stub({ ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'nav',
    gatewayActivationEpoch: () => 0, host: {}, useValue() {} }),
  react: stub({ useEffect() {}, useRef() {}, useState(initial) { return [hookValue ?? initial, update => { hookValue = update(hookValue ?? initial) }] }, useSyncExternalStore() {} }),
  'react/jsx-runtime': stub({ jsx: render, jsxs: render }),
})[name])
await module.evaluate()
const { accountantCsv, activityAmount, checkFictionalFile, combinedDraft, combinedManifest, combineReviews, csvCell, hasKrakenChart, hasPoolChart, ledgerCsv, ledgerRowState, marketQuote, packetReview, reviewAnswer, saveAccountantExport, summarize, workspaceMemory, workspaceRequests, default: plugin } = module.namespace
const sample = JSON.parse(fs.readFileSync(new URL('../dashboard/sample.json', import.meta.url), 'utf8'))

test('alert engine scopes findings and avoids duplicate notices', () => {
  const alerts = module.namespace.alertEngine()
  const finding = { id: 'fictional-finding', title: 'Unlimited approval', severity: 'warning' }
  assert.equal(alerts.replace('a', 'wallet-security', [finding]).length, 1)
  assert.equal(alerts.replace('a', 'wallet-security', [finding]).length, 0)
  assert.equal(alerts.replace('a', 'wallet-security', [{ ...finding, severity: 'high' }]).length, 1)
  assert.equal(alerts.read('b').items.length, 0)
  alerts.open('a', true)
  assert.equal(alerts.read('a').open, true)
  alerts.open('b', true)
  assert.equal(alerts.read('a').items.length, 0)
  assert.equal(alerts.read('b').items.length, 0)
  assert.equal(alerts.read('b').open, true)
})

test('incident exposure requires both protocol identity and a matching network', () => {
  const { incidentFindings } = module.namespace
  const incident = { id: 'fixture-report', name: 'Aave V3', chainIds: [1] }
  const position = { protocol: 'aave-v3', chainId: 1, evidence: 'fixture-position' }
  const [finding] = incidentFindings([incident], [position, { ...position, chainId: 8453 }, { ...position, protocol: 'Aave v2' }], true)
  assert.equal(finding.severity, 'high')
  assert.equal(finding.fictional, true)
  assert.deepEqual(Array.from(finding.matches, row => row.evidence), ['fixture-position'])
  assert.equal(incidentFindings([{ ...incident, chainIds: [] }], [position])[0].severity, 'info')
})

test('Spot balances warn about extra permissions without replacing the successful state', () => {
  const { spotBalanceMessage } = module.namespace
  const ready = spotBalanceMessage('binance', { status: 'ready', permissionStatus: 'broader' })
  assert.equal(ready.status, undefined)
  assert.match(ready.warning, /Turn off trading, withdrawals/)
  assert.match(spotBalanceMessage('binance', { status: 'ready', permissionStatus: 'unverified' }).warning, /could not verify/)
  assert.match(spotBalanceMessage('binance', { status: 'missing-key' }).status, /unavailable.*settings in Hermes/)
  assert.match(spotBalanceMessage('binance', { status: 'protected-host-unavailable' }).status, /in-process profile/)
  assert.match(spotBalanceMessage('kraken', { status: 'read-disabled' }).status, /Query Funds/)
  assert.match(spotBalanceMessage('kraken', { status: 'ready', permissionStatus: 'broader' }).warning, /in Kraken/)
  assert.match(spotBalanceMessage('bybit', { status: 'unsupported-account' }).status, /Unified Trading Account/)
  assert.match(spotBalanceMessage('bybit', { status: 'ready', permissionStatus: 'broader' }).warning, /in Bybit.com/)
})

test('combined coverage explains why a saved review is unavailable in plugin-host mode', () => {
  const result = combineReviews([{ mode: 'kraken-spot', status: 'protected-host-unavailable' }], '2026-01-01', '2026-01-01')
  assert.equal(result.rows.length, 0)
  assert.equal(result.coverage[0].historyStatus, 'unavailable')
  assert.match(result.coverage[0].state, /unavailable in Hermes plugin-host mode.*in-process Hermes profile/)
})

test('Overview attention lists only actionable scoped reviews with stable identity', () => {
  const { attentionItems } = module.namespace
  const undone = { id: 'previous-match', status: 'undone' }
  const pending = { id: 'source-conflict', status: 'pending' }
  const items = attentionItems(
    { status: 'ready', items: [undone, { id: 'confirmed', status: 'accepted' }, { id: 'rejected', status: 'rejected' }] },
    { status: 'ready', items: [{ id: 'resolved', status: 'preferred' }, pending] },
  )
  assert.deepEqual(Array.from(items, row => [row.kind, row.title, row.item.id]), [
    ['transfer', 'Possible transfer', undone.id],
    ['conflict', 'Conflicting records', pending.id],
  ])
  assert.equal(attentionItems({ status: 'unavailable', items: [undone] }, null).length, 0)
})

test('wallet provenance labels distinguish Etherscan and the two PublicNode block scopes', () => {
  const { walletSource } = module.namespace
  assert.match(walletSource({ source: 'etherscan' }), /Latest reported balances/)
  assert.equal(walletSource({ source: 'publicnode', blockKind: 'safe', block: '5' }), 'PublicNode \/ Safe block 5')
  assert.equal(walletSource({ source: 'publicnode', fallback: true, blockKind: 'latest', block: '8' }), 'PublicNode fallback \/ Pinned latest block 8')
})

test('market status distinguishes initial loading, refresh and failure with cached data', () => {
  const { marketLoadMessage } = module.namespace
  const data = { candles: [] }
  assert.equal(marketLoadMessage('Kraken', undefined), 'Loading Kraken market data…')
  assert.equal(marketLoadMessage('Kraken', { loading: true, data }), 'Refreshing Kraken market data…')
  assert.match(marketLoadMessage('Kraken', { failed: true }), /did not return usable.*Try again/)
  assert.match(marketLoadMessage('Kraken', { failed: true, data }), /refresh failed.*last available data/)
  assert.equal(marketLoadMessage('Kraken', { data }), '')
})

test('Ethereum chat cites exact scoped swap legs and reports missing holdings', () => {
  const { ethereumRecordAnswer } = module.namespace
  const answer = ethereumRecordAnswer('What did the Ethereum wallet swap?', sample.rows)
  assert.match(answer.text, /1 USDC out.*0\.01 WETH in/)
  assert.deepEqual(Array.from(answer.rows, row => row.id), sample.rows.slice(0, 2).map(row => row.id))
  assert.match(ethereumRecordAnswer('What did it swap?', [], { ethereumScope: true }).text, /No supported/)
  assert.match(ethereumRecordAnswer('What is my ETH balance?', sample.rows).text, /unavailable/)
  assert.equal(ethereumRecordAnswer('What fee did Kraken charge?', sample.rows), null)
})

test('market exposure separates a public close, fictional holdings and transfer evidence', () => {
  const { marketExposureAnswer } = module.namespace
  const chart = { market: 'eip155:1:0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640', ticker: 'WETH/USD' }
  const quote = { price: 2000, change: -2, at: '2026-11-09T05:00:00Z', basis: 'Completed hourly close' }
  const rows = sample.transfer_demo.slice(0, 2).map(row => ({ ...row, source: row.sourceMode }))
  const item = { id: 'possible', outgoing: rows[0].id, incoming: rows[1].id, status: 'pending', reasons: [] }
  const answer = marketExposureAnswer('What moved and what is my exposure?', chart, quote, { rows }, item, sample.portfolio, { demo: true })
  assert.match(answer.text, /fictional portfolio snapshot.*\$176,250\.00 in total.*ETH \$76,800\.00 \(44%\).*made-up demo values.*Possible transfer/)
  assert.doesNotMatch(answer.text, /public quote|2,000|−2\.00%/)
  assert.deepEqual(Array.from(answer.sections, section => section.label), ['Fictional portfolio', 'Fictional transfer evidence'])
  assert.equal(answer.sections.map(section => section.text).join(' '), answer.text)
  assert.equal(answer.marketQuote, null)
  assert.equal(answer.transferId, item.id)
  assert.deepEqual(Array.from(answer.rows, row => row.id), [...rows.map(row => row.id), 'sample-wallet-exposure'])
  const btc = marketExposureAnswer('What is my exposure?', { market: 'kraken:XBTUSD', ticker: 'BTC/USD' }, quote, null, null, sample.portfolio, { demo: true })
  assert.match(btc.text, /^The fictional portfolio snapshot/)
  const scoped = marketExposureAnswer('What is my exposure?', chart, quote, null, null, null, { demo: true })
  assert.match(scoped.text, /fictional portfolio allocation is not available.*Clear the account and date filters/)
  assert.doesNotMatch(scoped.text, /public quote/)
  const live = marketExposureAnswer('What is my exposure?', chart, quote, null, null, null)
  assert.match(live.text, /public quote.*−2\.00%.*exposure.*unavailable/)
  assert.deepEqual(Array.from(live.sections, section => section.label), ['Public market', 'Holdings coverage'])
  assert.equal(live.marketQuote, chart.market)
  const missing = marketExposureAnswer('What is my exposure?', chart, null, { rows }, null, false)
  assert.match(missing.text, /quote is unavailable.*exposure.*unavailable/)
  assert.deepEqual(Array.from(missing.sections, section => section.label), ['Public market', 'Holdings coverage'])
  assert.equal(missing.rows.length, 0)
})

test('fictional transfer conversation follows exact records and the saved decision', () => {
  const { transferConversationItem, transferConversationAnswer } = module.namespace
  const rows = sample.transfer_demo.map(row => ({ ...row, source: row.sourceMode === 'kraken-spot' ? 'Kraken Spot' : 'Ethereum wallet' }))
  const review = { mode: 'combined-sample', rows }
  const item = { id: 'matched', outgoing: rows[0].id, incoming: rows[1].id, confidence: 'Hash match',
    status: 'pending', reasons: ['Matching transaction hash.'] }
  assert.equal(transferConversationItem(review, [item], 'Where did my ETH go?'), item)
  assert.equal(transferConversationItem(review, [item], 'Where did my 0.500 ETH go?'), item)
  assert.equal(transferConversationItem(review, [item], 'Where did my 1 ETH go?'), null)
  assert.equal(transferConversationItem(review, [item], 'Where did my BTC go?', item.id), null)
  assert.equal(transferConversationItem(review, [item], 'What fees did I pay?', item.id), null)
  assert.equal(transferConversationItem({ ...review, mode: 'combined' }, [item], 'Where did my ETH go?'), null)
  const before = transferConversationAnswer(review, item, 'movement')
  assert.match(before.text, /^Possible transfer\..*0\.5 ETH.*0\.49 ETH.*0\.01 ETH/)
  assert.deepEqual(Array.from(before.rows, row => row.id), [item.outgoing, item.incoming])
  const confirmed = { ...item, status: 'accepted' }
  assert.equal(transferConversationItem(review, [confirmed], 'Did I lose half an ETH?', item.id), confirmed)
  assert.match(transferConversationAnswer(review, confirmed, 'fee').text, /^Confirmed transfer between your accounts\. Recorded fee: 0\.01 ETH/)
  assert.match(transferConversationAnswer(review, { ...item, status: 'undone' }).text, /^Possible transfer/)
  assert.match(transferConversationAnswer(review, { ...item, status: 'rejected' }).text, /^You rejected/)
})

test('the ETH starter stays on the selected record and never answers about another one', () => {
  const { recordStarterPrompt, recordMovementAnswer, DEMO_STARTER_QUESTIONS } = module.namespace
  const send = { ...sample.rows.find(row => row.id === 'sample:ethereum:eth-transfer-1'), source: 'Ethereum and Uniswap' }
  const rows = [send, ...sample.transfer_demo.map(row => ({ ...row, source: row.sourceMode === 'kraken-spot' ? 'Kraken Spot' : 'Ethereum wallet' }))]
  const review = { mode: 'combined-sample', rows }
  const item = { id: 'matched', outgoing: 'fictional-transfer-1', incoming: 'fictional-transfer-2', confidence: 'Hash match',
    status: 'pending', reasons: ['Matching transaction hash.'] }
  const [eth, exposure, fees] = DEMO_STARTER_QUESTIONS
  assert.equal(recordStarterPrompt(eth, null), eth)
  assert.equal(recordStarterPrompt(eth, send), 'Where did this 0.1 ETH go?')
  assert.equal(recordStarterPrompt(eth, rows.find(row => row.id === 'fictional-transfer-2')), 'Where did this 0.49 ETH come from?')
  assert.equal(recordStarterPrompt(exposure, send), exposure)
  assert.equal(recordStarterPrompt(fees, send), fees)
  const unmatched = recordMovementAnswer(review, [item], send)
  assert.equal(unmatched.recordId, send.id)
  assert.deepEqual(Array.from(unmatched.rows, row => row.id), [send.id])
  assert.equal(unmatched.transferId, undefined)
  assert.match(unmatched.text, /^Ethereum and Uniswap recorded 0\.1 ETH going out on 2026-01-01 08:30 UTC\./)
  assert.match(unmatched.text, /no matching receive/)
  assert.doesNotMatch(unmatched.text, /0\.5 ETH|0\.49 ETH|withdrawal/)
  assert.doesNotMatch(unmatched.text, /[\u2014;]/)
  for (const id of ['fictional-transfer-1', 'fictional-transfer-2']) {
    const record = rows.find(row => row.id === id)
    const matched = recordMovementAnswer(review, [item], record)
    assert.equal(matched.recordId, id)
    assert.equal(matched.transferId, item.id)
    assert.ok(Array.from(matched.rows, row => row.id).includes(id))
  }
  const other = rows.find(row => row.id === 'fictional-transfer-3')
  const alone = recordMovementAnswer(review, [item], other)
  assert.deepEqual(Array.from(alone.rows, row => row.id), [other.id])
  assert.doesNotMatch(alone.text, /0\.5 ETH|0\.49 ETH/)
})

test('registers a single Holdings route, sidebar row and global alert chip', () => {
  plugin.register({ registerMany(items) {
    assert.deepEqual(Array.from(items, item => item.data?.path), ['/holdings', '/holdings', undefined])
    assert.deepEqual(Array.from(items, item => item.area), ['routes', 'nav', 'statusBar.right'])
  } })
})

test('sample answers use ledger fee and two attributed swap legs', () => {
  const fees = summarize(sample, 'fees')
  assert.equal(fees.rows.length, 1)
  assert.match(fees.text, /0\.0002 BTC/)
  assert.equal(summarize(sample, 'swap').rows.length, 2)
  assert.match(summarize(sample, 'coverage').text, /Fictional coverage only/)
})

test('history chart requires the exact Kraken Spot BTC/USD trade identity', () => {
  const [row] = sample.rows.filter(hasKrakenChart)
  assert.equal(sample.rows.filter(hasKrakenChart).length, 1)
  assert.equal(hasKrakenChart({ ...row, sourceMode: 'kraken-spot', originalId: row.id, id: 'combined' }), true)
  for (const change of [{ asset: 'kraken-pair:BTC/EUR' }, { kind: 'trade_movement' },
    { id: 'binance:buy' }, { sourceMode: 'binance-spot' }]) {
    assert.equal(hasKrakenChart({ ...row, ...change }), false)
  }
})

test('pool chart requires mainnet swap evidence for the exact verified pool', () => {
  const [row] = sample.rows.filter(hasPoolChart)
  assert.equal(sample.rows.filter(hasPoolChart).length, 2)
  assert.equal(hasPoolChart({ ...row, sourceMode: 'ethereum', originalId: row.id, id: 'combined' }), true)
  for (const change of [{ asset: row.asset.replace('155:1/', '155:10/') }, { asset: 'WETH' },
    { id: row.id.replace('155:1:', '155:10:') }, { sourceMode: 'bybit-spot' }, { kind: 'transfer' },
    { evidence: [] }, { evidence: row.evidence.map(ref => ref.replace(/0x88e6[a-f0-9]+/, '0x' + 'aa'.repeat(20))) }]) {
    assert.equal(hasPoolChart({ ...row, ...change }), false)
  }
})

test('shared quote distinguishes last trade, candle close, wrong market and stale data', () => {
  const chart = { market: 'XXBTZUSD' }
  const data = { status: 'ready', market: chart.market, fetchedAt: '2026-01-02T02:00:00Z',
    closedAt: '2026-01-02T01:00:00Z', lastTrade: '135', candles: [
      ['2026-01-01T00:00:00Z', '100', '100', '100', '100'],
      ['2026-01-02T00:00:00Z', '100', '125', '100', '125'],
    ] }
  const now = Date.parse(data.fetchedAt)
  assert.equal(marketQuote(chart, { data }, now).price, 135)
  assert.equal(marketQuote(chart, { data }, now).change, 25)
  assert.equal(marketQuote(chart, { data }, now).at, '2026-01-02T02:00:00.000Z')
  assert.equal(marketQuote(chart, { data }, now + 120_000).stale, true)
  assert.equal(marketQuote(chart, { data, failed: true }, now).stale, true)
  const close = marketQuote(chart, { data: { ...data, lastTrade: null } }, now)
  assert.equal(close.price, 125)
  assert.equal(close.at, '2026-01-02T01:00:00.000Z')
  assert.equal(close.basis, 'Completed hourly close')
  assert.equal(marketQuote({ market: 'wrong' }, { data }, now), null)
  assert.equal(marketQuote(chart, { data: { ...data, lastTrade: 'NaN' } }, now), null)
})

test('Activity displays exact native movement without inventing a trade leg', () => {
  const native = { asset: 'eip155:1/slip44:60', kind: 'withdrawal', quantity: '-0.123456789012345678' }
  assert.deepEqual({ ...activityAmount(native) }, { outgoing: true, text: '0.123456789012345678 ETH' })
  const sell = { asset: 'kraken-pair:BTC/USD', kind: 'sell', quantity: '0.01' }
  assert.deepEqual({ ...activityAmount(sell) }, { outgoing: true, text: '0.01 BTC' })
  assert.deepEqual({ ...activityAmount({ ...sell, asset: 'bybit-pair:BTCUSDT' }) }, { outgoing: true, text: '0.01' })
  assert.equal(activityAmount({ ...native, quantity: '0.000' }), null)
})

test('ledger review states preserve unresolved and accepted transfer meaning', () => {
  const row = { id: 'sample', kind: 'withdrawal' }
  assert.equal(ledgerRowState(row, new Set(['sample']), new Map([['sample', 'accepted']]))[0], 'Needs review')
  assert.equal(ledgerRowState(row, new Set(), new Map([['sample', 'accepted']]))[0], 'Transfer confirmed')
  assert.equal(ledgerRowState(row, new Set(), new Map([['sample', 'pending']]))[0], 'Possible transfer')
  assert.equal(ledgerRowState(row, new Set(), new Map([['sample', 'undone']]))[0], 'Possible transfer')
  assert.equal(ledgerRowState(row, new Set(), new Map([['sample', 'rejected']]))[0], 'Recorded')
})

test('cancelled, replaced and old-gateway responses cannot update a view', async () => {
  let epoch = 1
  const requests = workspaceRequests(() => epoch)
  const visible = []
  const deferred = []
  function request(label, lane = 'review') {
    const current = requests.begin(lane)
    return new Promise(resolve => deferred.push(resolve)).then(() => {
      if (current()) visible.push(label)
    })
  }
  const cancelled = request('cancelled')
  requests.clear()
  const replaced = request('replaced')
  const current = request('current')
  const other = request('other', 'file')
  deferred.splice(0).reverse().forEach(resolve => resolve())
  await Promise.all([cancelled, replaced, current, other])
  assert.deepEqual(visible, ['other', 'current'])
  const old = request('old gateway')
  epoch += 1
  deferred.pop()()
  await old
  assert.deepEqual(visible, ['other', 'current'])
})

test('export keeps signed numbers and neutralizes spreadsheet formulas', () => {
  assert.equal(csvCell('-0.01'), '"-0.01"')
  for (const value of ['=2+2', ' +SUM(A1)', '@x', '-HYPERLINK("example")', '\t=2+2']) {
    assert.ok(csvCell(value).startsWith("\"'"))
  }
  assert.match(ledgerCsv(sample.rows), /quantity,cost,fee,fee_asset,evidence/)
})

test('fictional import proof reads only the known basename under an explicit grant', async () => {
  const fixture = fs.readFileSync(new URL('../fixtures/holdings-fictional-trades.csv', import.meta.url), 'utf8')
  const calls = []
  const files = {
    folders: async () => [],
    requestFolder: async () => ({ ok:true, folder:{id:'fictional-folder'} }),
    stat: async (...args) => { calls.push(['stat', ...args]); return {ok:true, exists:true, size:Buffer.byteLength(fixture)} },
    readText: async (...args) => { calls.push(['read', ...args]); return {ok:true, text:fixture} },
    release: async id => calls.push(['release', id]),
  }
  assert.match(await checkFictionalFile(files, sample.sample_import, () => true), /CSV verified/)
  assert.deepEqual(calls, [
    ['stat', 'fictional-folder', 'holdings-fictional-trades.csv'],
    ['read', 'fictional-folder', 'holdings-fictional-trades.csv'], ['release', 'fictional-folder'],
  ])
  files.readText = async () => ({ ok:true, text:'X' + fixture.slice(1) })
  assert.match(await checkFictionalFile(files, sample.sample_import, () => true), /does not match/)
  calls.length = 0
  files.folders = async () => [{ id:'fictional-folder' }]
  assert.equal(await checkFictionalFile(files, sample.sample_import, () => false), '')
  assert.deepEqual(calls, [])
})

test('fictional file check explains cancellation, limits and revoked grants', async () => {
  const files = { folders: async () => [], release: async () => true,
    requestFolder: async () => ({ok:true, folder:{id:'scratch'}}),
    stat: async () => ({ok:true, exists:true, size:4*1024*1024+1}),
    readText: async () => { throw new Error('Read must not happen') } }
  assert.match(await checkFictionalFile(files, sample.sample_import, () => true), /4 MiB/)
  files.stat = async () => ({ok:false, error:'not-granted'})
  assert.match(await checkFictionalFile(files, sample.sample_import, () => true), /revoked/)
  files.requestFolder = async () => ({ok:true, folder:null})
  assert.equal(await checkFictionalFile(files, sample.sample_import, () => true), 'No folder selected.')
})

test('writes only the scoped fictional export with exclusive names and reports partial saves', async () => {
  const calls = []
  const files = { folders: async () => [], release: async id => calls.push(['release', id]),
    requestFolder: async () => ({ok:true, folder:{id:'scratch'}}),
    writeText: async (...args) => { calls.push(args); return {ok:calls.length === 1} } }
  const rows = sample.rows.slice(0, 1)
  assert.match(await saveAccountantExport(files, rows, '{"mode":"fictional"}', () => true), /CSV was saved/)
  assert.equal(calls[0][0], 'scratch')
  assert.equal(calls[0][1], 'holdings-fictional-records.csv')
  assert.equal(calls[0][2], accountantCsv(rows, 'fictional'))
  assert.equal(calls[0][3].exclusive, true)
  assert.equal(calls[1][1], 'holdings-fictional-coverage.json')
  assert.equal(calls[1][3].exclusive, true)
  assert.deepEqual(calls[2], ['release', 'scratch'])
})

test('export cancels without writing, and distinguishes a failed second write', async () => {
  const calls = []
  const files = { folders: async () => [], requestFolder: async () => ({ ok: false }),
    release: async id => calls.push(['release', id]),
    writeText: async (_id, file) => {
      calls.push(file)
      if (file.endsWith('.json')) throw Error('simulated second write failure')
      return { ok: true }
    } }
  assert.equal(await saveAccountantExport(files, [], '{}', () => true), 'No folder selected.')
  assert.deepEqual(calls, [])
  files.requestFolder = async () => ({ ok: true, folder: { id: 'scope' } })
  assert.match(await saveAccountantExport(files, [], '{}', () => true), /CSV was saved.*manifest was not/)
  assert.deepEqual(calls, ['holdings-fictional-records.csv', 'holdings-fictional-coverage.json', ['release', 'scope']])
  calls.length = 0
  files.requestFolder = async () => {
    calls.push('selected')
    return { ok: true, folder: { id: 'scope' } }
  }
  assert.equal(await saveAccountantExport(files, [], '{}', () => calls.length === 0), '')
  assert.deepEqual(calls, ['selected', ['release', 'scope']])
})

test('same-source workspace remount restores chat and date scope without keeping another owner', () => {
  const memory = workspaceMemory()
  const local = JSON.stringify(['local', 'default'])
  const remote = JSON.stringify(['remote', 'default'])
  memory.read(local)
  memory.update(local, 'scope', 'kraken')
  memory.update(local, 'start', '2026-01-01')
  memory.update(local, 'thread', [{ question: 'What fees?', scope: 'Kraken' }])
  assert.equal(memory.read(local).scope, 'kraken')
  assert.equal(memory.read(local).start, '2026-01-01')
  assert.equal(memory.read(local).thread[0].scope, 'Kraken')
  assert.equal(memory.read(remote).thread.length, 0)
  assert.equal(memory.update(local, 'scope', 'wallet'), null)
  assert.equal(memory.read(local).scope, 'all')
})

test('Binance local answers cite exact fee rows and distinguish unqueried transfers', () => {
  const row = { kind: 'buy', fee_asset: 'binance-asset:BNB', evidence: ['fictional-api-fill'] }
  const review = { mode: 'binance-spot', rows: [row], fee_totals: { 'binance-asset:BNB': '0.0001200' },
    symbol: 'ETHUSDT', start: '2026-01-01', unresolved: [], gaps: ['One UTC day.'] }
  assert.equal(reviewAnswer(review, 'What fees were paid?').text, '0.00012 BNB')
  assert.equal(reviewAnswer(review, 'What fees were paid?').rows[0], row)
  assert.match(reviewAnswer(review, 'Any transfers?').text, /outside this Spot fill scan/)
  assert.match(reviewAnswer(review, 'Which scope?').text, /Binance.com Spot \/ ETHUSDT/)
})

test('Bybit fee answers cite resolved rows and disclose omitted fees and CSV', () => {
  const valid = { id: 'fictional-a', fee_asset: 'bybit-asset:ETH' }
  const unresolved = { id: 'fictional-b', fee_asset: 'bybit-asset:SOL' }
  const review = { mode: 'bybit-spot', start: '2026-01-01', rows: [valid, unresolved],
    unresolved: [unresolved.id], fee_totals: { 'bybit-asset:ETH': '0.00012345' },
    coverage: [{ complete: true }], gaps: ['There was no CSV comparison.'] }
  const fees = reviewAnswer(review, 'What fees were paid?')
  assert.equal(fees.rows.length, 1)
  assert.equal(fees.rows[0], valid)
  assert.match(fees.text, /0\.00012345 ETH.*incomplete/)
  assert.match(reviewAnswer(review, 'Which scope?').text, /no CSV comparison/)
})

test('combined selection preserves evidence, dates and exact fees without exposing unscoped IDs', () => {
  const row = (id, at, fee) => ({ id, at, kind: 'buy', asset: 'bybit-pair:ETHUSDT',
    quantity: '0.01', fee, fee_asset: 'bybit-asset:ETH', evidence: ['source:' + id] })
  const input = { mode: 'bybit-spot', status: 'ready', snapshot: {
    mode: 'bybit-spot', start: '2026-01-01', end: '2026-01-02', unresolved: ['hidden', 'uncertain'],
    coverage: [{ section: 'spot-fills', complete: true, unresolved_fees: ['hidden', 'uncertain'] }],
    rows: [row('a', '2026-01-01T12:00:00+00:00', '0.10000000000000000001'),
      row('b', '2026-01-01T13:00:00+00:00', '-0.00000000000000000001'),
      row('uncertain', '2026-01-01T14:00:00+00:00', '0.50'),
      row('hidden', '2026-01-02T12:00:00+00:00', '1.00')],
  } }
  const combined = combineReviews([input, { mode: 'ethereum', status: 'empty' }], '2026-01-01', '2026-01-01')
  assert.deepEqual(Array.from(combined.rows, item => item.originalId), ['a', 'b', 'uncertain'])
  assert.equal(combined.rows[0].evidence[0], 'source:a')
  assert.equal(combined.fee_totals['bybit-asset:ETH'], '0.10000000000000000000')
  assert.equal(combined.coverage[1].state, 'No saved review')
  assert.equal(combined.coverage[0].unresolvedOutsideSelection, 1)
  assert.ok(!JSON.stringify(combined).includes('hidden'))
  assert.match(ledgerCsv(combined.rows, true), /source,originalId/)
  assert.match(reviewAnswer(combined, 'What fees were paid?').text, /0.1 ETH \(Bybit Spot API\)/)
  assert.throws(() => combineReviews([input, { mode: 'bybit-csv' }], '2026-01-01', '2026-01-01'))
})

test('combined selection isolates repeated identities instead of merging them', () => {
  const row = { id: 'repeated', at: '2026-01-01T10:00:00Z', kind: 'transfer', evidence: ['fictional'] }
  const selected = combineReviews([{ mode: 'ethereum', status: 'ready', snapshot: {
    mode: 'ethereum', start: 100, end: 200, rows: [row, row], coverage: [], unresolved: [],
  } }], '2026-01-01', '2026-01-01', true)
  assert.equal(selected.mode, 'combined-sample')
  assert.equal(selected.rows.length, 0)
  assert.equal(selected.conflicts['ethereum:repeated'], 2)
  assert.equal(selected.coverage[0].state, 'Selected blocks only snapshot. UTC date coverage unverified.')
})

test('combined coverage separates empty, partial, missing and unavailable history', () => {
  const day = '2026-01-01'
  const complete = { section: 'spot-fills', pages: 1, failed_pages: [], complete: true }
  const saved = (mode, stream) => ({ mode, status: 'ready', snapshot: {
    mode, start: day, end: day, rows: [], unresolved: [], coverage: [stream],
  } })
  const inputs = [saved('kraken-spot', complete),
    saved('binance-spot', { ...complete, failed_pages: [2], gap: 'failed-page' }),
    { mode: 'bybit-spot', status: 'storage-locked' }, { mode: 'ethereum', status: 'empty' }]
  const review = combineReviews(inputs, day, day)
  assert.deepEqual(Array.from(review.coverage, item => item.historyStatus),
    ['no-activity', 'incomplete', 'unavailable', 'missing-review'])
  assert.equal(review.coverage[0].fetchedStart, day)
  assert.equal(review.coverage[1].fetchedStart, undefined)
  assert.equal(review.coverage[1].sourceCoverage[0].failedPages[0], 2)
  assert.equal(review.coverage[3].requestedStart, day)
  const answer = reviewAnswer(review, 'What history is missing?')
  assert.match(answer.text, /No activity returned.*partial.*Unavailable.*No saved review.*does not establish balances/)
  assert.deepEqual(Array.from(answer.rows, row => row.coverageMode), inputs.map(input => input.mode))
  const wider = combineReviews([inputs[0]], day, '2026-01-02')
  assert.equal(wider.coverage[0].historyStatus, 'incomplete')
  assert.equal(wider.coverage[0].fetchedEnd, day)
})

test('combined selection preserves conflict pairs only when both dates are in scope', () => {
  const api = { id: 'day-one', at: '2026-01-01T23:59:00Z', fee: '0.001', fee_asset: 'kraken-asset:XXBT' }
  const file = { ...api, fee: '0.002' }
  const input = { mode: 'kraken-spot', status: 'ready', snapshot: {
    mode: 'kraken-spot', rows: [api], coverage: [], unresolved: ['day-one', 'spans-dates'],
    conflict_evidence: { 'day-one': [api, file], 'spans-dates': [api, { ...file, at: '2026-01-02T00:00:00Z' }] },
  } }
  const selection = combineReviews([input], '2026-01-01', '2026-01-01', true)
  assert.deepEqual(Object.keys(selection.conflictEvidence), ['kraken-spot:day-one'])
  assert.deepEqual(Array.from(selection.unresolved), ['kraken-spot:day-one'])
  assert.equal(selection.coverage[0].unresolvedOutsideSelection, 1)
  assert.equal(selection.rows.length, 0)
  assert.equal(Object.keys(selection.fee_totals).length, 0)
  assert.ok(!JSON.stringify(selection).includes('spans-dates'))
})

test('combined transfer chat cites both original rows without changing totals', () => {
  const out = { id: 'out', kind: 'withdrawal', fee: '0.01' }
  const into = { id: 'in', kind: 'deposit', fee: '0' }
  const review = { mode: 'combined-sample', rows: [out, into], fee_totals: { ETH: '0.01' },
    transferReview: { status: 'ready', items: [{ outgoing: 'out', incoming: 'in', status: 'accepted' }] } }
  const answer = reviewAnswer(review, 'Which transfer links are accepted?')
  assert.match(answer.text, /1 accepted, 0 rejected, 0 awaiting review/)
  assert.deepEqual(Array.from(answer.rows), [out, into])
  assert.equal(review.fee_totals.ETH, '0.01')
})

test('Hermes draft contains only selected fictional facts, exact totals and review references', () => {
  const rows = sample.rows.filter(row => row.id.startsWith('kraken:'))
  const selected = combineReviews([{ mode: 'kraken-spot', status: 'ready', snapshot: {
    ...sample.history_demo['kraken-spot'], mode: 'kraken-spot', rows,
    conflict_evidence: sample.conflict_demo, unresolved: Object.keys(sample.conflict_demo),
  } }], '2026-01-01', '2026-01-01', true)
  const sourceId = Object.keys(selected.conflicts)[0]
  const text = combinedDraft(selected, { status: 'ready', items: [] },
    { status: 'ready', items: [{ sourceId, status: 'excluded' }] })
  const data = JSON.parse(text.split('\n\n')[1])
  assert.deepEqual(data.scope, { startUTC: '2026-01-01', endUTC: '2026-01-01', sources: ['Kraken Spot'] })
  assert.deepEqual(data.exactFeeTotals, { 'kraken-asset:XXBT': '0.0002' })
  assert.deepEqual(data.selectedRows.map(row => row.id), Array.from(selected.rows, row => row.id))
  assert.equal(data.conflictReview.disputed[0].status, 'excluded')
  assert.equal(data.conflictReview.disputed[0].excludedFromTotals, true)
  assert.equal(data.conflictReview.disputed[0].evidence.length, 2)
  assert.doesNotMatch(text, /observations|sourceRecords|ethereum|binance|bybit/i)
  assert.equal(combinedDraft({ ...selected, mode: 'combined' }), null)
})

test('fresh workspace opens Portfolio without carrying another profile transcript', () => {
  const memory = workspaceMemory()
  assert.equal(memory.read('profile-a').tab, 'Overview')
  memory.update('profile-a', 'thread', ['local turn'])
  assert.equal(memory.read('profile-a').thread.length, 1)
  assert.equal(memory.read('profile-b').thread.length, 0)
  assert.equal(workspaceMemory().read('profile-a').thread.length, 0)
})

test('combined accountant manifest explains exclusions and never turns links into ledger entries', () => {
  const inputs = ['kraken-spot', 'ethereum', 'binance-spot', 'bybit-spot'].map(mode => {
    const history = sample.history_demo[mode]
    const conflict_evidence = mode === 'kraken-spot' ? sample.conflict_demo : {}
    return { mode, status: history.status, snapshot: {
      ...history, mode, conflict_evidence, unresolved: Object.keys(conflict_evidence),
      rows: sample.rows.filter(row => mode === (row.id.startsWith('kraken:') ? 'kraken-spot' : 'ethereum'))
        .concat(sample.transfer_demo.filter(row => row.sourceMode === mode)),
    } }
  })
  const review = combineReviews(inputs, '2026-01-01', '2026-01-01', true)
  const sourceId = Object.keys(review.conflicts)[0]
  const manifest = JSON.parse(JSON.stringify(combinedManifest(review,
    { status: 'ready', items: [{ id: 'fixture-accepted-link', status: 'accepted' }, { id: 'fixture-undone-link', status: 'undone' }] },
    { status: 'ready', items: [{ id: 'fixture-conflict', sourceId, status: 'excluded', choices: [] }] })))
  assert.equal(manifest.rows.length, 9)
  assert.equal(new Set(manifest.rows).size, 9)
  assert.deepEqual(manifest.feeTotals, { 'kraken-asset:XXBT': '0.0002', 'eip155:1/slip44:60': '0.010' })
  assert.deepEqual(manifest.transferReview.unresolved, ['fixture-undone-link'])
  assert.deepEqual(manifest.conflictReview.unresolved, [])
  assert.equal(manifest.conflictReview.disputedRecords[0].id, sourceId)
  assert.deepEqual(manifest.conflictReview.disputedRecords[0].evidence,
    Array.from(review.conflictEvidence[sourceId].observations.flatMap(row => row.evidence)))
  assert.deepEqual(manifest.unresolvedItems, [{
    id: sourceId, inRecords: false, reason: 'Conflicting observations excluded from the records and totals.',
  }])
  assert.deepEqual(manifest.coverage.map(item => item.historyStatus), ['bounded-complete', 'block-partial', 'no-activity', 'unavailable'])
  const csv = ledgerCsv([{ ...review.rows[0], evidence: ['=HYPERLINK("fictional")'] }], true)
  assert.match(csv, /"'=HYPERLINK\(""fictional""\)"/)
})

test('accountant CSV annotates selected rows without creating a transfer or changing amounts', () => {
  const timestamp = '2026-01-01T00:00:00Z'
  const rows = [
    { id: 'out', originalId: 'orig-out', source: 'Kraken Spot', at: timestamp, kind: 'withdrawal',
      asset: 'ETH', quantity: '-0.123456789012345678', fee: '0.0100', fee_asset: 'ETH', evidence: ['api:out'] },
    { id: 'in', originalId: 'orig-in', source: 'Ethereum', at: timestamp, kind: 'deposit',
      asset: 'ETH', quantity: '0.113456789012345678', evidence: ['=HYPERLINK("fixture")'] },
  ]
  const csv = accountantCsv(rows, 'combined-sample', {
    transfers: [
      { id: 't1', outgoing: 'out', incoming: 'in', status: 'accepted' },
      { id: 't2', outgoing: 'out', incoming: 'outside', status: 'undone' },
    ],
    unresolved: ['in'],
  })
  assert.deepEqual(csv.split('\r\n'), [
    'id,timestamp_utc,kind,asset,quantity,cost,fee,fee_asset,evidence,source,original_id,record_review,transfer_reviews',
    '"out","2026-01-01T00:00:00Z","withdrawal","ETH","-0.123456789012345678","","0.0100","ETH","api:out","Kraken Spot","orig-out","recorded","t1:accepted;t2:undone"',
    '"in","2026-01-01T00:00:00Z","deposit","ETH","0.113456789012345678","","","","\'=HYPERLINK(""fixture"")","Ethereum","orig-in","needs-review","t1:accepted"',
    '',
  ])
  assert.match(accountantCsv(sample.rows.filter(row => row.id.startsWith('kraken:')), 'fictional'), /"Kraken Spot"/)
})


test('accountant packet keeps scoped decision events, evidence and open questions', () => {
  const rows = [{ id: 'out', source: 'Kraken Spot', originalId: 'o', evidence: ['api:o'] },
    { id: 'in', source: 'Ethereum', originalId: 'i', evidence: ['chain:i'] }]
  const trail = [{ status: 'accepted', revision: 1, updatedAt: '2026-11-01T00:00:00Z' },
    { status: 'undone', revision: 2, updatedAt: '2026-11-01T01:00:00Z' }]
  const manifest = JSON.parse(JSON.stringify(packetReview({ rows, unresolved: ['missing'] }, {
    sessionOnly: true, items: [{ id: 'valid', outgoing: 'out', incoming: 'in', status: 'undone', history: trail },
      { id: 'other', outgoing: 'out', incoming: 'outside', status: 'pending', history: trail }],
  })))
  assert.deepEqual(manifest.decisionLog.map(event => event.status), ['accepted', 'undone'])
  assert.deepEqual(manifest.openQuestions.map(item => item.kind), ['transfer', 'coverage'])
  assert.deepEqual(manifest.openQuestions[0].evidence, ['api:o', 'chain:i'])
  assert.equal(manifest.decisionScope, 'fictional-session')
  assert.deepEqual(manifest.sourcesPerRow.map(row => row.originalId), ['o', 'i'])
  assert.equal(manifest.decisionLog.some(event => event.id === 'other'), false)
})

test('watch client discards old-profile results and delivers only in-app notifications', async () => {
  const { alertEngine, createWatchClient } = module.namespace
  let profile = 'alpha'
  const host = { state: { connectionId: { get: () => 'local' }, profile: { get: () => profile } },
    notify: payload => calls.push(payload) }
  const alerts = alertEngine()
  const calls = []
  let deliver
  const ctx = { rest: () => new Promise(resolve => { deliver = resolve }),
    os: { notify: payload => calls.push(payload) } }
  const client = createWatchClient(ctx, alerts, host, () => 1)
  const first = client({ action: 'poll' })
  profile = 'beta'
  deliver({ status: 'ready', unread: 1, new: [{ id: 'alpha-alert' }] })
  await first
  assert.equal(alerts.read('["local","alpha",1]').watch, null)
  assert.equal(calls.length, 0)
  const second = client({ action: 'poll' })
  deliver({ status: 'ready', unread: 1, new: [{ id: 'beta-alert' }] })
  await second
  assert.equal(alerts.read('["local","beta",1]').watch.unread, 1)
  assert.equal(calls.length, 1)
  assert.match(calls[0].message, /1 new Holdings alert/)
})

test('accountant summary separates holdings, lots, estimates and cited reviewed flows', () => {
  const { accountantSummary } = module.namespace
  const output = accountantSummary({
    selection: 'Fictional QA',
    holdings: [{ source: 'Wallet 1', at: '2026-11-09', rows: [{ asset: 'ETH', balance: '0.123456789123456789' }] }],
    lots: [{ source: 'wallet', asset: 'ETH', quantity: '0.12', costUsd: '300.00',
      lots: [{ date: '2026-10-02', quantity: '0.12', costUsd: '300.00', id: 'lot-evidence' }] }],
    pnl: [{ source: 'Wallet 1', at: '2026-11-09', pnl: { realized_gain: null, unrealized_gain: '-10.02' } }],
    report: { years: [{ year: '2026', recordCount: 2, flows: [] }],
      transfers: [{ status: 'accepted', excludedFromFlows: true, evidence: ['wallet-ref', 'exchange-ref'] }] },
    openQuestions: [{ question: 'Check coverage', evidence: ['coverage-id'] }],
  })
  assert.match(output, /HOLDINGS SNAPSHOTS[\s\S]*0\.123456789123456789/)
  assert.match(output, /IMPORTED COST BASIS[\s\S]*lot-evidence/)
  assert.match(output, /PROVIDER ESTIMATE \(ZERION\)[\s\S]*Realized Unavailable \/ Unrealized -10.02/)
  assert.match(output, /accepted \/ linked legs excluded from flows, original fees retained \/ Evidence wallet-ref \/ exchange-ref/)
  assert.match(output, /Check coverage \/ Evidence coverage-id/)
})

test('a failed background alert poll reports unavailable instead of a quiet check', async () => {
  const { alertEngine, createWatchClient } = module.namespace
  const host = { state: { connectionId: { get: () => 'local' }, profile: { get: () => 'alpha' } },
    notify: () => assert.fail('A failed check has no findings') }
  const alerts = alertEngine()
  const key = '["local","alpha",1]'
  alerts.watch(key, { status: 'ready', unread: 0 }, false)
  const client = createWatchClient({ rest: async () => { throw new Error('offline') } }, alerts, host, () => 1)
  await client({ action: 'poll' })
  assert.equal(alerts.read(key).watch.status, 'unavailable')
})

test('accountant packet writes three exclusive files and identifies a failed summary', async () => {
  const writes = []
  const files = { folders: async () => [], requestFolder: async () => ({ ok: true, folder: { id: 'qa' } }),
    release: async () => true, stat: async () => ({ ok: false, error: 'not-found' }),
    writeText: async (_id, filename, data, options) => {
      writes.push({ filename, data, exclusive: options.exclusive })
      return { ok: !filename.endsWith('-summary.txt') }
    },
  }
  const result = await saveAccountantExport(files, [], '{}', () => true, 'balance-snapshots', { summary: 'Exact summary' })
  assert.match(result, /CSV and coverage manifest were saved.*summary was not/)
  assert.deepEqual(writes.map(row => row.filename), ['holdings-balance-snapshots-records.csv',
    'holdings-balance-snapshots-coverage.json', 'holdings-balance-snapshots-summary.txt'])
  assert.equal(writes[2].data, 'Exact summary')
  assert.ok(writes.every(row => row.exclusive))
  writes.length = 0
  files.stat = async () => ({ ok: true, exists: true })
  assert.match(await saveAccountantExport(files, [], '{}', () => true), /existing accountant packet/)
  files.stat = async () => ({ ok: false, error: 'not-granted' })
  assert.match(await saveAccountantExport(files, [], '{}', () => true), /could not check this folder/)
  assert.deepEqual(writes, [])
})


test('account rail passes exact source identity and keeps review rows distinct from selected accounts', () => {
  const { AccountMenu } = module.namespace
  const selected = []
  const items = [{ id: 'all', name: 'All loaded wallets', detail: 'Current snapshots', icon: 'all' },
    { id: '1', name: 'Wallet 2', detail: 'Base', icon: 'wallet' }]
  const tree = AccountMenu({ items, selected: '1', onSelect: (id, event) => selected.push([id, event]) })
  assert.deepEqual(Array.from(tree.children, child => child['aria-pressed']), [false, true])
  const event = { target: 'wallet-button' }
  tree.children[1].onClick(event)
  assert.deepEqual(selected, [['1', event]])
  const review = AccountMenu({ items: [{ id: 'kraken-spot', name: 'Kraken Spot', icon: 'kraken', detail: 'View source coverage' }],
    selected: null, onSelect: id => selected.push(id) })
  assert.equal(review.children[0]['aria-pressed'], undefined)
  review.children[0].onClick(event)
  assert.equal(selected[1], 'kraken-spot')
})

test('ticker fills the bar at rest, freezes in place on pause and offers auto-scroll and add market controls', () => {
  const { MarketStrip, tickerCopies } = module.namespace
  hookValue = undefined
  const opened = [], added = []
  const draw = (onAdd = () => added.push(1)) => MarketStrip({ entries: {}, open: chart => opened.push(chart.market), onAdd })
  let tree = draw()
  assert.equal(tree['data-moving'], false)
  assert.equal(tree.children[0].children, 'Markets')
  const groups = tree.children[1].children.children
  assert.equal(groups.length, tickerCopies(4))
  assert.ok(groups.length >= 2)
  assert.equal(tree.style['--ticker-copies'], groups.length)
  assert.equal(groups[0]['aria-hidden'], undefined)
  assert.ok(groups.slice(1).every(group => group['aria-hidden'] === true && group.children.every(item => item.tabIndex === -1)))
  const still = groups[0]
  assert.equal(still.children.length, 4)
  still.children[2].onClick()
  still.children[3].onClick()
  assert.deepEqual(opened.splice(0), ['SOLUSD', 'BNBUSD'])
  const motion = tree.children[2]
  assert.equal(motion['aria-label'], 'Start auto-scroll of the market ticker')
  assert.equal(motion.title, motion['aria-label'])
  assert.equal(motion.children[1].children, 'Auto-scroll')
  tree.children[3].onClick()
  assert.deepEqual(added, [1])
  assert.equal(tree.children[3].children, '+ Add market')
  motion.onClick()
  tree = draw()
  assert.equal(tree['data-moving'], true)
  assert.equal(tree.children[2]['aria-label'], 'Pause auto-scroll of the market ticker')
  // Same groups and keys while moving, so pausing only stops the animation where it is.
  assert.deepEqual(Array.from(tree.children[1].children.children, group => group.key), Array.from(groups, group => group.key))
  tree.children[1].children.children[0].children[0].onClick()
  tree.children[1].children.children[1].children[0].onClick()
  assert.equal(opened[0], opened[1])
  tree.children[2].onClick()
  assert.equal(draw()['data-moving'], false)
  assert.equal(draw(null).children[3], null)
  assert.equal(tickerCopies(1), 6)
  assert.equal(tickerCopies(12), 2)
  hookValue = undefined
})

test('ticker CSS pauses the animation instead of removing it, so the strip never jumps', () => {
  const src = fs.readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
  assert.match(src, /\.market-strip \.market-strip-track \{ animation:holdings-market-scroll [^}]*animation-play-state:paused; \}/)
  assert.match(src, /\.market-strip\[data-moving=true\] \.market-strip-track \{ animation-play-state:running; \}/)
  assert.match(src, /translateX\(calc\(-100% \/ var\(--ticker-copies, 2\)\)\)/)
  assert.doesNotMatch(src, /market-strip-window:has\(button:focus-visible\) \.market-strip-track \{ animation:none; \}/)
})


test('support questions stay separate from portfolio questions and FAQ links open exact articles', () => {
  const { SupportPage, isSupportQuestion } = module.namespace
  assert.equal(isSupportQuestion('Where do I add my API keys?'), true)
  assert.equal(isSupportQuestion('How do I export my records?'), true)
  assert.equal(isSupportQuestion('Where did my ETH go?'), false)
  assert.equal(isSupportQuestion('What fees did I pay?'), false)
  const guide = JSON.parse(fs.readFileSync(new URL('../dashboard/support.json', import.meta.url), 'utf8'))
  const actions = []
  const tree = SupportPage({ guide, selected: 'keys', onSelect: id => actions.push(id),
    onNavigate: id => actions.push(id), onAsk: item => actions.push(item.id) })
  const sections = tree.children.filter(item => item?.className === 'support-article')
  assert.equal(sections.length, 1)
  const keys = sections[0]
  assert.equal(keys.children[0].children, guide.articles.find(item => item.id === 'keys').title)
  assert.equal(keys.children[1].children, guide.articles.find(item => item.id === 'keys').answer)
  keys.children[2].children[0].onClick()
  keys.children[2].children[1].onClick()
  assert.deepEqual(actions, ['keys', 'Sources'])

})


test('context rails replace irrelevant account filters and preserve applicable source controls', () => {
  const { pageRailKind, PageMenu, activityAskLabel } = module.namespace
  for (const loaded of [false, true]) {
    const state = { review: loaded, walletScope: loaded }
    for (const [tab, kind] of Object.entries({Support: 'help', Settings: 'settings', Charts: 'markets', Alerts: 'alerts', Security: 'security'}))
      assert.equal(pageRailKind(tab, state), kind)
    for (const tab of ['Overview', 'Activity', 'Exports']) assert.equal(pageRailKind(tab, state), 'accounts')
  }
  assert.equal(pageRailKind('Sources'), 'connections')
  assert.equal(pageRailKind('Sources', { review: true }), 'accounts')
  for (const tab of ['Wallets & DeFi', 'DeFi']) {
    assert.equal(pageRailKind(tab), 'wallet-sections')
    assert.equal(pageRailKind(tab, { walletScope: true }), 'accounts')
  }
  const selected = []
  const menu = PageMenu({ items: [{id: 'keys', label: 'Keys and storage'}, {id: 'conversation', label: 'Conversation'}],
    selected: 'keys', onSelect: id => selected.push(id) })
  assert.equal(menu.children[0]['aria-current'], 'location')
  assert.equal(menu.children[1]['aria-current'], undefined)
  menu.children[1].onClick()
  assert.deepEqual(selected, ['conversation'])
  assert.equal(activityAskLabel(false), 'Ask about this activity')
  assert.equal(activityAskLabel(true), 'Ask about this record')
})

test('combined fee answers stay on the exchange, wallet or asset the question names', () => {
  const { askScope, transferConversationItem } = module.namespace
  const fill = (id, at, asset, fee, feeAsset, kind = 'buy') => ({ id, at, kind, asset, quantity: '0.1', fee, fee_asset: feeAsset, evidence: ['fictional'] })
  const snapshot = (mode, rows) => ({ mode, status: 'ready', snapshot: { mode, start: '2026-01-01', end: '2026-01-01', unresolved: [],
    coverage: [{ section: 'spot-fills', complete: true, pages: 1, failed_pages: [] }], rows } })
  const kraken = { mode: 'kraken-spot', status: 'ready', snapshot: { mode: 'kraken-spot', start: '2026-01-01', end: '2026-01-01',
    unresolved: ['k-dup'], coverage: [{ section: 'ledger', complete: true, pages: 1, failed_pages: [] }],
    conflict_evidence: { 'k-dup': [fill('k-dup', '2026-01-01T03:00:00Z', 'kraken-pair:BTC/USD', '1', 'kraken-asset:XXBT'),
      fill('k-dup', '2026-01-01T03:00:00Z', 'kraken-pair:BTC/USD', '2', 'kraken-asset:XXBT')] },
    rows: [fill('k1', '2026-01-01T01:00:00Z', 'kraken-pair:BTC/USD', '0.0002', 'kraken-asset:XXBT'),
      { ...fill('k2', '2026-01-01T02:00:00Z', 'eip155:1/slip44:60', '0.010', 'eip155:1/slip44:60', 'withdrawal'), quantity: '-0.5' }] } }
  const combined = combineReviews([kraken,
    { mode: 'ethereum', status: 'ready', snapshot: { mode: 'ethereum', start: 1, end: 2, unresolved: [], coverage: [],
      rows: [{ id: 'w1', at: '2026-01-01T04:00:00Z', kind: 'transfer', asset: 'eip155:1/slip44:60', quantity: '0.49', fee: '0', fee_asset: null, evidence: ['f'] }] } },
    snapshot('binance-spot', [fill('b1', '2026-01-01T05:00:00Z', 'binance-pair:ETH/USDT', '0.00012345', 'binance-asset:BNB'),
      fill('b2', '2026-01-01T06:00:00Z', 'binance-pair:ETH/USDT', '0.0200', 'binance-asset:USDT', 'sell')]),
    snapshot('bybit-csv', [fill('y1', '2026-01-01T07:00:00Z', 'bybit-pair:ETHUSDT', '0.00001', 'bybit-asset:ETH'),
      fill('y2', '2026-01-01T08:00:00Z', 'bybit-pair:SOLUSDT', '0.0300', 'bybit-asset:USDT', 'sell')]),
  ], '2026-01-01', '2026-01-01', true)

  const bybit = reviewAnswer(combined, 'What fees did I pay on Bybit?')
  assert.equal(bybit.text, '0.00001 ETH (Bybit Spot CSV) / 0.03 USDT (Bybit Spot CSV) Only resolved fees in this review are counted.'
    + ' Incomplete source history can omit fees.')
  assert.deepEqual(Array.from(bybit.rows, row => row.originalId), ['y1', 'y2'])
  assert.doesNotMatch(bybit.text, /Kraken|Binance|BNB|BTC|disputed/)

  const krakenFees = reviewAnswer(combined, 'What fees did I pay on Kraken?')
  assert.match(krakenFees.text, /^0\.0002 BTC \(Kraken Spot\) \/ 0\.01 ETH \(Kraken Spot\) .* 1 disputed record is excluded\./)
  assert.deepEqual(Array.from(krakenFees.rows, row => row.originalId), ['k1', 'k2'])
  // A selected record is the chat's scope, but a fee question that names a source covers the whole review, and says so.
  const selected = combined.rows.find(row => row.originalId === 'w1')
  const withRecord = reviewAnswer(combined, 'What fees did I pay on Kraken?', { record: selected })
  assert.match(withRecord.text, /^This covers every Kraken fee in this review, not only the selected record\. 0\.0002 BTC \(Kraken Spot\) \/ 0\.01 ETH \(Kraken Spot\) /)
  assert.deepEqual(Array.from(withRecord.rows, row => row.originalId), ['k1', 'k2'])
  assert.match(reviewAnswer(combined, 'What fees did I pay?', { record: selected }).text, /^This covers every fee in this review, not only the selected record\. /)
  assert.doesNotMatch(krakenFees.text, /selected/)
  assert.match(reviewAnswer(combined, 'what did binance charge me in fees').text, /^0\.00012345 BNB \(Binance\.com Spot\) \/ 0\.02 USDT \(Binance\.com Spot\) /)
  assert.match(reviewAnswer(combined, 'What fees did my wallet pay?').text, /^No resolved fee rows for your wallet in this selection\./)
  assert.match(reviewAnswer(combined, 'What fees did I pay in USDT?').text, /^0\.02 USDT \(Binance\.com Spot\) \/ 0\.03 USDT \(Bybit Spot CSV\) /)
  assert.match(reviewAnswer(combined, 'What fees did I pay on SOL?').text, /^0\.03 USDT \(Bybit Spot CSV\) /)
  assert.match(reviewAnswer(combined, 'What fees did I pay on Binance and Bybit?').text, /BNB.*Binance.*Bybit/)
  assert.doesNotMatch(reviewAnswer(combined, 'What fees did I pay on Binance and Bybit?').text, /Kraken/)

  // With no names, the answer is the full fee_totals answer it was before.
  const all = reviewAnswer(combined, 'What fees did I pay?')
  assert.equal(all.text, Object.entries(combined.fee_totals).map(([asset]) => asset).length === 6
    ? '0.0002 BTC (Kraken Spot) / 0.01 ETH (Kraken Spot) / 0.00012345 BNB (Binance.com Spot) / 0.02 USDT (Binance.com Spot) / '
      + '0.00001 ETH (Bybit Spot CSV) / 0.03 USDT (Bybit Spot CSV) Only resolved fees in this review are counted. '
      + '1 disputed record is excluded. Incomplete source history can omit fees.' : 'fee_totals changed')
  assert.equal(all.rows.length, 6)

  assert.match(reviewAnswer(combined, 'How many trades on Bybit?').text, /^2 matching source rows for Bybit in this selection\./)
  assert.match(reviewAnswer(combined, 'How many trades?').text, /^5 matching source rows in this selection\./)
  assert.match(reviewAnswer(combined, 'What is the coverage for Bybit?').text, /^Bybit Spot CSV — /)
  assert.doesNotMatch(reviewAnswer(combined, 'What is the coverage for Bybit?').text, /Kraken/)
  assert.match(reviewAnswer(combined, 'What coverage do I have?').text, /Kraken Spot — .*Bybit Spot CSV — /)

  assert.deepEqual(askScope('Fees on Bybit for SOL').names, ['Bybit', 'SOL'])
  assert.equal(askScope('What did I sell?').named, false)
  assert.equal(askScope('What fees did I pay?').named, false)
  const rows = sample.transfer_demo.map(row => ({ ...row, source: row.sourceMode === 'kraken-spot' ? 'Kraken Spot' : 'Ethereum wallet' }))
  const item = { id: 'matched', outgoing: rows[0].id, incoming: rows[1].id, confidence: 'Hash match', status: 'pending', reasons: [] }
  const sampleReview = { mode: 'combined-sample', rows }
  assert.equal(transferConversationItem(sampleReview, [item], 'What fees did I pay on Bybit for ETH?'), null)
  assert.equal(transferConversationItem(sampleReview, [item], 'What fees did I pay on ETH?'), null)
  assert.equal(transferConversationItem(sampleReview, [item], 'Where did my ETH go?'), item)
  assert.equal(transferConversationItem(sampleReview, [item], 'What was the fee on my ETH transfer?'), item)
})
