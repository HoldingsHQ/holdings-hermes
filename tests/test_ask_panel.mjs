import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
const source = fs.readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
const mod = new vm.SourceTextModule(source)
const element = (type, props, key) => ({ type, props, key })
const stub = values => new vm.SyntheticModule(Object.keys(values), function () { for (const [k, v] of Object.entries(values)) this.setExport(k, v) })
await mod.link(name => ({
  '@hermes/plugin-sdk': stub({ ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'nav', gatewayActivationEpoch: () => 0, host: {}, useValue() {} }),
  react: stub({ useEffect() {}, useRef: initial => ({ current: initial }), useState: initial => [initial, () => {}], useSyncExternalStore() {} }),
  'react/jsx-runtime': stub({ jsx: element, jsxs: element }),
})[name]); await mod.evaluate()
const { AskButton, railPlacement, pageRailKind, datePresetRange, activeDatePreset, DATE_PRESETS, workspaceMemory } = mod.namespace
const css = source.slice(source.indexOf('const css = `'), source.indexOf('`\n\nfunction label'))

test('Ask Holdings starts closed for a new workspace', () => {
  assert.equal(workspaceMemory().read('profile-a').chatOpen, false)
})

test('floating Ask button controls the chat panel and hides while it is open', () => {
  const opened = []
  const ref = { current: null }
  const closed = AskButton({ open: false, onOpen: () => opened.push(1), buttonRef: ref })
  assert.equal(closed.type, 'button')
  assert.equal(closed.props.className, 'ask-fab')
  assert.equal(closed.props['aria-controls'], 'holdings-chat')
  assert.equal(closed.props['aria-expanded'], false)
  assert.equal(closed.props.hidden, undefined)
  assert.equal(closed.props.ref, ref)
  assert.equal(closed.props.children.at(-1).props.children, 'Ask Holdings')
  closed.props.onClick()
  assert.deepEqual(opened, [1])
  const open = AskButton({ open: true, onOpen() {}, buttonRef: ref })
  assert.equal(open.props.hidden, true)
  assert.equal(open.props['aria-expanded'], true)
})

test('the Ask button renders in the page card on every tab, after the page and its evidence panel', () => {
  const shell = source.slice(source.indexOf("return jsxs('main', { className: 'holdings holdings-shell'"))
  const fab = shell.indexOf("jsx(AskButton, { open: chatOpen, buttonRef: askButton, onOpen: askAboutSelection })")
  assert.equal(shell.indexOf('jsx(AskButton', fab + 1), -1, 'one Ask button')
  assert.ok(fab > shell.indexOf("jsxs('div', { className: 'center'"), 'button sits in the page card')
  assert.ok(fab > shell.indexOf("className: 'panel stack evidence'"), 'button follows the evidence panel')
  assert.ok(fab < shell.indexOf("id: 'holdings-chat'"), 'button comes before the chat panel')
  assert.doesNotMatch(source, /Show chat|'Collapse'|Collapse chat|Collapse Ask Holdings/)
})

test('the chat panel has a close button, closes on Escape and returns focus to the Ask button', () => {
  assert.match(source, /button\('×', \(\) => closeChat\(\), \{ className: 'chat-close', 'aria-label': 'Close Ask Holdings', 'aria-controls': 'holdings-chat' \}\)/)
  assert.match(source, /onKeyDown: event => \{\s*if \(event\.key !== 'Escape' \|\| event\.defaultPrevented\) return\s*event\.preventDefault\(\); event\.stopPropagation\(\); closeChat\(\)/)
  assert.match(source, /if \(!chatOpen && focusAskButton\.current\) \{\s*focusAskButton\.current = false\s*askButton\.current\?\.focus\(\)/)
  assert.match(source, /'aria-labelledby': 'holdings-chat-title'/)
})

test('Settings keeps a synced open and close control for the chat', () => {
  assert.match(source, /button\(chatOpen \? 'Close Ask Holdings' : 'Open Ask Holdings', \(\) => \{\n\s*if \(chatOpen\) return closeChat\(\{ returnFocus: false \}\)\n\s*focusChat\.current = true\n\s*updateView\('chatOpen', true\)\n\s*\},\n\s*\{ className: 'quiet-action', 'aria-expanded': Boolean\(chatOpen\), 'aria-controls': 'holdings-chat' \}\)/)
})

test('panel styles push content when wide, cover the page under 720px and respect reduced motion', () => {
  assert.match(css, /\.grid\[data-rail=false\]:has\(> #holdings-chat\) \{ grid-template-columns:minmax\(0,1fr\) var\(--workspace-chat\)/)
  assert.match(css, /\.grid\[data-rail=false\]:has\(> #holdings-chat\[hidden\]\) \{ grid-template-columns:minmax\(0,1fr\); \}/)
  const narrow = css.slice(css.indexOf('@container (max-width:720px)'))
  assert.match(narrow, /#holdings-chat:not\(\[hidden\]\) \{ position:absolute; inset:0;/)
  const reduced = css.slice(css.lastIndexOf('@media (prefers-reduced-motion: reduce)'))
  assert.match(reduced, /#holdings-chat:not\(\[hidden\]\) \{ animation:none; \}/)
  assert.match(reduced, /ask-fab \{ transition:none; \}/)
  assert.match(css, /button\.ask-fab \{ position:absolute; right:14px; bottom:11px;[^}]*background:var\(--selected\); color:var\(--selected-ink\)/)
})

test('account rails appear only on Wallets and Sources and other pages use the header bar', () => {
  const tabs = ['Overview', 'Activity', 'Wallets & DeFi', 'DeFi', 'Charts', 'Security', 'Alerts', 'Sources', 'Exports', 'Settings', 'Support']
  const placements = state => Object.fromEntries(tabs.map(tab => [tab, railPlacement(pageRailKind(tab, state), tab)]))
  assert.deepEqual(placements({}), { Overview: 'header', Activity: 'header', 'Wallets & DeFi': 'header', DeFi: 'header',
    Charts: 'rail', Security: 'header', Alerts: 'header', Sources: 'header', Exports: 'header', Settings: 'rail', Support: 'rail' })
  assert.deepEqual(placements({ review: true, walletScope: true }), { Overview: 'header', Activity: 'header', 'Wallets & DeFi': 'rail', DeFi: 'header',
    Charts: 'rail', Security: 'header', Alerts: 'header', Sources: 'rail', Exports: 'header', Settings: 'rail', Support: 'rail' })
  assert.match(source, /className: 'grid', 'data-rail': railShown \? 'true' : 'false'/)
  assert.match(source, /const headerBar = workspaceData && !railShown && !onSetupScreen/)
})

test('date presets produce inclusive UTC ranges and recognize custom dates', () => {
  const today = new Date('2026-11-10T20:00:00Z')
  assert.deepEqual(DATE_PRESETS.map(([id]) => id), ['all', '7d', '30d', '90d', 'ytd'])
  assert.deepEqual(datePresetRange('all', today), { start: '', end: '' })
  assert.deepEqual(datePresetRange('7d', today), { start: '2026-11-04', end: '2026-11-10' })
  assert.deepEqual(datePresetRange('30d', today), { start: '2026-10-12', end: '2026-11-10' })
  assert.deepEqual(datePresetRange('90d', today), { start: '2026-08-13', end: '2026-11-10' })
  assert.deepEqual(datePresetRange('ytd', today), { start: '2026-01-01', end: '2026-11-10' })
  assert.equal(datePresetRange('bogus', today), null)
  assert.equal(activeDatePreset('', '', today), 'all')
  assert.equal(activeDatePreset('2026-11-04', '2026-11-10', today), '7d')
  assert.equal(activeDatePreset('2026-05-01', '2026-06-01', today), 'custom')
})

test('demo banner explains that only exports are saved', () => {
  assert.match(source, /"You're viewing fictional demo data\. Nothing is saved unless you export it\."/)
  assert.doesNotMatch(source, /Nothing here is saved/)
})

test('new visible copy avoids em dashes and semicolons', () => {
  const added = ['Ask Holdings', 'Close Ask Holdings', 'Open Ask Holdings', 'You can also open the chat from the Ask Holdings button in the lower right corner of every page.',
    "You're viewing fictional demo data. Nothing is saved unless you export it.", ...DATE_PRESETS.map(([, name]) => name)]
  for (const text of added) {
    assert.ok(source.includes(text), text)
    assert.doesNotMatch(text, /—|;/)
  }
})

test('wallet total labels ETH and USDC pricing and only says partial when something is unpriced', () => {
  const { walletPricingNote } = mod.namespace
  const eth = { chain: 'Ethereum', chainId: 1, balances: [{ asset: 'ETH', units: '4774.5' }, { asset: 'WETH', units: '214.1' }, { asset: 'USDC', units: '1136.6' }] }
  const point = { valueUsd: '1', observed: 3, prices: [
    { provider: 'GeckoTerminal WETH/USD', asset: 'WETH', chainId: 1, price: '2000' },
    { provider: 'GeckoTerminal WETH/USD (ETH priced at the WETH rate)', asset: 'ETH', chainId: 1, price: '2000' },
    { provider: 'Fixed USDC/USD (USDC counted at $1)', asset: 'USDC', chainId: 1, price: '1' }] }
  const full = walletPricingNote(point, [{ snapshot: eth }])
  assert.equal(full.partial, false)
  assert.equal(full.pricing, 'ETH is priced at the WETH rate because DefiLlama had no usable ETH price. USDC is counted at $1 because DefiLlama had no usable USDC price.')
  assert.deepEqual([full.found, full.priced, full.missing], [3, 3, 0])
  assert.equal(full.coverage, 'On Ethereum, Holdings reads only ETH, WETH and USDC from the public network. To see every token, add a Zerion API key in the Holdings plugin settings in Hermes, then load tokens from Zerion on the Wallets page.')
  const base = { chain: 'Base', chainId: 8453, balances: [{ asset: 'ETH', units: '2' }, { asset: 'WETH', units: '0' }, { asset: 'USDC', units: '5' }] }
  const both = walletPricingNote({ ...point, prices: [...point.prices, { provider: 'Fixed USDC/USD (USDC counted at $1)', asset: 'USDC', chainId: 8453, price: '1' }] },
    [{ snapshot: eth }, { snapshot: base }], { zerionTokens: true })
  assert.equal(both.partial, true)
  assert.match(both.pricing, /Holdings has no price for ETH on Base yet, so it is left out of this total\.$/)
  assert.deepEqual([both.found, both.priced, both.missing], [5, 4, 1])
  assert.match(both.coverage, /On Base, Holdings reads only ETH, WETH and USDC from the public network\. Zerion could not load tokens for these wallets, so this total uses the short public list\./)
  const unpriced = walletPricingNote({ valueUsd: null }, [{ snapshot: eth }])
  assert.equal(unpriced.pricing, null)
  assert.match(unpriced.coverage, /^On Ethereum/)
  assert.equal(walletPricingNote({ valueUsd: null }, [{ snapshot: { chain: 'Solana', chainId: 'solana', balances: [] } }]).coverage, '')
  for (const text of [full.pricing, full.coverage, both.pricing, both.coverage]) assert.doesNotMatch(text, /—|;/)
})

test('Finish setup is one dismissible line with one button for the next optional step', () => {
  const { FinishSetupCard } = mod.namespace
  const calls = []
  const card = FinishSetupCard({ exchangeReady: false, alertReady: false, onKeys: () => calls.push('keys'), onAlert: () => calls.push('alert'), onDismiss: () => calls.push('dismiss') })
  assert.equal(card.props.className, 'finish-setup')
  assert.equal(card.props['aria-label'], 'Finish setup')
  const [line, action, close] = card.props.children
  assert.match(line.props.children, /exchange balances in this total/)
  assert.equal(close.props['aria-label'], 'Dismiss finish setup')
  action.props.onClick()
  close.props.onClick()
  const alertOnly = FinishSetupCard({ exchangeReady: true, alertReady: false, onKeys: () => calls.push('keys'), onAlert: () => calls.push('alert'), onDismiss() {} })
  assert.match(alertOnly.props.children[0].props.children, /Alert watch/)
  alertOnly.props.children[1].props.onClick()
  assert.deepEqual(calls, ['keys', 'dismiss', 'alert'])
  assert.equal(FinishSetupCard({ exchangeReady: true, alertReady: true }), null)
  assert.match(source, /const finishSetup = !demo && realSourceLoaded && !setupDismissed && setupStatus && jsx\(FinishSetupCard/)
  assert.ok(source.indexOf('      finishSetup,') > source.indexOf("jsx('p', { className: 'portfolio-total', children: totals.grandUsd"), 'finish setup sits below the total')
})

test('alert rows draw one continuous rule and Mark read keeps a full border', () => {
  assert.match(css, /\.holdings-shell \.watch-manual \{ align-items:center; border-block-start:1px solid var\(--activity-rule\)/)
  assert.match(css, /\.holdings\.holdings-shell \.watch-manual > button:first-child \{ min-width:0; border:0; border-radius:0; \}/)
})

test('wallet rows round units, show price, value and source, sort by value and mark unpriced tokens', () => {
  const { walletValueRows, formatUnits, formatPrice, priceSourceLabel, UNPRICED_TIP } = mod.namespace
  assert.equal(formatUnits('4774.551794456062094343'), '4,774.5518')
  assert.equal(formatUnits('0.000012345678'), '0.00001235')
  assert.equal(formatUnits('0.000000000000000000'), '0')
  assert.equal(formatPrice('2000.5'), '$2,000.50')
  assert.equal(formatPrice('0.0123456'), '$0.01235')
  const snapshot = { chain: 'Ethereum', chainId: 1, balances: [
    { asset: 'ETH', units: '2', contract: null }, { asset: 'LINK', units: '30', contract: '0xlink' },
    { asset: 'UNI', units: '5', contract: '0xuni' }, { asset: 'DAI', units: null, contract: '0xdai' },
    { asset: 'USDC', units: '10', contract: '0xusdc' }] }
  const point = { valueUsd: '470', prices: [
    { provider: 'DefiLlama price', asset: 'ETH', chainId: 1, price: '200' },
    { provider: 'DefiLlama price', asset: 'LINK', chainId: 1, price: '2' },
    { provider: 'Fixed USDC/USD (USDC counted at $1)', asset: 'USDC', chainId: 1, price: '1' },
    { provider: 'DefiLlama price', asset: 'UNI', chainId: 8453, price: '9' }] }
  const { rows, totalUsd } = walletValueRows(snapshot, point)
  assert.deepEqual(rows.map(item => item.row.asset), ['ETH', 'LINK', 'USDC', 'UNI', 'DAI'])
  assert.deepEqual(rows.map(item => item.valueUsd), [400, 60, 10, null, null])
  assert.deepEqual(rows.map(item => item.source), ['DefiLlama', 'DefiLlama', 'Counted at $1', null, null])
  assert.equal(totalUsd, 470)
  assert.equal(walletValueRows(snapshot, undefined).totalUsd, null)
  assert.equal(priceSourceLabel('GeckoTerminal WETH/USD (ETH priced at the WETH rate)'), 'WETH rate')
  assert.match(source, /children: 'No price yet' \}/)
  assert.match(source, /'Wallet total for priced tokens'/)
  assert.match(css, /\.holdings-shell \.desk-rows :is\(\.wallet-value-head,button\.wallet-value-row\) \{ display:grid; grid-template-columns:minmax\(0,1\.3fr\)/)
  assert.doesNotMatch(UNPRICED_TIP, /—|;/)
})

test('real wallet total uses the large style, a plain label, a pill and a plain sentence', () => {
  const { walletTotalMeta } = mod.namespace
  assert.match(source, /className: 'portfolio-hero wallet-total-hero'/)
  assert.match(source, /walletParts\.length && exchangeParts\.length \? 'Total value in USD'\n\s+: exchangeParts\.length \? 'Exchange value in USD' : 'Wallet value in USD'/)
  assert.match(source, /`\$\{totals\.missing\} \$\{totals\.missing === 1 \? 'token' : 'tokens'\} not priced`/)
  assert.doesNotMatch(source, /observed balance rows priced/)
  assert.doesNotMatch(source, /Partial wallet value/)
  const at = '2026-11-10T13:51:00Z'
  const when = new Date(at)
  const stamp = when.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' at ' + when.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
  assert.equal(walletTotalMeta({ found: 3, priced: 1, missing: 2 }, at), `Holdings priced 1 of the 3 tokens it found, updated ${stamp}.`)
  assert.equal(walletTotalMeta({ found: 3, priced: 3, missing: 0 }, at), `Every token Holdings found has a price, updated ${stamp}.`)
  assert.equal(walletTotalMeta({ found: 0, priced: 0, missing: 0 }, at), `Holdings found no token balances when it checked on ${stamp}.`)
  assert.equal(walletTotalMeta({ found: 2, priced: 2, missing: 0 }, 'not a date'), 'Every token Holdings found has a price.')
})

test('the total note is one sentence and the detail sits in a "How this is counted" disclosure', () => {
  const { countingDetails, CountingDetails, exchangeHoldings, NOT_COUNTED_NOTE, EXCHANGE_PRICING_NOTE } = mod.namespace
  assert.doesNotMatch(source, /wallet-pricing-note|exchange-pricing-note|wallet-coverage-note/)
  assert.match(source, /jsx\(CountingDetails, \{ groups: countingDetails\(pricingNote, exchangeParts\) \}\)/)
  const note = { labels: ['Prices for the public list come from DefiLlama, and Holdings uses a price only when DefiLlama reports it with high confidence in the last hour.'],
    coverageItems: ['On Ethereum, Holdings reads only ETH and USDC from the public network.',
      'Where Zerion tokens are loaded, this total uses Zerion’s token list in place of the short public list, so each token is counted once. DeFi positions and NFTs are listed on their own pages and are not added to this total.'],
    missingNames: ['LINK'] }
  const kraken = { ...exchangeHoldings('kraken', { balances: [{ asset: 'DOT', free: '40', locked: '0' }, { asset: 'ZUSD', free: '5', locked: '0' }],
    prices: { ZUSD: { price: '1', provider: 'US dollar cash' } } }), exchange: 'kraken' }
  const groups = countingDetails(note, [kraken])
  assert.deepEqual(groups.map(group => group.title), ['Prices', 'Tokens Holdings reads', 'Not in this total'])
  assert.ok(groups[0].items.includes(EXCHANGE_PRICING_NOTE))
  assert.equal(groups[2].items[0], 'Holdings has no price for LINK and DOT on Kraken yet, so they are left out.')
  assert.equal(groups[2].items[1], NOT_COUNTED_NOTE)
  const text = groups.flatMap(group => group.items).join(' ')
  assert.equal(text.match(/DeFi positions and NFTs/g).length, 1, 'the not-counted sentence appears once')
  assert.doesNotMatch(text, /—|;/)
  const tree = CountingDetails({ groups })
  assert.equal(tree.type, 'details')
  assert.equal(tree.props.children[0].props.children, 'How this is counted')
  assert.deepEqual(countingDetails(null, []).map(group => group.id), ['left-out'])
})

test('keyless help explains coverage, the Zerion and Etherscan keys, signup links and where keys go', () => {
  const { KEYLESS_HELP, KeylessHelp } = mod.namespace
  const text = Object.values(KEYLESS_HELP).join(' ')
  assert.match(text, /https:\/\/dashboard\.zerion\.io/)
  assert.match(text, /https:\/\/docs\.etherscan\.io\/set-up-your-api-key/)
  assert.match(KEYLESS_HELP.zerion, /every token.*DeFi positions and NFTs/)
  assert.match(KEYLESS_HELP.etherscan, /transaction history/)
  assert.match(KEYLESS_HELP.where, /Holdings plugin settings inside Hermes/)
  assert.doesNotMatch(text, /—|;/)
  assert.doesNotMatch(KEYLESS_HELP.covers, /^Read-only/i)
  const help = KeylessHelp()
  assert.equal(help.props.children[0].props.children, 'What Holdings reads without keys')
  assert.match(source, /\.\.\.walletPositions,\n    !demo && setupStatus\?\.zerionConfigured !== true && jsx\(KeylessHelp, \{\}\),/)
})

test('Zerion tokens replace the public rows so each holding counts once, and failures fall back', () => {
  const { walletHoldings, walletPricingNote } = mod.namespace
  const usdc = '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
  const snapshot = { chain: 'Ethereum', chainId: 1, balances: [
    { asset: 'ETH', contract: null, units: '2' }, { asset: 'USDC', contract: usdc, units: '10' }, { asset: 'LINK', contract: '0xlink', units: '3' }] }
  const point = { valueUsd: '4010', prices: [
    { provider: 'DefiLlama price', asset: 'ETH', chainId: 1, price: '2000' },
    { provider: 'DefiLlama price', asset: 'USDC', chainId: 1, price: '1' }] }
  const keyless = walletHoldings(snapshot, point, null)
  assert.equal(keyless.origin, 'public')
  assert.equal(keyless.totalUsd, 4010)
  const zerionRows = [
    { id: 'z1', asset: 'ETH', contract: null, units: '2', price: '2001' },
    { id: 'z2', asset: 'USDC', contract: usdc.toUpperCase().replace('0X', '0x'), units: '10', price: '1' },
    { id: 'z3', asset: 'PEPE', contract: '0xpepe', units: '1000', price: null },
    { id: 'z4', asset: 'AAVE', contract: '0xaave', units: '1', price: '90' }]
  const complete = walletHoldings(snapshot, point, { state: 'complete', rows: zerionRows })
  assert.equal(complete.origin, 'zerion')
  assert.deepEqual(complete.rows.map(item => item.row.asset), ['ETH', 'AAVE', 'USDC', 'PEPE'])
  assert.equal(complete.totalUsd, 4002 + 90 + 10)
  assert.equal(complete.rows.filter(item => item.row.asset === 'ETH').length, 1)
  assert.deepEqual(complete.rows.map(item => item.source), ['Zerion', 'Zerion', 'Zerion', null])
  const partial = walletHoldings(snapshot, point, { state: 'page-limit', rows: zerionRows.slice(0, 1) })
  assert.equal(partial.origin, 'mixed')
  assert.deepEqual(partial.rows.map(item => [item.row.asset, item.origin]), [['ETH', 'zerion'], ['USDC', 'public'], ['LINK', 'public']])
  assert.equal(partial.totalUsd, 4002 + 10)
  for (const failed of [{ state: 'rate-limited', rows: [] }, { state: 'invalid-key' }, undefined])
    assert.equal(walletHoldings(snapshot, point, failed).totalUsd, 4010)
  const solana = { chain: 'Solana', chainId: 'solana', balances: [{ asset: 'SOL', units: '1' }] }
  assert.equal(walletHoldings(solana, null, { state: 'complete', rows: zerionRows }).origin, 'public')
  const note = walletPricingNote(point, [{ snapshot }], { zerionTokens: true, reports: [{ state: 'complete', rows: zerionRows }] })
  assert.match(note.coverage, /uses Zerion’s token list in place of the short public list, so each token is counted once\. DeFi positions and NFTs are listed on their own pages and are not added to this total\./)
  assert.doesNotMatch(note.coverage, /On Ethereum, Holdings reads only/)
  assert.deepEqual([note.found, note.priced, note.missing], [4, 3, 1])
  assert.match(note.pricing, /Tokens from Zerion use Zerion’s verified prices/)
  assert.match(note.pricing, /Holdings has no price for PEPE yet, so it is left out of this total\.$/)
  assert.doesNotMatch(note.pricing + note.coverage, /—|;/)
})

test('exchange rows price with the exchange ticker and the grand total adds wallets and exchanges once', () => {
  const { exchangeHoldings, portfolioTotals, walletHoldings, EXCHANGE_PRICING_NOTE } = mod.namespace
  const result = { status: 'ready', balances: [
    { asset: 'BTC', free: '0.5', locked: '0.25' }, { asset: 'USDC', free: '10', locked: '0' },
    { asset: 'MYSTERY', free: '100', locked: '0' }, { asset: 'XXBT', free: null, locked: '1' }],
    prices: { BTC: { price: '100', provider: 'Binance BTC/USDC price' }, USDC: { price: '1', provider: 'USDC counted at $1' },
      XXBT: { price: '100', provider: 'Kraken BTC/USD price' } } }
  const exchange = exchangeHoldings('binance', result)
  assert.deepEqual(exchange.rows.map(item => [item.row.asset, item.valueUsd, item.source]),
    [['BTC', 75, 'Binance'], ['USDC', 10, 'Counted at $1'], ['MYSTERY', null, null], ['XXBT', null, null]])
  assert.equal(exchange.totalUsd, 85)
  assert.deepEqual([exchange.found, exchange.priced], [3, 2])
  const wallet = walletHoldings({ chain: 'Base', chainId: 8453, balances: [{ asset: 'ETH', contract: null, units: '1' }] },
    { prices: [{ provider: 'DefiLlama price', asset: 'ETH', chainId: 8453, price: '15' }] })
  const totals = portfolioTotals([wallet], [exchange])
  assert.deepEqual(totals, { walletsUsd: 15, exchangesUsd: 85, grandUsd: 100, found: 4, priced: 3, missing: 1 })
  assert.deepEqual(portfolioTotals([], []), { walletsUsd: null, exchangesUsd: null, grandUsd: null, found: 0, priced: 0, missing: 0 })
  assert.equal(portfolioTotals([], [exchange]).grandUsd, 85)
  assert.match(source, /jsxs\('dl', \{ className: 'portfolio-balance', 'aria-label': 'Total by source'/)
  assert.match(source, /\['Asset', 'Free', 'Locked', 'Price', 'Value'\]/)
  assert.doesNotMatch(EXCHANGE_PRICING_NOTE, /—|;/)
})

test('the page card reserves a band for the Ask button, so pages and evidence panels end above it', () => {
  // The button is 42px tall and sits 11px above the card bottom, so a 64px band holds it with room on both sides.
  assert.match(css, /\.holdings-shell \{ --ask-band:64px; \}/)
  assert.match(css, /\.grid:has\(> #holdings-chat\[hidden\]\) > \.center \{ padding-bottom:var\(--ask-band\); \}/)
  assert.match(css, /\.grid:has\(> #holdings-chat\[hidden\]\) > \.center > \.evidence \{ bottom:var\(--ask-band\); max-height:none; \}/)
  assert.match(css, /button\.ask-fab \{ position:absolute; right:14px; bottom:11px; z-index:35;/)
  assert.doesNotMatch(css, /button\.ask-fab \{ right:20px; bottom:20px; \}/)
  assert.match(css, /\.center:has\(> \.evidence\) > \.workspace-page \{ visibility:hidden; \}/)
})

test('starter questions launched with a selected record carry that record', () => {
  const starter = source.slice(source.indexOf('function askStarter(prompt)'), source.indexOf('function askStarter(prompt)') + 700)
  assert.match(starter, /DEMO_STARTER_QUESTIONS\[0\] && review && scopedRecord/)
  assert.match(starter, /recordMovementAnswer\(review, transferReview\?\.items, scopedRecord\)/)
  assert.match(starter, /question: recordStarterPrompt\(prompt, scopedRecord\)/)
  assert.match(source, /button\(demo && review \? recordStarterPrompt\(prompt, scopedRecord\) : prompt, \(\) => askStarter\(prompt\)/)
})
