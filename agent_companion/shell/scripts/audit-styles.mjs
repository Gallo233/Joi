/**
 * Style health audit for the Joi shell.
 *
 * The shell's CSS grew as archaeological layers: base rules near the top, then
 * successive redesigns appended at the bottom that re-target the same elements
 * through more specific selectors. That is invisible in a diff and invisible in
 * a screenshot, so this script measures it directly and prints the numbers the
 * refactor is supposed to move.
 *
 * Usage:
 *   node scripts/audit-styles.mjs              # human-readable report
 *   node scripts/audit-styles.mjs --json       # machine-readable, for diffing
 *   node scripts/audit-styles.mjs --baseline b.json   # compare against a saved run
 *   node scripts/audit-styles.mjs --src path/to/src   # measure a different tree
 *
 * `--src` exists so a baseline can be re-measured against a pristine copy when
 * the measurement itself is corrected. A baseline taken with a parser bug is
 * worse than none: every later run diffs against a number that was never true.
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const srcFlag = process.argv.indexOf('--src')
const SRC = srcFlag === -1 ? path.resolve(HERE, '../src') : path.resolve(process.argv[srcFlag + 1])

/** Selectors that only exist to out-specify an earlier rule for the same element. */
const ESCALATION_PATTERNS = [/\.shell:not\(/, /^\.settings-active\s/]

/** Values below this are unreadable on a Retina panel at normal viewing distance. */
const MIN_FONT_PX = 11

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) walk(full, out)
    else out.push(full)
  }
  return out
}

const files = walk(SRC)
const cssFiles = files.filter((f) => f.endsWith('.css'))
const vueFiles = files.filter((f) => f.endsWith('.vue'))
const codeFiles = files.filter((f) => /\.(vue|ts|js)$/.test(f))

const rel = (f) => path.relative(SRC, f)
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')

/**
 * Every stylesheet in the project: the global files plus each <style> block
 * inside an SFC. Blocks carry their own line offset so reported line numbers
 * point at the real place in the real file.
 */
function collectStylesheets() {
  const sheets = []
  for (const file of cssFiles) {
    sheets.push({ file: rel(file), scoped: false, offset: 0, text: fs.readFileSync(file, 'utf8') })
  }
  for (const file of vueFiles) {
    const source = fs.readFileSync(file, 'utf8')
    const re = /<style([^>]*)>([\s\S]*?)<\/style>/g
    let match
    while ((match = re.exec(source))) {
      sheets.push({
        file: rel(file),
        scoped: /\bscoped\b/.test(match[1]),
        offset: source.slice(0, match.index).split('\n').length,
        text: match[2],
      })
    }
  }
  return sheets
}

const sheets = collectStylesheets()
const allCss = sheets.map((s) => stripComments(s.text)).join('\n')

/**
 * True for real selectors, false for at-rule preludes, keyframe steps and any
 * stray declaration text.
 *
 * Restricting this to selectors beginning with `.` or `#` would silently drop
 * every element-level rule -- including `button:active` and `:focus-visible`,
 * which are precisely the ones this audit exists to count.
 */
function isSelector(text) {
  if (!text || text.startsWith('@') || text.includes(';')) return false
  return !/^(from|to|\d+%)$/.test(text)
}

/**
 * Rule blocks, with the file and line they were declared at.
 *
 * `[^{}]+` is what makes consecutive rules work: it starts right after the
 * previous rule's `}` and cannot cross a brace, so it captures exactly the
 * selector text. Anchoring on the brace instead would consume it and silently
 * drop every second rule.
 */
function collectRules() {
  const rules = []
  for (const sheet of sheets) {
    const text = stripComments(sheet.text)
    const re = /([^{}]+)\{([^{}]*)\}/g
    let match
    while ((match = re.exec(text))) {
      const selector = match[1].trim().replace(/\s+/g, ' ')
      if (!isSelector(selector)) continue
      rules.push({
        selector,
        declarations: match[2],
        file: sheet.file,
        scoped: sheet.scoped,
        line: sheet.offset + text.slice(0, match.index).split('\n').length,
      })
    }
  }
  return rules
}

const rules = collectRules()

function distinctValues(prop) {
  const found = new Map()
  const re = new RegExp(`(?<![\\w-])${prop}\\s*:\\s*([^;{}]+);`, 'g')
  let match
  while ((match = re.exec(allCss))) {
    const value = match[1].trim()
    found.set(value, (found.get(value) || 0) + 1)
  }
  return found
}

// ── Dead classes ────────────────────────────────────────────────────────────
// A class is dead when no template, script or scoped style ever names it.
// Numeric fragments like `.5s` inside shorthand values are not class names.
const declaredClasses = new Set(
  [...allCss.matchAll(/\.(-?[A-Za-z_][A-Za-z0-9_-]*)/g)].map((m) => m[1]),
)
const markup = codeFiles.map((f) => fs.readFileSync(f, 'utf8')).join('\n')

/*
 * Classes assembled at runtime never appear in the source as whole words.
 * `:class="`status-${turn.status}`"` puts `status-failed` on an element without
 * the string `status-failed` existing anywhere, so a plain substring search
 * calls it dead. Eighteen live classes were being reported that way -- the
 * emotion states, the step and turn statuses, the cabins -- and this number is
 * read specifically when deciding what is safe to delete.
 */
const dynamicPrefixes = [...markup.matchAll(/`([a-z0-9-]*?-)\$\{/g)].map((m) => m[1])
const builtAtRuntime = (name) => dynamicPrefixes.some((prefix) => name.startsWith(prefix))

const deadClasses = [...declaredClasses]
  .filter((name) => !markup.includes(name) && !builtAtRuntime(name))
  .sort()

// ── Interaction states ──────────────────────────────────────────────────────
// A press state that only changes the cursor is not feedback: the user sees
// nothing happen. Count those separately from real pressed styling.
const activeRules = rules.filter((r) => r.selector.includes(':active'))
const activeWithVisualChange = activeRules.filter(
  (rule) => !/^\s*cursor\s*:[^;]+;?\s*$/.test(rule.declarations),
)
const focusVisibleRules = rules.filter((r) => r.selector.includes(':focus-visible'))

// Interactive elements that a person can click, across every template.
const interactiveElements = [...markup.matchAll(/<(button|a|input|select|textarea)\b/g)].length

// ── Scatter: how far apart are the rules that style one element? ────────────
// This is the number that decides whether a change is a one-file edit or an
// archaeology session.
const byTarget = new Map()
for (const rule of rules) {
  const classes = [...rule.selector.matchAll(/\.(-?[A-Za-z_][A-Za-z0-9_-]*)/g)].map((m) => m[1])
  if (!classes.length) continue
  const target = classes[classes.length - 1]
  if (!byTarget.has(target)) byTarget.set(target, [])
  byTarget.get(target).push(rule)
}
const scattered = [...byTarget.entries()]
  .map(([target, list]) => {
    const sameFile = list.filter((r) => r.file === list[0].file)
    const spread = sameFile.length > 1 ? Math.max(...sameFile.map((r) => r.line)) - Math.min(...sameFile.map((r) => r.line)) : 0
    return { target, blocks: list.length, spread, file: list[0].file }
  })
  .filter((entry) => entry.blocks > 3)
  .sort((a, b) => b.spread - a.spread || b.blocks - a.blocks)

// ── Colors, tokens, scales ──────────────────────────────────────────────────
const hardcodedColors = [
  ...allCss.matchAll(
    /(?<![\w-])(?:color|background|background-color|border-color|fill|stroke)\s*:\s*(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))/g,
  ),
].map((m) => m[1].toLowerCase())
const declaredTokens = new Set([...allCss.matchAll(/(--[a-z0-9-]+)\s*:/g)].map((m) => m[1]))
const usedTokens = new Set([...allCss.matchAll(/var\((--[a-z0-9-]+)/g)].map((m) => m[1]))
const unusedTokens = [...declaredTokens].filter((t) => !usedTokens.has(t)).sort()

const fontSizes = distinctValues('font-size')
const tinyFonts = [...fontSizes.keys()].filter((v) => {
  const px = parseFloat(v)
  return v.endsWith('px') && Number.isFinite(px) && px < MIN_FONT_PX
})

const escalating = rules.filter((r) => ESCALATION_PATTERNS.some((p) => p.test(r.selector)))
const importantCount = (allCss.match(/!important/g) || []).length

// ── File sizes ──────────────────────────────────────────────────────────────
const fileSizes = codeFiles
  .concat(cssFiles)
  .filter((f, i, arr) => arr.indexOf(f) === i)
  .map((f) => ({ file: rel(f), lines: fs.readFileSync(f, 'utf8').split('\n').length }))
  .sort((a, b) => b.lines - a.lines)

const globalCssLines = cssFiles.reduce((n, f) => n + fs.readFileSync(f, 'utf8').split('\n').length, 0)
const scopedCssLines = sheets
  .filter((s) => s.scoped)
  .reduce((n, s) => n + s.text.split('\n').length, 0)

const report = {
  files: {
    globalCssLines,
    scopedCssLines,
    largest: fileSizes.slice(0, 5),
  },
  rules: {
    total: rules.length,
    scoped: rules.filter((r) => r.scoped).length,
    escalating: escalating.length,
    important: importantCount,
  },
  interaction: {
    interactiveElements,
    activeRules: activeRules.length,
    activeRulesWithVisualChange: activeWithVisualChange.length,
    focusVisibleRules: focusVisibleRules.length,
  },
  deadCode: {
    declaredClasses: declaredClasses.size,
    deadClasses: deadClasses.length,
    unusedTokens: unusedTokens.length,
  },
  tokens: {
    declared: declaredTokens.size,
    hardcodedColorLiterals: hardcodedColors.length,
    distinctHardcodedColors: new Set(hardcodedColors).size,
  },
  scales: {
    fontSize: fontSizes.size,
    fontSizesBelowMin: tinyFonts.length,
    borderRadius: distinctValues('border-radius').size,
    padding: distinctValues('padding').size,
    gap: distinctValues('gap').size,
    boxShadow: distinctValues('box-shadow').size,
  },
  scatter: {
    targetsOverThreeBlocks: scattered.length,
    worstSpread: scattered[0] ? scattered[0].spread : 0,
  },
}

const args = process.argv.slice(2)

if (args.includes('--json')) {
  process.stdout.write(JSON.stringify(report, null, 2) + '\n')
  process.exit(0)
}

const pad = (label, width = 42) => label + ' '.repeat(Math.max(1, width - label.length))
const section = (title) => `\n\x1b[1m${title}\x1b[0m`

console.log(section('Files'))
console.log(pad('  global CSS lines'), report.files.globalCssLines)
console.log(pad('  scoped CSS lines (inside SFCs)'), report.files.scopedCssLines)
for (const f of report.files.largest) console.log(pad(`  ${f.file}`), f.lines)

console.log(section('Rules'))
console.log(pad('  rule blocks'), report.rules.total, `(${report.rules.scoped} scoped)`)
console.log(pad('  specificity-escalating selectors'), report.rules.escalating)
console.log(pad('  !important declarations'), report.rules.important)

console.log(section('Interaction feedback'))
console.log(pad('  interactive elements in templates'), report.interaction.interactiveElements)
console.log(
  pad('  :active rules (with a visible change)'),
  `${report.interaction.activeRules} (${report.interaction.activeRulesWithVisualChange})`,
)
console.log(pad('  :focus-visible rules'), report.interaction.focusVisibleRules)

console.log(section('Dead code'))
console.log(pad('  class names declared'), report.deadCode.declaredClasses)
console.log(pad('  never referenced in src/'), report.deadCode.deadClasses)
console.log(pad('  custom properties never read'), report.deadCode.unusedTokens)

console.log(section('Tokens vs hardcoded values'))
console.log(pad('  custom properties declared'), report.tokens.declared)
console.log(
  pad('  hardcoded color literals'),
  `${report.tokens.hardcodedColorLiterals} (${report.tokens.distinctHardcodedColors} distinct)`,
)

console.log(section('Scale drift'))
for (const [key, value] of Object.entries(report.scales)) {
  console.log(pad(`  ${key}`), value)
}
if (tinyFonts.length) console.log(pad('  → below minimum'), tinyFonts.join(', '))

console.log(section('Most scattered targets'))
if (!scattered.length) {
  console.log('  none — every element is styled in one place')
} else {
  for (const entry of scattered.slice(0, 8)) {
    console.log(`  ${pad(`.${entry.target}`, 34)}${entry.blocks} blocks, spread ${entry.spread} lines`)
  }
}

const baselineFlag = args.indexOf('--baseline')
if (baselineFlag !== -1 && args[baselineFlag + 1]) {
  const previous = JSON.parse(fs.readFileSync(args[baselineFlag + 1], 'utf8'))
  console.log(section('Change since baseline'))
  const compare = (label, now, before) => {
    if (now === before) return
    const delta = now - before
    const arrow = delta > 0 ? `+${delta}` : `${delta}`
    console.log(`  ${pad(label, 40)}${before} → ${now}  (${arrow})`)
  }
  const flatten = (obj, prefix = '') =>
    Object.entries(obj).flatMap(([k, v]) =>
      v && typeof v === 'object' && !Array.isArray(v) ? flatten(v, `${prefix}${k}.`) : [[`${prefix}${k}`, v]],
    )
  const before = Object.fromEntries(flatten(previous))
  for (const [key, value] of flatten(report)) {
    if (typeof value === 'number' && typeof before[key] === 'number') compare(key, value, before[key])
  }
}

console.log()
