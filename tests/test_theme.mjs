import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const source = fs.readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
const css = source.match(/const css = `([\s\S]*?)`/)[1]
function block(selector) {
  const start = css.indexOf('\n' + selector + ' {')
  assert.ok(start >= 0, `missing ${selector}`)
  return css.slice(start + 1).split('}')[0]
}
function tokens(selector) {
  return Object.fromEntries([...block(selector).matchAll(/(--[\w-]+):([^;]+);/g)].map(m => [m[1], m[2].trim()]))
}
const central = tokens('.holdings')
const darkStatus = tokens('.dark .holdings')
const STATUS = ['--caution', '--warning', '--positive', '--negative', '--signal-mint', '--signal-amber', '--signal-ink']
const THEMED = ['--canvas', '--surface', '--activity-paper', '--activity-rail', '--subtle', '--activity-soft', '--question',
  '--answer', '--tint', '--ink', '--secondary', '--activity-muted', '--line', '--activity-rule', '--stroke',
  '--control-border', '--activity-accent', '--selected', '--selected-ink', '--focus', '--alloc-1', '--alloc-2', '--alloc-3', '--alloc-4']

function themeVars(name, seen = new Set()) {
  assert.ok(!seen.has(name), `token cycle at ${name}`)
  seen.add(name)
  const value = central[name]
  assert.ok(value, `missing ${name}`)
  assert.doesNotMatch(value, /#[0-9a-f]{3,8}\b/i, `${name} is hardcoded`)
  const refs = [...value.matchAll(/var\((--[\w-]+)\)/g)].map(m => m[1])
  return refs.flatMap(ref => ref.startsWith('--ui-') ? [ref] : themeVars(ref, new Set(seen)))
}
function luminance(hex) {
  const rgb = hex.slice(1).match(/../g).map(c => parseInt(c, 16) / 255)
    .map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
  return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722
}
const contrast = (a, b) => { const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x); return (hi + .05) / (lo + .05) }

test('one central block maps every neutral, surface, stroke and accent token to Hermes --ui-* variables', () => {
  for (const name of THEMED) assert.ok(themeVars(name).length > 0, `${name} does not reach a --ui-* variable`)
  assert.match(central['--ink'], /^var\(--ui-base\)$/)
  assert.match(central['--secondary'], /var\(--ui-base\) 88%/)
  assert.match(central['--line'], /^var\(--ui-stroke-secondary\)$/)
  assert.match(central['--canvas'], /^var\(--ui-bg-editor\)$/)
  for (const name of ['--activity-accent', '--stroke', '--secondary']) assert.match(central[name], /var\(--ui-base\)/, `${name} must mix toward the theme foreground`)
  assert.deepEqual(Object.keys(darkStatus).sort(), ['--caution', '--negative', '--positive', '--warning'])
})

test('hex colors appear only as status colors and the one shadow color in the central block', () => {
  const outside = css.replace(block('.holdings'), '').replace(block('.dark .holdings'), '')
  assert.deepEqual(outside.match(/#[0-9a-f]{3,8}\b/gi) ?? [], [])
  const hexTokens = Object.entries(central).filter(([, value]) => /#[0-9a-f]{3,8}\b/i.test(value)).map(([name]) => name)
  assert.deepEqual(hexTokens.sort(), ['--shadow-color', ...STATUS].sort())
  assert.doesNotMatch(source.replace(css, ''), /['"`]#[0-9a-f]{3,8}['"`]/i)
  assert.doesNotMatch(css, /\.holdings-shell \{[^}]*--ink:/)
  assert.doesNotMatch(css, /var\(--(background|foreground|color-primary|color-muted)\)/)
})

test('status colors stay readable on light and dark Hermes surfaces', () => {
  const light = central, dark = { ...central, ...darkStatus }
  for (const [mode, t, surfaces] of [['light', light, ['#ffffff', '#f2f3f5']], ['dark', dark, ['#16181d', '#1f232b']]]) {
    for (const fg of ['--positive', '--negative']) for (const bg of surfaces) assert.ok(contrast(t[fg], bg) >= 4.5, `${mode}: ${fg} on ${bg}`)
    assert.ok(contrast(t['--caution'], t['--warning']) >= 4.5, `${mode}: caution`)
    assert.ok(contrast(t['--signal-ink'], t['--signal-mint']) >= 4.5)
    assert.ok(contrast(t['--signal-ink'], t['--signal-amber']) >= 4.5)
  }
})

test('allocation slices use theme tokens and shadows use the shadow color', () => {
  assert.match(source, /const allocationColors = \['var\(--alloc-1\)', 'var\(--alloc-2\)', 'var\(--alloc-3\)', 'var\(--alloc-4\)'\]/)
  assert.doesNotMatch(css, /box-shadow:[^;]*#[0-9a-f]/i)
  assert.match(css, /box-shadow:0 8px 24px color-mix\(in srgb, var\(--shadow-color\) 13%, transparent\)/)
})

test('evidence and conversation surfaces use theme tokens, not forced white', () => {
  assert.match(css, /\.holdings-shell \.evidence-header \{ background:var\(--surface\);/)
  assert.match(css, /\.holdings-shell \.chat-question \{[^}]*background:var\(--question\)/)
  assert.match(css, /\.holdings-shell \.message \{[^}]*background:var\(--answer\)/)
  assert.doesNotMatch(css, /\.holdings-shell \.evidence[^}]*background:#fff/)
  assert.match(css, /\.chart-series \.chart-line \{[^}]*stroke:var\(--activity-accent\)/)
})
