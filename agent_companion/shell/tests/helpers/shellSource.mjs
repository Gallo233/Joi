/**
 * Static readers over the shell's own source.
 *
 * The contracts these helpers support -- approvals stay visually separate and
 * unobscured, developer detail stays behind Developer Mode -- are product
 * requirements from PRD 9.1 and TDD 12.2. They are properties of the rendered
 * shell, but the shell has no DOM test harness, and adding one would not help:
 * the requirements are about *structure* (what is nested in what, what is
 * gated by what, which layer outranks which), and structure is exactly what
 * the source states outright. Reading it directly keeps these tests fast and
 * keeps them working while the templates are split into components.
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../src')

/** Elements that never carry children, so they must not push onto the tag stack. */
const VOID_TAGS = new Set([
  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
  'link', 'meta', 'param', 'source', 'track', 'wbr',
])

const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) walk(full, out)
    else out.push(full)
  }
  return out
}

const sourceFiles = walk(SRC)
// Tests identify a stylesheet by this path, so it has to read the same on every
// platform: `path.relative` yields `styles\\tokens.css` on Windows, and a test
// looking for `styles/tokens.css` then finds nothing and reports the token it
// was checking as undefined rather than as a mismatch.
const relative = (file) => path.relative(SRC, file).split(path.sep).join('/')

/** Every stylesheet: the global files plus each <style> block inside an SFC. */
export function stylesheets() {
  const sheets = []
  for (const file of sourceFiles.filter((f) => f.endsWith('.css'))) {
    sheets.push({ file: relative(file), scoped: false, text: fs.readFileSync(file, 'utf8') })
  }
  for (const file of sourceFiles.filter((f) => f.endsWith('.vue'))) {
    const source = fs.readFileSync(file, 'utf8')
    for (const match of source.matchAll(/<style([^>]*)>([\s\S]*?)<\/style>/g)) {
      sheets.push({ file: relative(file), scoped: /\bscoped\b/.test(match[1]), text: match[2] })
    }
  }
  return sheets
}

/**
 * Flat list of rule blocks. Nested at-rule bodies are included; their wrappers
 * are not.
 *
 * `[^{}]+` is what makes consecutive rules work: it starts right after the
 * previous rule's `}` and cannot cross a brace, so it captures exactly the
 * selector text. Anchoring on the brace instead would consume it and silently
 * drop every second rule.
 *
 * Element-level selectors are kept. Filtering to `.`/`#` would drop
 * `button:active` and `:focus-visible`, which is where the shell's interaction
 * feedback lives.
 */
export function cssRules() {
  const rules = []
  for (const sheet of stylesheets()) {
    const text = stripComments(sheet.text)
    for (const match of text.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const selector = match[1].trim().replace(/\s+/g, ' ')
      if (!selector || selector.startsWith('@') || selector.includes(';')) continue
      if (/^(from|to|\d+%)$/.test(selector)) continue
      rules.push({
        selector,
        declarations: match[2],
        file: sheet.file,
        scoped: sheet.scoped,
        atRule: enclosingAtRule(text, match.index),
      })
    }
  }
  return rules
}

/**
 * The `@media` / `@container` / `@supports` prelude a rule sits inside, if any.
 *
 * Found by walking braces backwards from the rule, because the flat scan above
 * discards nesting. Tests need this to tell "collapses when the panel is
 * narrow" from "collapses when the window is narrow" -- two rules that look
 * identical in isolation and mean opposite things.
 */
function enclosingAtRule(text, index) {
  let depth = 0
  for (let i = index - 1; i >= 0; i -= 1) {
    const ch = text[i]
    if (ch === '}') depth += 1
    else if (ch === '{') {
      if (depth === 0) {
        const prelude = text.slice(0, i).match(/@[\w-]+[^{};]*$/)
        return prelude ? prelude[0].trim().replace(/\s+/g, ' ') : null
      }
      depth -= 1
    }
  }
  return null
}

/** Classes named anywhere in a selector, e.g. `.a .b:hover` -> ['a', 'b']. */
export function classesIn(selector) {
  return [...selector.matchAll(/\.(-?[A-Za-z_][A-Za-z0-9_-]*)/g)].map((m) => m[1])
}

/**
 * True when the rule styles `className` itself rather than something inside it.
 *
 * `.card` and `.rail .card:hover` style the card; `.card > p` and
 * `.card button.secondary` style its contents. The distinction matters whenever
 * a test asks whether an element carries a treatment, because a border on a
 * button inside a card is not a border on the card.
 */
export function targetsExactly(selector, className) {
  const escaped = className.replace(/[-[\]{}()*+?.,\\^$|#\s]/g, '\\$&')
  return selector
    .split(',')
    .some((part) => new RegExp(`\\.${escaped}(?:::?[\\w-]+(?:\\([^)]*\\))?)*$`).test(part.trim()))
}

export function declarationValue(declarations, property) {
  const match = declarations.match(new RegExp(`(?<![\\w-])${property}\\s*:\\s*([^;]+)`))
  return match ? match[1].trim() : null
}

/**
 * Highest z-index any rule assigns to a selector mentioning `className`.
 * Returns null when the class never participates in stacking.
 */
export function stackingLevel(className) {
  let highest = null
  for (const rule of cssRules()) {
    if (!classesIn(rule.selector).includes(className)) continue
    const value = declarationValue(rule.declarations, 'z-index')
    if (value === null) continue
    const level = Number.parseInt(value, 10)
    if (!Number.isFinite(level)) continue
    highest = highest === null ? level : Math.max(highest, level)
  }
  return highest
}

/** The <template> block of every SFC. */
export function templates() {
  return sourceFiles
    .filter((file) => file.endsWith('.vue'))
    .map((file) => {
      const source = fs.readFileSync(file, 'utf8')
      const match = source.match(/<template>([\s\S]*)<\/template>/)
      return match ? { file: relative(file), text: match[1] } : null
    })
    .filter(Boolean)
}

/** Static classes plus the literal keys of an object `:class` binding. */
function classesOnTag(attributes) {
  const classes = []
  const literal = attributes.match(/(?:^|\s)class="([^"]*)"/)
  if (literal) classes.push(...literal[1].split(/\s+/).filter(Boolean))
  const bound = attributes.match(/(?:^|\s):class="\{([^}]*)\}"/)
  if (bound) {
    for (const entry of bound[1].split(',')) {
      const key = entry.match(/^\s*'([^']+)'\s*:|^\s*"([^"]+)"\s*:|^\s*([A-Za-z_][\w-]*)\s*:/)
      if (key) classes.push(key[1] || key[2] || key[3])
    }
  }
  return classes
}

/**
 * Every element in a template, each with the chain of elements enclosing it.
 * Ancestors are ordered outermost-first.
 */
export function elementsWithAncestors(template) {
  const elements = []
  const stack = []
  const tagPattern = /<(\/?)([a-zA-Z][\w.-]*)((?:"[^"]*"|'[^']*'|[^>"'])*?)(\/?)>/g

  for (const match of template.text.matchAll(tagPattern)) {
    const [, closing, tag, attributes = '', selfClosing] = match

    if (closing) {
      for (let i = stack.length - 1; i >= 0; i -= 1) {
        if (stack[i].tag === tag) {
          stack.length = i
          break
        }
      }
      continue
    }

    const element = {
      tag,
      attributes,
      classes: classesOnTag(attributes),
      file: template.file,
      line: template.text.slice(0, match.index).split('\n').length,
      ancestors: stack.map((entry) => ({ tag: entry.tag, classes: entry.classes, attributes: entry.attributes })),
    }
    elements.push(element)

    if (!selfClosing && !VOID_TAGS.has(tag.toLowerCase())) {
      stack.push(element)
    }
  }

  return elements
}

/** Every element across every SFC template. */
export function allElements() {
  return templates().flatMap((template) => elementsWithAncestors(template))
}

/** True when the element, or anything enclosing it, carries the class. */
export function withinClass(element, className) {
  return element.ancestors.some((ancestor) => ancestor.classes.includes(className))
}

/** The `v-if` / `v-else-if` / `v-show` expression on an element, if any. */
export function conditionOf(element) {
  const match = element.attributes.match(/(?:^|\s)v-(?:else-if|if|show)="([^"]*)"/)
  return match ? match[1] : null
}

/**
 * Every line of script the shell runs, across .ts files and SFC <script> blocks.
 * Use this for contracts about behaviour that will outlive the file it
 * currently lives in.
 */
export function scriptSource() {
  return sourceFiles
    .filter((file) => /\.(ts|js|vue)$/.test(file))
    .map((file) => {
      const source = fs.readFileSync(file, 'utf8')
      if (!file.endsWith('.vue')) return source
      return [...source.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join('\n')
    })
    .join('\n')
}
