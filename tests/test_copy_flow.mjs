import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
const source = fs.readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
const mod = new vm.SourceTextModule(source)
let hooks = [], cursor = 0
const useState = initial => {
 const slot = cursor++
 if (!(slot in hooks)) hooks[slot] = typeof initial === 'function' ? initial() : initial
 return [hooks[slot], value => { hooks[slot] = typeof value === 'function' ? value(hooks[slot]) : value }]
}
const useRef = initial => { const slot = cursor++; return hooks[slot] ??= {current:initial} }
const element = (type, props, key) => ({type,props,key})
const stub = values => new vm.SyntheticModule(Object.keys(values), function () { for (const [k,v] of Object.entries(values)) this.setExport(k,v) })
await mod.link(name => ({
 '@hermes/plugin-sdk': stub({ ROUTES_AREA:'routes', SIDEBAR_NAV_AREA:'nav', gatewayActivationEpoch:()=>0, host:{}, useValue(){} }),
 react: stub({useEffect(){},useRef,useState,useSyncExternalStore(){}}),
 'react/jsx-runtime':stub({jsx:element,jsxs:element}),
})[name]); await mod.evaluate()
const {attentionBreakdown, coverageExplanation, streamCoverage} = mod.namespace

test('attention explains the distinct alert and record counts', () => {
 assert.equal(attentionBreakdown([{kind:'transfer'},{kind:'transfer'}], [{},{},{},{}]), '4 alert findings, 2 possible transfers')
 assert.equal(attentionBreakdown([{kind:'conflict'}], []), '1 record conflict')
 assert.equal(attentionBreakdown([{kind:'transfer'}], []), '1 possible transfer')
 assert.match(source, /name === 'Activity' && reviewCount > 0 && jsx\('span', \{ className: 'nav-count', 'aria-label': reviewCountText\(reviewCount\), children: reviewCount \}/)
 assert.match(source, /\['Needs your review', reviewCount\]/)
 assert.equal(mod.namespace.reviewCountText(3), '3 to review')
 assert.doesNotMatch(source, /to decide'/)
 assert.equal(mod.namespace.statusLabel('transfer', 'accepted'), 'Transfer confirmed')
 assert.equal(mod.namespace.statusLabel('transfer', 'rejected'), 'Not the same')
 assert.equal(mod.namespace.statusLabel('conflict', 'undone'), 'Needs review')
 assert.match(source, /`Decision history \/ \$\{packet\.decisionLog\.length\} \$\{packet\.decisionLog\.length === 1 \? 'event' : 'events'\}`/)
 assert.ok(source.indexOf('const reviewCount = ') < source.indexOf('const exportPreview = '), 'reviewCount is defined before Exports uses it')
})
test('coverage describes the selected dates without claiming complete history', () => {
 const item={requestedStart:'2026-01-01',requestedEnd:'2026-01-02',historyStatus:'block-partial'}
 assert.match(coverageExplanation(item), /2026-01-01 through 2026-01-02 UTC/)
 assert.match(coverageExplanation(item), /Some records could not be loaded/)
 assert.match(coverageExplanation(item), /cannot confirm that all activity/)
 assert.doesNotMatch(coverageExplanation({...item,historyStatus:'block-scoped'}), /Some records/)
 assert.equal(coverageExplanation({state:'Unavailable',historyStatus:'unavailable'}), 'Unavailable')
 assert.equal(streamCoverage({section:'txlist',pages:1,complete:false,gap:'failed-page',failedPages:[2]}),
 'Transactions: 1 page loaded. Some records could not be downloaded. History is incomplete. Pages not loaded: 2.')
 assert.match(streamCoverage({section:'tokentx',pages:2,complete:true}), /^Token transfers: 2 pages loaded/)
})
test('transfer decisions precede records and original evidence starts collapsed', () => {
 const panel=source.slice(source.indexOf('const transferPanel ='),source.indexOf('const fictionalHandoff ='))
 assert.ok(panel.indexOf("button('Confirm match'") < panel.indexOf("className: 'transfer-pair'"))
 assert.ok(panel.indexOf("button('Not the same'") < panel.indexOf("className: 'transfer-pair'"))
 assert.ok(panel.indexOf("button('Decide later'") < panel.indexOf("className: 'transfer-pair'"))
 assert.match(panel, /disabled: transferBusy \|\| !ownsTransfer/)
 assert.match(panel, /jsxs\('details', \{ children: \[jsx\('summary', \{ children: 'Original evidence'/)
})
test('Alerts lists available checks and has no peg section', () => {
 assert.match(source, /children: 'Available checks'/)
 assert.match(source, /title: 'Wallet security'/)
 assert.match(source, /title: 'Address poisoning'/)
 assert.match(source, /title: 'Risky outgoing recipients'/)
 assert.match(source, /title: 'Protocol hacks matching your DeFi positions'/)
 assert.match(source, /title: 'Exchange status'/)
 assert.match(source, /title: 'Price moves'/)
 assert.match(source, /watchRuleDefaults = \{ security: true, hacks: true, outflow: false, price: false, exchange: false, lookalike: false \}/)
 assert.match(source, /className: 'alert-check-state'/)
 assert.match(source, /'Source: ' \+ item.source/)
 assert.doesNotMatch(source, /The check is on|The check is off, and the source is/)
 assert.doesNotMatch(source, /Stablecoin peg checks|function checkPegs|heldStablecoins/)
})
test('DeFi and unavailable watch provide existing setup navigation', () => {
 const defi=source.slice(source.indexOf('const defi ='),source.indexOf('const settings ='))
 assert.match(defi, /jsx\(EmptyState, \{ text: 'Add a wallet to see money you have lent, borrowed or added to apps like Aave and Uniswap\.',\s*action: 'Add a wallet', onAction: \(\) => navigate\('Wallets & DeFi'\) \}\)/)
 assert.match(source, /key-source-unavailable' && button\('Open the setup guide', \(\) => \{ setHelpArticle\('holdings-key'\); navigate\('Support'\) \}/)
 assert.doesNotMatch(source, /Open Holdings plugin settings/)
 assert.match(source, /host.navigate\(keySettingsRoute\(profile\)\)/)
})
test('export preview keeps raw manifest in collapsed Advanced details', () => {
 assert.doesNotMatch(source, /children: 'Export details and missing information'/)
 assert.match(source, /jsxs\('details', \{ children: \[jsx\('summary', \{ children: 'Advanced export details' \}\),\s*jsx\('pre', \{ tabIndex: 0, children: manifest \}\)/)
 assert.match(source, /full source details, review decisions and missing information/)
 assert.match(source, /JSON.stringify\(combinedExport, null, 2\)/)
})


test('tooltips open by focus, hover and click, and close with Escape or focus leaving', () => {
 hooks=[]
 const render=()=>{ cursor=0; return mod.namespace.InfoTip({label:'Coverage'}) }
 let tree=render()
 assert.equal(tree.props.children[0].props['aria-label'],'About Coverage')
 assert.equal(tree.props.children[1],false)
 tree.props.children[0].props.onFocus()
 tree=render()
 assert.equal(tree.props.children[1].props.role,'tooltip')
 assert.equal(tree.props.children[0].props['aria-describedby'],tree.props.children[1].props.id)
 tree.props.children[0].props.onClick()
 tree.props.onMouseLeave({currentTarget:{contains:()=>false,ownerDocument:{}}})
 assert.equal(render().props.children[1].props.role,'tooltip')
 let stopped=false
 tree.props.onKeyDown({key:'Escape',stopPropagation(){stopped=true}})
 assert.equal(stopped,true)
 assert.equal(render().props.children[1],false)
 tree.props.onMouseEnter()
 assert.equal(render().props.children[1].props.role,'tooltip')
 tree.props.onMouseLeave({currentTarget:{contains:()=>true,ownerDocument:{}}})
 assert.equal(render().props.children[1].props.role,'tooltip')
 tree.props.onBlur({currentTarget:{contains:()=>false},relatedTarget:null})
 assert.equal(render().props.children[1],false)
})
test('general news and read findings do not inflate actionable alert counts', () => {
 const entries=[{id:'general',severity:'info'},{id:'warning',severity:'warning'},
  {id:'critical',severity:'critical'},{id:'read',severity:'high',read:true}]
 assert.deepEqual(mod.namespace.actionAlerts(entries).map(x=>x.id),['warning','critical'])
 assert.equal(entries.length,4)
 assert.match(source, /General news \(\$\{generalNews.length\}\)/)
})
test('alert toast auto dismisses in a corner and opens Alerts on request', () => {
 let opened=0
 const notice=mod.namespace.alertToast('Fictional finding',()=>opened++)
 assert.equal(notice.placement,'bottom-right')
 assert.equal(notice.durationMs,8000)
 assert.equal(notice.action.label,'Open Alerts')
 assert.equal(opened,0)
 notice.action.onClick()
 assert.equal(opened,1)
})
test('watch setup and help disclose storage and foreground requirements', () => {
 const guide=JSON.parse(fs.readFileSync(new URL('../dashboard/support.json',import.meta.url),'utf8'))
 assert.ok(guide.articles.some(x=>x.id==='background-watch' && /Hermes/.test(x.answer)))
 assert.ok(source.includes("'Alert watch is locked until Hermes can read your Holdings key. Open the setup guide to add it.'"))
 assert.match(source,/Advanced script setup/)
 assert.match(source,/onScroll: event =>/)
 assert.match(source,/getAttribute\('data-label'\)/)
})

test('key settings link opens the agent schema and carries the selected profile', () => {
 assert.equal(mod.namespace.keySettingsRoute('qa sample'), '/settings?tab=plugins&agent=holdings&profile=qa%20sample')
 assert.equal(mod.namespace.keySettingsRoute(null), '/settings?tab=plugins&agent=holdings')
})

test('setup displays three actionable steps with independently observed progress', () => {
 let action = ''
 const result = mod.namespace.SetupHoldings({ walletReady: false, exchangeReady: true, alertReady: false,
   onWallet: () => { action = 'wallet' }, onKeys: () => { action = 'keys' },
   onAlert: () => { action = 'alert' }, onDemo: () => { action = 'demo' } })
 const [title, , steps, demo] = result.props.children
 assert.equal(title.props.children, 'Set up Holdings')
 assert.equal(steps.props.children.length, 3)
 for (const [index, name] of ['wallet', 'keys', 'alert'].entries()) {
   const [heading, , button] = steps.props.children[index].props.children
   assert.equal(heading.props.children[1].props.children, index === 1 ? 'Done' : 'Not done')
   button.props.onClick()
   assert.equal(action, name)
 }
 assert.equal(demo.props.children, 'Just looking? Try the demo')
 demo.props.onClick()
 assert.equal(action, 'demo')
})
