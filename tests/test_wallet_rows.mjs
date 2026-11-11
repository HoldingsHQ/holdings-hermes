import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'

// Wallet rows for 0.1.4: up to five addresses and twelve address and network pairs. Fictional data only.
const source = fs.readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
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
  assert.doesNotMatch(value, /—|;|·|\bledger\b|\bEVM\b/i, value)
  for (const sentence of value.split(/(?<=[.?!])\s+/)) assert.ok(sentence.split(/\s+/).length > 3, 'no fragment sentences: ' + sentence)
}
const evm = digit => '0x' + digit.repeat(40)

test('the form starts with one wallet and adds rows up to five', () => {
  assert.equal(ns.MAX_WALLET_ROWS, 5)
  assert.equal(ns.MAX_WALLET_PAIRS, 12)
  let rows = [ns.walletRow()]
  for (let count = 0; count < 8; count += 1) rows = ns.addWalletRow(rows)
  assert.equal(rows.length, 5)
  assert.equal(new Set(rows.map(row => row.id)).size, 5)
  assert.ok(rows.every(row => row.address === '' && Array.isArray(row.chains)))
  assert.strictEqual(ns.addWalletRow(rows), rows)
})

test('every row but the first can be removed and the others keep their values', () => {
  const rows = [1, 2, 3].reduce(list => ns.addWalletRow(list), [ns.walletRow()])
    .map((row, index) => ({ ...row, address: evm(String(index + 1)) }))
  assert.equal(ns.removeWalletRow(rows, rows[0].id).length, 4)
  const without = ns.removeWalletRow(rows, rows[2].id)
  assert.deepEqual(without.map(row => row.address), [evm('1'), evm('2'), evm('4')])
  assert.deepEqual(ns.removeWalletRow(rows, -1), rows)
})

test('a new row only ticks networks that still fit under the pair cap', () => {
  const all = [1, 8453, 42161, 56, 4663]
  const rows = [{ id: 1, address: evm('1'), chains: all }, { id: 2, address: evm('2'), chains: all }, { id: 3, address: evm('3'), chains: [1] }]
  assert.equal(ns.walletPairs(rows).length, 11)
  assert.deepEqual(ns.addWalletRow(rows).at(-1).chains, [1])
  const full = [...rows.slice(0, 2), { ...rows[2], chains: [1, 8453] }]
  assert.deepEqual(ns.addWalletRow(full).at(-1).chains, [])
  assert.deepEqual(ns.addWalletRow([ns.walletRow()]).at(-1).chains, [1, 8453])
})

test('five addresses at the cap make twelve pairs and one more pair is too many', () => {
  const rows = [
    { id: 1, address: evm('1'), chains: [1, 8453, 42161] }, { id: 2, address: evm('2'), chains: [1, 8453, 56] },
    { id: 3, address: evm('3'), chains: [1, 8453] }, { id: 4, address: evm('4'), chains: [1, 8453, 4663] },
    { id: 5, address: '1'.repeat(31) + '2', chains: [1, 8453] },
  ]
  assert.equal(ns.walletPairs(rows).length, 12)
  assert.equal(ns.walletPairs([...rows.slice(0, 4), { ...rows[4], address: evm('5'), chains: [1, 8453] }]).length, 13)
  assert.equal(ns.walletPairs([...rows.slice(0, 4), { id: 9, address: '', chains: [1, 8453] }]).length, 11)
})

test('labels follow the address row, not the pair position', () => {
  const pairs = [{ address: evm('a'), chainId: 1 }, { address: evm('a'), chainId: 8453 }, { address: evm('B'), chainId: 1 },
    { address: '1'.repeat(31) + '2', chainId: 'solana' }, { address: evm('b'), chainId: 56 }]
  assert.deepEqual(ns.walletLabels(pairs), ['Wallet 1', 'Wallet 1', 'Wallet 2', 'Wallet 3', 'Wallet 2'])
  assert.ok(!source.includes('`Wallet ${index + 1} / ${snapshot.chain}`'))
  assert.ok(!source.includes("'Wallet 2 (optional)'"))
  assert.match(source, /legend', \{ children: index \? `Wallet \$\{index \+ 1\} \(optional\)` : 'Wallet 1' \}/)
  assert.match(source, /index > 0 && button\('Remove'/)
  assert.match(source, /'aria-label': `Remove wallet \$\{index \+ 1\}`/)
  assert.match(source, /button\('Add another wallet'[\s\S]{0,160}disabled: walletInputs\.length >= MAX_WALLET_ROWS/)
  assert.match(source, /\}, id\)\n\s+\}\) \}\),/)
  assert.match(source, /name: part\.label \?\? `Wallet \$\{slot \+ 1\}`/)
  assert.match(source, /place: walletParts\.length > 1 \? `in \$\{part\.label/)
})

test('the new wallet copy follows the copy rules', () => {
  const texts = [
    `One address can cover several networks, so tick each one you use. You can add up to ${ns.MAX_WALLET_ROWS} addresses, and Holdings reads up to ${ns.MAX_WALLET_PAIRS} address and network pairs at a time. A Solana address always reads Solana only.`,
    `You have added the most addresses Holdings reads at once, which is ${ns.MAX_WALLET_ROWS}.`,
    `12 of ${ns.MAX_WALLET_PAIRS} address and network pairs selected. Untick a network before you choose another one.`,
    `Holdings reads up to ${ns.MAX_WALLET_PAIRS} address and network pairs at a time. Untick a network or remove a wallet.`,
    'Watching 3 address and network pairs.',
  ]
  for (const text of texts) {
    copyRules(text)
    const pattern = text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\b5\b/g, '\\$\\{MAX_WALLET_ROWS\\}')
      .replace(/^12 of/, '\\$\\{walletPairs\\(walletInputs\\)\\.length\\} of').replace(/\b12\b/g, '\\$\\{MAX_WALLET_PAIRS\\}')
      .replace('Watching 3 address and network pairs', 'Watching \\$\\{watch\\.walletCount\\} address and network \\$\\{watch\\.walletCount === 1 \\? \'pair\' : \'pairs\'\\}')
    assert.match(source, new RegExp(pattern), text)
  }
  assert.ok(!source.includes('clear the second wallet'))
  assert.ok(!source.includes('watched public wallet(s)'))
})

test('setup box 3 is tagged optional and advanced and opens the setup guide', () => {
  const calls = []
  const tree = ns.SetupHoldings({ onDemo() {}, onWallet() {}, onKeys() {}, onAlert: () => calls.push('guide') })
  const flat = node => node == null || typeof node !== 'object' ? [] : Array.isArray(node) ? node.flatMap(flat) : [node, ...flat(node.children)]
  const nodes = flat(tree)
  const tag = nodes.filter(node => node.className === 'setup-step-tag')
  assert.deepEqual(tag.map(node => node.children), ['Optional, advanced setup'])
  const titles = nodes.filter(node => node.type === 'h2').map(node => node.children)
  assert.deepEqual(titles, ['1. Add a public wallet', '2. Add exchange keys (optional)', '3. Turn on Alert watch'])
  const guide = nodes.find(node => node.type === 'button' && node.children === 'Open the setup guide')
  guide.onClick()
  assert.deepEqual(calls, ['guide'])
  copyRules(ns.SETUP_COPY.alerts)
  assert.match(ns.SETUP_COPY.alerts, /Holdings key/)
  assert.doesNotMatch(ns.SETUP_COPY.alerts, /plugin settings/)
  assert.match(source, /const openHoldingsKeyGuide = \(\) => \{ setHelpArticle\('holdings-key'\); navigate\('Support'\) \}/)
  assert.equal(source.match(/onAlert: openHoldingsKeyGuide/g).length, 2)
})

test('the Holdings key article renders each copy-paste block and follows the copy rules', () => {
  const guide = JSON.parse(fs.readFileSync(new URL('../dashboard/support.json', import.meta.url), 'utf8'))
  const article = guide.articles.find(item => item.id === 'holdings-key')
  for (const paragraph of article.answer.split('\n\n')) copyRules(paragraph)
  const tree = ns.SupportPage({ guide, selected: 'holdings-key', onNavigate() {}, onAsk() {} })
  const flat = node => node == null || typeof node !== 'object' ? [] : Array.isArray(node) ? node.flatMap(flat) : [node, ...flat(node.children)]
  const blocks = flat(tree).filter(node => node.type === 'pre')
  assert.equal(blocks.length, article.commands.length)
  assert.deepEqual(blocks.map(node => node.children.children), article.commands.map(item => item.code))
  for (const item of article.commands) copyRules(item.label.replace(/^(Mac|Linux) step \d\. /, ''))
  const wallets = guide.articles.find(item => item.id === 'wallets').answer
  assert.match(wallets, /up to five addresses/)
  assert.doesNotMatch(wallets, /up to two/)
})

test('Alert watch polls every five minutes and the copy states the per-check schedule', () => {
  assert.match(source, /const WATCH_POLL_MS = 300_000/)
  assert.doesNotMatch(source, /about once a minute while Hermes is open|check about once a minute/)
  assert.ok(source.includes('Turn on Alert watch to check about every 5 minutes while Hermes is open. Outflows are checked every 5 minutes and the other checks every 15 minutes.'))
  const guide = JSON.parse(fs.readFileSync(new URL('../dashboard/support.json', import.meta.url), 'utf8'))
  const watch = guide.articles.find(item => item.id === 'background-watch')
  assert.match(watch.answer, /about every 5 minutes/)
  assert.match(watch.answer, /Outflows are checked every 5 minutes and the other checks every 15 minutes\./)
  assert.match(watch.answer, /refresh every 3 hours/)
  assert.doesNotMatch(watch.answer + watch.keywords, /once a minute|every minute/)
})

test('the three approved Holdings key lines appear exactly and point to the setup guide', () => {
  const lines = [
    'Alert watch saves your alert rules and history on this computer, locked with a private encryption key called the Holdings key. The setup guide shows how to make that key and keep it in your Mac Keychain or another secure store that Hermes reads. Then turn on Alert watch on the Alerts page.',
    'Alert watch is locked until Hermes can read your Holdings key. Open the setup guide to add it.',
    "The Holdings key isn\\'t entered here. Follow the setup guide to keep it in your Keychain or 1Password.",
  ]
  for (const line of lines) assert.equal(source.split(line).length, 2, line)
  for (const line of lines) assert.doesNotMatch(line, /;|—|·/)
  const keys = source.slice(source.indexOf("The Holdings key isn\\'t entered here."))
  assert.match(keys.slice(0, 260), /button\('Open the setup guide', \(\) => \{ setHelpArticle\('holdings-key'\); navigate\('Support'\) \}/)
  assert.doesNotMatch(source, /Watching is off\. Set up your Holdings key|Open Holdings plugin settings|follow the saved-review guide to set up your Holdings key/)
})

test('long setup commands scroll inside their block instead of widening the Support article', () => {
  assert.match(source, /\.support-page > \.support-article \{ min-width:0; \}/)
  assert.match(source, /pre\.support-command \{[^}]*overflow-x:auto/)
})

test('a locked Alert watch names what is missing and offers Check again', () => {
  const approved = 'Alert watch is locked until Hermes can read your Holdings key. Open the setup guide to add it.'
  assert.equal(ns.lockedWatchText(undefined), approved)
  assert.equal(ns.lockedWatchText('not-set-up'), approved)
  assert.equal(ns.lockedWatchText('anything else'), approved)
  const restart = ns.lockedWatchText('restart-needed')
  assert.match(restart, /Restart the Hermes gateway/)
  assert.match(restart, /Check again/)
  assert.match(ns.lockedWatchText('wrong-format'), /not in the right format/)
  for (const reason of ['restart-needed', 'wrong-format', 'not-set-up']) copyRules(ns.lockedWatchText(reason))
  assert.match(source, /watch\?\.status === 'key-source-unavailable' \? lockedWatchText\(watch\.reason\)/)
  assert.match(source, /watch\?\.status === 'key-source-unavailable' && button\('Check again', \(\) => requestWatch\(\{ action: 'read' \}\)/)
})

test('the fictional combined review uses the bundled Binance and Bybit CSV snapshots', () => {
  assert.match(source, /\['kraken-spot', 'ethereum', 'binance-spot', fictional \? 'bybit-csv' : 'bybit-spot'\]/)
  assert.match(source, /const bundled = sample\.exchange_demo\?\.\[mode\]/)
  assert.match(source, /if \(bundled\?\.mode === mode && Array\.isArray\(bundled\.rows\)\)/)
})
