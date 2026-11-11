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
  assert.doesNotMatch(value, /—|;/, value)
  for (const sentence of value.split(/(?<=[.?!])\s+/)) assert.ok(sentence.split(/\s+/).length > 3, 'no fragment sentences: ' + sentence)
}
const part = (rows, extra = {}) => ({ rows, totalUsd: rows.reduce((sum, item) => sum + (item.valueUsd ?? 0), 0),
  found: rows.length, priced: rows.filter(item => item.valueUsd != null).length, ...extra })
const held = (asset, units, valueUsd) => ({ row: { asset, units: String(units) }, price: valueUsd == null ? null : valueUsd / units, valueUsd })

test('each page has one intro and the old duplicate paragraphs are gone', () => {
  for (const old of ['Explore fictional transactions.', 'These are fictional wallet records.', 'These are fictional positions grouped by protocol.',
    'Check your tokens for scam warnings and apps that have permission', 'Alerts you check by hand stay in this workspace',
    'Trace money movements across the accounts and wallets in this review.', 'Add a second wallet to compare them in Portfolio.'])
    assert.ok(!source.includes(old), old)
  assert.match(css, /\.page-intro \{ max-width:74ch;/)
})

test('the demo hero says Total value in USD with a demo chip and the combined open adds no status line', () => {
  assert.ok(!source.includes('Priced total / Fictional USD'))
  assert.ok(source.includes("['Total value in USD ', statusChip('Fictional demo', 'info')]"))
  assert.ok(!source.includes('Combined fictional selection opened.'))
  assert.ok(!source.includes("notice && jsx('p', { role: 'status', children: notice })"))
  assert.match(source, /className: 'holdings-toast', role: 'status'/)
  assert.match(source, /setTimeout\(\(\) => setNotice\(''\), 9000\)/)
})

test('ledger rows keep the chart button in its own cell', () => {
  assert.match(css, /\.ledger-region:not\(\.activity-region \*\) \.ledger-line \{ grid-template-columns:minmax\(0,1fr\) 32px 76px; \}/)
  assert.match(css, /button\[data-chart-record\] \{ grid-column:3;/)
})

test('zero balances are hidden behind a quiet toggle', () => {
  assert.equal(ns.emptyBalance({ units: '0' }), true)
  assert.equal(ns.emptyBalance({ units: '0.5' }), false)
  assert.equal(ns.emptyBalance({ units: null }), false)
  assert.equal(ns.emptyTokensLabel(1), 'Show 1 token with no balance')
  assert.equal(ns.emptyTokensLabel(5), 'Show 5 tokens with no balance')
  assert.match(source, /valued\.rows\.filter\(\(\{ row \}\) => showEmptyTokens \|\| !emptyBalance\(row\)\)/)
})

test('scroll area clears the Ask pill and chat chips share one outlined style', () => {
  assert.match(css, /\.holdings\.holdings-shell \.workspace-page \{ padding-bottom:24px; scroll-padding-bottom:24px; \}/)
  assert.doesNotMatch(css, /#holdings-chat\[hidden\]\) \.workspace-page \{ padding-bottom:104px; \}/)
  assert.match(css, /\.holdings\.holdings-shell \.chat-suggestions button \{ border:1px solid var\(--control-border\); border-radius:999px; background:transparent;/)
})

test('starters follow what is loaded and each one has a local answer', () => {
  assert.deepEqual(ns.starterQuestions({ demo: true }), ['Where did my ETH go?', 'What is my exposure?', 'What fees were actually paid?'])
  assert.deepEqual(ns.starterQuestions({ unpriced: 0, exchangeReady: true }), ['What am I holding right now?'])
  assert.deepEqual(ns.starterQuestions({ unpriced: 2, exchangeReady: false }), ns.STARTER_QUESTIONS)
  const wallet = part([held('ETH', 2, 4000), held('LINK', 3, null)])
  const exchange = { ...part([held('XXBT', 0.5, 30000)]), exchange: 'kraken' }
  assert.equal(ns.krakenAssetName('ZUSD'), 'USD')
  assert.equal(ns.krakenAssetName('SOL'), 'SOL')
  const both = ns.holdingsStarterAnswer([wallet], [exchange])
  assert.match(both.text, /\$34,000\.00 in total/)
  assert.match(both.text, /\$4,000\.00 in wallets and \$30,000\.00 on exchanges/)
  assert.match(both.text, /1 token has no price yet/)
  assert.match(both.text, /BTC on Kraken \(\$30,000\.00\)/)
  const empty = ns.holdingsStarterAnswer([], [])
  assert.equal(empty.setupNeeded, true)
  const unpriced = ns.unpricedStarterAnswer({ missingNames: ['LINK'] }, [])
  assert.match(unpriced.text, /no price for LINK yet/)
  const exchangeHelp = ns.exchangeStarterAnswer()
  assert.match(exchangeHelp.text, /view-only API key/)
  for (const answer of [both, empty, unpriced, exchangeHelp]) copyRules(answer.text)
  assert.match(source, /starterQuestions\(\{ demo, unpriced: totals\.missing, exchangeReady \}\)/)
})

test('narrow layout shows a tab overflow cue and the Ask panel covers the whole view', () => {
  assert.match(source, /'data-more-right': navMore\.right \? 'true' : 'false'/)
  const narrow = css.slice(css.lastIndexOf('@container (max-width:720px)'))
  assert.match(narrow, /\.grid:has\(> #holdings-chat:not\(\[hidden\]\)\) \{ position:static; \}/)
  assert.match(narrow, /#holdings-chat:not\(\[hidden\]\) \{ position:absolute; inset:0;/)
})

test('Wallets with nothing loaded has no header bar and Finish setup copy follows the rules', () => {
  assert.match(source, /onSetupScreen = nothingLoaded && \(tab === 'Overview' \|\| tab === 'Wallets & DeFi' \|\| railKind === 'accounts'\)/)
  for (const flags of [{ exchangeReady: false, alertReady: false }, { exchangeReady: true, alertReady: false }])
    copyRules(ns.FinishSetupCard({ ...flags, onKeys() {}, onAlert() {}, onDismiss() {} }).props.children[0].props.children)
})

test('leftover 1: the donut center shows whole dollars on one baseline', () => {
  assert.equal(ns.donutCenterUsd(176250), '$176,250')
  assert.equal(ns.donutCenterUsd(176250.75), '$176,251')
  assert.equal(ns.donutCenterUsd(1234567), '$1.23M')
  assert.equal(ns.donutCenterUsd(12500000), '$12.5M')
  assert.equal(ns.donutCenterUsd('x'), '—')
  assert.match(source, /jsx\('strong', \{ className: 'allocation-total', children: donutCenterUsd\(total\) \}\)/)
  assert.match(css, /\.allocation-center \.allocation-total \{ font-size:clamp\(14px, 9\.5cqi, 17px\); font-weight:700; letter-spacing:-\.01em; line-height:1\.1; white-space:nowrap;/)
  assert.match(css, /\.allocation-donut \{ position:relative; width:min\(100%,176px\); container-type:inline-size; \}/)
})

test('leftover 2: the scroll area leaves room for the Ask pill', () => {
  const pill = /button\.ask-fab \{ position:absolute; right:14px; bottom:11px;/
  assert.match(css, pill)
  assert.match(css, /> \.center \{ padding-bottom:var\(--ask-band\); \}/)
  assert.doesNotMatch(css, /padding-bottom:88px/)
})

test('leftover 3: Esc closes the topmost layer from anywhere and returns focus to the pill', () => {
  assert.equal(ns.escapeTarget({ drawerOpen: true, menuOpen: true, chatOpen: true }), 'drawer')
  assert.equal(ns.escapeTarget({ menuOpen: true, chatOpen: true }), 'menu')
  assert.equal(ns.escapeTarget({ chatOpen: true }), 'chat')
  assert.equal(ns.escapeTarget({}), null)
  assert.match(source, /document\.addEventListener\('keydown', onKey\)\n    return \(\) => document\.removeEventListener\('keydown', onKey\)/)
  assert.match(source, /if \(event\.key !== 'Escape' \|\| event\.defaultPrevented\) return\n      const state = escapeState\.current/)
  assert.match(source, /else state\.closeChat\?\.\(\)/)
  assert.match(source, /function closeChat\(\{ returnFocus = true \} = \{\}\) \{\n    focusAskButton\.current = returnFocus/)
})

test('leftover 4: the wallet form has two short help lines', () => {
  assert.ok(!source.includes('This reads the main coin and supported tokens on that network.'))
  const lines = [...source.matchAll(/className: 'muted wallet-form-help', children: '([^']+)'/g)].map(match => match[1])
  assert.equal(lines.length, 2)
  for (const line of lines) copyRules(line)
  assert.match(lines.join(' '), /recovery phrase or password/)
  assert.match(lines.join(' '), /every token on Solana/)
})

test('leftover 5: toasts sit above the Ask pill and clear of the tab bar', () => {
  assert.match(css, /\.holdings\.holdings-shell \.holdings-toast \{ top:auto; bottom:80px; z-index:70; width:max-content; \}/)
  assert.match(css, /:has\(#holdings-chat:not\(\[hidden\]\)\) \.holdings-toast \{ left:calc\(\(100% - var\(--workspace-chat\)\) \/ 2\); \}/)
})
