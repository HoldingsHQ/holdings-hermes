// 0.1.9 eye-pass fixes: asset symbols stay whole, the section menu ignores a hidden page, and wallet Remove sits inside its box.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const source = fs.readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')

test('pairs and asset symbols never break inside the token', () => {
  // The app sets overflow-wrap:anywhere on .holdings, so a narrow Activity column split ETH/USDT into ETH/U and SDT.
  assert.match(source, /\.holdings \.asset-token \{ white-space:nowrap; overflow-wrap:normal; word-break:keep-all; \}/)
  assert.match(source, /className: 'col-asset', children: jsx\('strong', \{ className: 'asset-token', children: activitySymbol\(row\) \}\)/)
  assert.match(source, /jsx\('span', \{ className: 'asset-token', children: label\(row\.asset\) \}\)/)
  assert.match(source, /@container \(max-width:860px\) \{[^}]*\}\n  \.holdings-shell \.activity-table \.narrow-account \{ display:block; \}\n  \/\*[^*]*\*\/\n  \.holdings-shell \.activity-table :is\(th,td\) \{ padding-inline:10px; \}/)
  // The record list keeps its asset column at least as wide as the symbol, at full width and in the narrow layout.
  assert.match(source, /\.ledger-fields \{ display:grid; grid-template-columns:minmax\(82px,1fr\) minmax\(68px,1\.15fr\) minmax\(max-content,\.65fr\)/)
  assert.match(source, /\.ledger-fields \{ grid-template-columns:minmax\(0,1\.15fr\) minmax\(0,\.85fr\) minmax\(max-content,\.65fr\); gap:4px 8px; \}/)
})

test('the section menu keeps its choice while an evidence panel hides the page', () => {
  const handler = source.slice(source.indexOf("className: 'workspace-page', ref: pageScroll"), source.indexOf('setScrollSection(previous =>'))
  assert.match(handler, /if \(root\.inert \|\| !root\.getClientRects\(\)\.length\) return\n\s*const top = root\.getBoundingClientRect\(\)\.top \+ 70/)
})

test('wallet Remove sits inside its box, not on the border line', () => {
  assert.match(source, /button\.wallet-remove \{ position:absolute; top:4px; right:8px;/)
  assert.match(source, /\.wallet-inputs:has\(\.wallet-remove\) fieldset \{ padding-top:40px; \}/)
  assert.doesNotMatch(source, /wallet-remove \{ position:absolute; top:-13px/)
})
