import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
const source = fs.readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
const css = source.slice(source.indexOf('const css = `'), source.indexOf('\n`\n', source.indexOf('const css = `')))
const mod = new vm.SourceTextModule(source)
const element = (type, props, key) => ({ type, ...props, key })
const stub = values => new vm.SyntheticModule(Object.keys(values), function () { for (const [k, v] of Object.entries(values)) this.setExport(k, v) })
await mod.link(name => ({
  '@hermes/plugin-sdk': stub({ ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'nav', gatewayActivationEpoch: () => 0, host: {}, useValue() {} }),
  react: stub({ useEffect() {}, useRef: v => ({ current: v }), useState: v => [v, () => {}], useSyncExternalStore() {} }),
  'react/jsx-runtime': stub({ jsx: element, jsxs: element }),
})[name]); await mod.evaluate()
const ns = mod.namespace
const text = node => node == null || node === false ? '' : typeof node !== 'object' ? String(node)
  : Array.isArray(node) ? node.map(text).join(' ') : text(node.children)
const copyRules = value => {
  assert.doesNotMatch(value, /—|;/, value)
  for (const sentence of value.split(/(?<=[.?!])\s+/)) assert.ok(sentence.split(/\s+/).length > 3, 'no fragment sentences: ' + sentence)
}

test('every page leads with a plain intro and the glossary icon rows are gone', () => {
  assert.doesNotMatch(source, /helpRow\(/)
  for (const name of ['Overview', 'Activity', 'Wallets & DeFi', 'DeFi', 'Alerts', 'Security', 'Sources', 'Exports', 'Support', 'Settings']) {
    assert.ok(ns.PAGE_INTROS[name], name)
    copyRules(ns.PAGE_INTROS[name])
    assert.match(source, new RegExp(`pageIntro\\('${name.replace(/[&]/g, '\\$&')}'\\)`))
  }
  assert.equal(ns.pageIntro('DeFi').className, 'page-intro')
})

test('tabs group utility pages behind a divider and keep Settings in the demo', () => {
  assert.deepEqual(ns.NAV_MAIN, ['Overview', 'Activity', 'Wallets & DeFi', 'DeFi', 'Charts', 'Security', 'Alerts', 'Exports'])
  assert.deepEqual(ns.NAV_UTILITY, ['Sources', 'Support', 'Settings'])
  assert.doesNotMatch(source, /filter\(name => !demo \|\| name !== 'Settings'\)/)
  const narrow = css.slice(css.lastIndexOf('@container (max-width:800px)'))
  assert.match(narrow, /scroll-snap-type:x proximity/)
  assert.match(css, /workspace-topnav-wrap\[data-more-right=true\]::after \{ opacity:1; \}/)
  assert.match(source, /className: 'topnav-more'/)
})

test('Ask uses one button label, plain source labels and three starter questions', () => {
  assert.equal(ns.STARTER_QUESTIONS.length, 3)
  assert.doesNotMatch(source, /'Ask locally'|Local answer calculated from records|'Model answer: '|Chat options and data sharing/)
  for (const label of ['From your records', 'From the help guide', 'From public market data', 'Written by your Hermes model']) assert.ok(source.includes(`'${label}'`), label)
  assert.match(source, /'Ask about anything on this page\. Holdings answers from the records you have loaded and links to the evidence\.'/)
  assert.match(source, /children: 'Privacy and options'/)
})

test('empty states carry an action and Settings explains each optional key', () => {
  const empty = ns.EmptyState({ text: 'Nothing needs a decision right now.', action: 'Open Sources', onAction() {} })
  assert.match(text(empty), /Open Sources/)
  const card = ns.GetMoreCard({ status: { zerionConfigured: true }, onOpenKeys() {} })
  const all = text(card)
  assert.match(all, /Get more from Holdings/)
  assert.match(all, /dashboard\.zerion\.io/)
  assert.match(all, /docs\.etherscan\.io\/set-up-your-api-key/)
  assert.match(all, /trading and withdrawals turned off/)
  assert.match(all, /Added/)
  for (const guide of ns.KEY_GUIDES) { copyRules(guide.gain); guide.steps.forEach(step => assert.doesNotMatch(step, /—|;/)) }
  assert.match(text(ns.GetMoreCard({ status: null, demo: true, onOpenKeys() {} })), /You are in the demo/)
})

test('user-facing copy avoids EVM and SPL jargon', () => {
  const strings = source.match(/'[^'\n]*'|`[^`\n]*`/g).filter(item => /\s/.test(item))
  assert.deepEqual(strings.filter(item => /\bEVM\b|\bSPL\b/.test(item)), [])
  const guide = fs.readFileSync(new URL('../dashboard/support.json', import.meta.url), 'utf8')
  assert.doesNotMatch(guide, /\bEVM\b|\bSPL\b/)
})

test('light-mode controls use a 3:1 border, buttons are 32px tall and text is at least 12px', () => {
  assert.match(css, /--control-border:var\(--stroke\)/)
  assert.match(css, /--stroke:color-mix\(in srgb, var\(--ui-base\) 70%, var\(--ui-bg-elevated\)\)/)
  assert.match(css, /button:not\(\.activity-region button\):not\(\.info-tip-button\) \{ min-height:32px; \}/)
  const tail = css.slice(css.indexOf('/* UI polish pass'))
  assert.doesNotMatch(tail, /font-size:1[01]px/)
  assert.doesNotMatch(css.replace(/\.activity-region[^{]*\{[^}]*\}/g, ''), /font-size:10px; \}\n  \.holdings-shell \.market-strip-quotes small/)
})
