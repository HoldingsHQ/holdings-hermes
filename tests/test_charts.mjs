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
const hooks = []
let hook = 0
await module.link(name => ({
  '@hermes/plugin-sdk': stub({ ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'nav', gatewayActivationEpoch: () => 0, host: {}, useValue() {} }),
  react: stub({
    useEffect() {},
    useRef: value => ({ current: value }),
    useState: value => {
      const i = hook++
      if (!(i in hooks)) hooks[i] = typeof value === 'function' ? value() : value
      return [hooks[i], next => { hooks[i] = typeof next === 'function' ? next(hooks[i]) : next }]
    },
    useSyncExternalStore() {},
  }),
  'react/jsx-runtime': stub({ jsx: element, jsxs: element }),
})[name])
await module.evaluate()
const { chartCurves, moneyFigure, chartAxisUsd, sampleHistoryWindow, AllocationDonut, MarketChart } = module.namespace

test('chartCurves builds a smooth cubic path and a closed fill', () => {
  const two = chartCurves([[0, 10], [20, 4]], 20)
  assert.match(two.line, /^M0 10L20 4$/)
  assert.match(two.area, /L20 20L0 20Z$/)
  const many = chartCurves([[0, 10], [10, 8], [20, 2], [30, 6]], 20)
  assert.match(many.line, /^M0 10C/)
  assert.equal((many.line.match(/C/g) || []).length, 3)
  assert.match(many.area, /L30 20L0 20Z$/)
  assert.deepEqual(chartCurves([], 20), { line: '', area: '' })
})

test('moneyFigure splits dollars and cents for chart totals', () => {
  const tree = moneyFigure(164250)
  assert.equal(tree.className, 'chart-money')
  assert.equal(tree.children[0].children, '$')
  assert.equal(tree.children[1].children, '164,250')
  assert.equal(tree.children[2].type, 'sup')
  assert.equal(tree.children[2].children, '00')
  assert.equal(moneyFigure(0.0004).startsWith('$'), true)
})

test('chartAxisUsd keeps price-axis labels on one line', () => {
  assert.equal(chartAxisUsd(164250), '$164,250')
  assert.equal(chartAxisUsd(64_249.74), '$64,250')
  assert.equal(chartAxisUsd(1.23), '$1.23')
  assert.equal(chartAxisUsd(Number.NaN), '')
})

test('sampleHistoryWindow keeps a 7-day slice without dropping the series', () => {
  const values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18]
  assert.deepEqual(sampleHistoryWindow(values, 'All'), values)
  assert.deepEqual(sampleHistoryWindow(values, '1M'), values)
  const week = sampleHistoryWindow(values, '7D')
  assert.equal(week.length, 4)
  assert.deepEqual(week, [15, 16, 17, 18])
  assert.equal(sampleHistoryWindow([9, 10], '7D').length, 2)
})

test('AllocationDonut puts the total in the center and lists value with percent', () => {
  hook = 0
  const tree = AllocationDonut({
    items: [['ETH', 76800], ['WBTC', 34000], ['USDC', 22650]],
    total: 133450,
    label: 'total',
  })
  assert.equal(tree.className, 'allocation-card')
  const center = tree.children[0].children[1]
  assert.equal(center.className, 'allocation-center')
  assert.equal(center.children[0].className, 'allocation-total')
  assert.equal(center.children[0].children, '$133,450')
  assert.equal(center.children[1].children, 'total')
  const rows = tree.children[1].children
  assert.equal(rows.length, 3)
  assert.equal(rows[0].children[1].children[0].children, 'ETH')
  assert.equal(rows[0].children[1].children[1].children, '58%')
  assert.match(rows[0].children[2].children, /\$76,800/)
})

test('MarketChart line series uses a gradient fill and keeps keyboard inspection', () => {
  hook = 0
  hooks.length = 0
  const times = ['2026-01-01T00:00:00Z', '2026-01-01T01:00:00Z', '2026-01-01T08:00:00Z']
  const chart = { market: 'demo', name: 'Fictional market', provider: 'Fictional feed', description: 'Fictional market' }
  const data = { status: 'ready', market: chart.market, closedAt: '2026-01-01T09:00:00Z', fetchedAt: times[2],
    candles: times.map((at, i) => [at, '1', '4', '1', String(i + 1)]) }
  let style = 'Line'
  const tree = () => { hook = 0; return MarketChart({ chart, entry: { data }, refresh() {}, chartStyle: style, setChartStyle: next => { style = next } }) }
  const plot = () => tree().children.find(child => child?.className === 'chart-plot').children[1]
  const series = plot().children[0].children.find(node => node?.className === 'chart-series')
  assert.ok(series)
  assert.equal(series.children.find(node => node.className === 'chart-line').type, 'path')
  assert.equal(series.children.find(node => node.className === 'chart-fill').type, 'path')
  const pointer = { clientX: 155, currentTarget: { getBoundingClientRect: () => ({ left: 100, width: 100 }) } }
  plot().onPointerMove({ ...pointer, pointerType: 'mouse' })
  assert.equal(plot()['aria-valuenow'], 1)
  assert.equal(plot().children[1].children[0].children, '$2.00')
  plot().onKeyDown({ key: 'Home', preventDefault() {} })
  assert.equal(plot()['aria-valuenow'], 0)
  plot().onPointerLeave()
  assert.equal(plot()['aria-valuenow'], 2, 'leave returns the crosshair to the latest point')
})
