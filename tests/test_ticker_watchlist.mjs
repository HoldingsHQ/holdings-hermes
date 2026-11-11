import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'

const source = fs.readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
const module = new vm.SourceTextModule(source)
const stub = values => new vm.SyntheticModule(Object.keys(values), function () {
  for (const [name, value] of Object.entries(values)) this.setExport(name, value)
})
const element = (type, props) => ({ type, ...props })
let chartHooks
await module.link(name => ({
  '@hermes/plugin-sdk': stub({ ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'nav', gatewayActivationEpoch: () => 0, host: {}, useValue() {} }),
  react: stub({ useEffect() {}, useRef: value => ({ current: value }), useState: value => chartHooks ? chartHooks(value) : [value, () => {}], useSyncExternalStore() {} }),
  'react/jsx-runtime': stub({ jsx: element, jsxs: element }),
})[name])
await module.evaluate()
const { tickerWatchlist, watchMarketId, MarketStrip, MarketChart, fitTickerWindow } = module.namespace
const addedMarket = 'ADA'

test('ordered watchlist survives reconstruction and stays separate per profile', () => {
  const values = new Map()
  const storage = { get: (key, fallback) => values.get(key) ?? fallback, set: (key, value) => values.set(key, value) }
  const store = tickerWatchlist(storage)
  let changes = 0
  store.subscribe(() => changes++)
  const defaults = store.read('profile-a')
  assert.equal(defaults.length, 4)
  const custom = watchMarketId(addedMarket)
  store.write('profile-a', ['SOLUSD', custom, 'XXBTZUSD', 'SOLUSD', 'https://untrusted.example'])
  assert.deepEqual(tickerWatchlist(storage).read('profile-a'), ['SOLUSD', custom, 'XXBTZUSD'])
  assert.deepEqual(tickerWatchlist(storage).read('profile-b'), defaults)
  assert.equal(changes, 1)
  store.write('profile-a', [])
  assert.deepEqual(tickerWatchlist(storage).read('profile-a'), [])
  assert.equal(MarketStrip({ entries: {}, open() {}, ids: [] }), null)
})

test('watchlist accepts provider-style IDs and drops old contract IDs', () => {
  assert.equal(watchMarketId('ADA'), 'kraken:ADA')
  assert.equal(watchMarketId('ADA?unsafe'), null)
  assert.equal(watchMarketId('XBT'), null)
  assert.equal(watchMarketId('onchain:base:0x' + 'ab'.repeat(20)), null)
})

test('ticker renders selected quotes in order and opens the matching listed market', () => {
  const id = watchMarketId(addedMarket)
  const at = new Date().toISOString()
  let opened
  const strip = MarketStrip({ ids: ['BNBUSD', id], entries: {
    [id]: { data: { status: 'ready', market: id,
      candles: [[at, '1', '1', '1', '0.000001']], closedAt: at, fetchedAt: at } },
  }, open(chart) { opened = chart } })
  const [quotes, duplicate] = strip.children[1].children.children
  assert.deepEqual(quotes.children.map(button => button.children[0].children), ['BNB/USD', 'ADA/USD'])
  assert.equal(quotes.children[1].children[1].children, '$0.000001')
  assert.equal(duplicate['aria-hidden'], true, 'copies fill the bar at rest and stay hidden from assistive tech')
  assert.doesNotMatch(JSON.stringify(quotes), /24h close|Stale/)
  quotes.children[1].onClick()
  assert.equal(opened.market, id)
  assert.equal(opened.route, '/market/kraken-spot/ADA')
})

test('price plot itself supports hover, tap and bounded keyboard inspection across missing hours', () => {
  const times = ['2026-01-01T00:00:00Z', '2026-01-01T01:00:00Z', '2026-01-01T08:00:00Z']
  const chart = { market: 'demo', name: 'Fictional market', provider: 'Fictional feed' }
  const data = { status: 'ready', market: chart.market, closedAt: '2026-01-01T09:00:00Z', fetchedAt: times[2],
    candles: times.map((at, i) => [at, '1', '4', '1', String(i + 1)]) }
  const state = []
  let chartStyle = 'Line'
  function renderChart() {
    let hook = 0
    chartHooks = initial => {
      const i = hook++
      if (!(i in state)) state[i] = initial
      return [state[i], next => { state[i] = next }]
    }
    try {
      return MarketChart({ chart, entry: { data }, refresh() {}, chartStyle, setChartStyle: next => { chartStyle = next } })
    } finally {
      chartHooks = undefined
    }
  }
  const render = () => renderChart().children.find(child => child?.className === 'chart-plot').children[1]
  const styleButtons = () => renderChart().children.find(child => child?.className === 'market-chart-controls').children[0].children
  const pointer = { clientX: 155, currentTarget: { getBoundingClientRect: () => ({ left: 100, width: 100 }) } }
  let plot = render()
  assert.equal(plot.tabIndex, 0)
  assert.equal(plot['aria-valuenow'], 2)
  plot.onPointerMove({ ...pointer, pointerType: 'mouse' })
  plot = render()
  assert.equal(plot['aria-valuenow'], 1, 'nearest candle is based on time, not row count')
  assert.match(plot['aria-valuetext'], /01:00 UTC, close \$2\.00/)
  assert.equal(plot.children[1].children[0].children, '$2.00')
  assert.equal(plot.children[1].children[1].dateTime, times[1])
  styleButtons()[1].onClick()
  assert.equal(styleButtons()[1]['aria-pressed'], true)
  const candlePlot = render()
  assert.equal(candlePlot['aria-valuenow'], 1, 'style change retains the inspected hour')
  assert.equal(candlePlot.children[0].children[4].length, 3)
  assert.equal(candlePlot.children[0].children[4][1].children[1].type, 'rect')
  styleButtons()[0].onClick()
  const series = render().children[0].children[4]
  assert.equal(series.className, 'chart-series')
  assert.equal(series.children[2].type, 'path')
  assert.equal(series.children[2].className, 'chart-line')
  assert.equal(series.children[1].className, 'chart-fill')
  assert.match(series.children[2].d, /^M/ )
  plot.onPointerMove({ ...pointer, clientX: 100, pointerType: 'touch' })
  assert.equal(render()['aria-valuenow'], 1, 'touch scrolling does not inspect a candle')
  plot.onClick({ ...pointer, clientX: 100 })
  assert.equal(render()['aria-valuenow'], 0)

  let prevented = 0
  for (const [key, expected] of [['ArrowLeft', 0], ['ArrowRight', 1], ['End', 2], ['ArrowRight', 2], ['Home', 0], ['Tab', 0]]) {
    render().onKeyDown({ key, preventDefault() { prevented++ } })
    assert.equal(render()['aria-valuenow'], expected, key)
  }
  assert.equal(prevented, 5, 'Tab can leave the chart')
})

test('resting ticker hides quotes cut by the window edge and shows them all while moving', () => {
  const quote = (left, right) => { const attrs = new Set(); return { attrs, getBoundingClientRect: () => ({ left, right }),
    toggleAttribute: (name, on) => on ? attrs.add(name) : attrs.delete(name) } }
  const quotes = [quote(100, 230), quote(238, 380), quote(388, 520), quote(528, 660)]
  const attrs = new Set()
  const view = { attrs, getBoundingClientRect: () => ({ left: 100, right: 540 }), querySelectorAll: () => quotes,
    toggleAttribute: (name, on) => on ? attrs.add(name) : attrs.delete(name) }
  fitTickerWindow(view, true)
  assert.deepEqual(quotes.map(item => item.attrs.has('data-ticker-hidden')), [false, false, false, true])
  assert.ok(view.attrs.has('data-fitted'))
  fitTickerWindow(view, false)
  assert.deepEqual(quotes.map(item => item.attrs.has('data-ticker-hidden')), [false, false, false, false])
  assert.ok(!view.attrs.has('data-fitted'))
  assert.equal(fitTickerWindow(undefined, true), undefined)
  assert.equal(fitTickerWindow(null, true), undefined)
})

test('ticker styles drop the fade at rest and keep a hidden quote reachable by keyboard', () => {
  assert.match(source, /\.market-strip-window\[data-fitted\] \{ -webkit-mask-image:none; mask-image:none; \}/)
  assert.match(source, /\.market-strip-window:not\(:focus-within\) \[data-ticker-hidden\] \{ visibility:hidden; \}/)
  assert.match(source, /ref: windowRef/)
  // The pressed Auto-scroll label takes the selected text color, not the page ink on the selected fill.
  assert.match(source, /\.market-strip-motion\[aria-pressed=true\] small \{ color:inherit; \}/)
})

test('a pause halfway through a quote moves the loop to the nearest quote start', () => {
  let shift = 70
  const animation = { currentTime: 0, effect: { getComputedTiming: () => ({ duration: 24000 }) } }
  const group = { querySelectorAll: () => [0, 130, 270].map(left => ({ getBoundingClientRect: () => ({ left: 100 - shift + 4 + left }) })) }
  const track = { children: [group, {}], firstElementChild: group, getAnimations: () => [animation],
    getBoundingClientRect: () => ({ left: 100 - shift, width: 800 }) }
  const view = { firstElementChild: track, getBoundingClientRect: () => ({ left: 100, right: 300 }), querySelectorAll: () => [], toggleAttribute() {} }
  fitTickerWindow(view, true)
  assert.equal(animation.currentTime, 130 / 400 * 24000)
  animation.currentTime = 'untouched'
  shift = 0
  fitTickerWindow(view, true)
  assert.equal(animation.currentTime, 'untouched')
  shift = 380
  fitTickerWindow(view, true)
  assert.equal(animation.currentTime, 0)
  animation.currentTime = 'untouched'
  shift = 70
  fitTickerWindow(view, false)
  assert.equal(animation.currentTime, 'untouched')
})
