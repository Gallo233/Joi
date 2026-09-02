/**
 * PRD 9.1: "审批卡与普通消息视觉分离" -- an approval must not read as just
 * another thing Joi said. The user is being asked to authorise something; if it
 * looks like ordinary chat they will scroll past it, or worse, click through it.
 *
 * The refactor compresses routine status down to a single line precisely so
 * approvals stand out. That pressure runs the wrong way for this requirement:
 * every step that removes chrome is a step that could remove the chrome
 * distinguishing an approval. These tests hold that line.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { classesIn, cssRules, declarationValue, targetsExactly } from './helpers/shellSource.mjs'

/** Surfaces that ask the user to authorise something. */
const APPROVAL_SURFACES = ['execution-approval', 'approval-actions', 'mini-approval-actions']

/** The ordinary "Joi said something" container an approval must not resemble. */
const ORDINARY_MESSAGE = 'message-bubble'

function rulesTargeting(className) {
  return cssRules().filter((rule) => classesIn(rule.selector).includes(className))
}

test('each approval surface is visually distinguished, not styled as ordinary chat', () => {
  for (const surface of APPROVAL_SURFACES) {
    const rules = rulesTargeting(surface)
    assert.ok(rules.length > 0, `${surface} has no styling at all -- it would render as plain text`)

    // Separation is carried either by the container (a card with its own border
    // or fill) or by its actions (buttons with a solid accent). Both read as
    // "this is not ordinary message content"; requiring one specific mechanism
    // would be a rule about implementation, not about what the user sees.
    const distinguished = rules.some((rule) => {
      const border = declarationValue(rule.declarations, 'border')
      const background = declarationValue(rule.declarations, 'background')
      const hasBorder = border !== null && border !== '0' && border !== 'none'
      const hasFill = background !== null && background !== 'none' && background !== 'transparent'
      return hasBorder || hasFill
    })

    assert.ok(
      distinguished,
      `${surface} declares no border or fill anywhere -- nothing separates it from ordinary chat`,
    )
  }
})

test('no rule styles an approval surface and an ordinary message together', () => {
  const shared = cssRules().filter((rule) => {
    const classes = classesIn(rule.selector)
    return classes.includes(ORDINARY_MESSAGE) && APPROVAL_SURFACES.some((s) => classes.includes(s))
  })

  assert.deepEqual(
    shared.map((rule) => rule.selector),
    [],
    'a single rule styling both would let an approval drift into looking like ordinary chat',
  )
})

test('the conversation approval keeps a card of its own', () => {
  // The in-conversation approval is the one PRD 9.1 names directly: it sits
  // inline among messages, so it is the one that has to carry a card.
  //
  // `targetsExactly` is what makes this test real: the approval's own buttons
  // carry borders, and matching on "any rule mentioning the class" would let a
  // button's border stand in for the card's.
  const carded = cssRules()
    .filter((rule) => targetsExactly(rule.selector, 'execution-approval'))
    .some((rule) => {
      const border = declarationValue(rule.declarations, 'border')
      return border !== null && border !== '0' && border !== 'none'
    })

  assert.ok(carded, 'execution-approval lost its border -- it would sit flush with the surrounding turn')
})
