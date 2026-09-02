import assert from 'node:assert/strict'
import test from 'node:test'

import { NEUTRAL_VISEMES, VISEMES, blendVisemes, mouthShape, visemesFromSpectrum } from '../src/voiceVisemes.ts'

const SAMPLE_RATE = 48_000
const BINS = 1024

/**
 * A spectrum shaped like voiced speech: formant humps on a falling tilt.
 *
 * The tilt matters. Real speech loses about 6dB per octave, and an analyser
 * that only saw clean peaks would let a bug in the tilt correction pass -- so
 * the fixture carries the same slope the correction has to cancel.
 */
function spectrumWithFormants(formants, { tiltDbPerOctave = -6, floorDb = -95 } = {}) {
  const hzPerBin = SAMPLE_RATE / 2 / BINS
  const decibels = new Float32Array(BINS)
  for (let i = 0; i < BINS; i += 1) {
    const hz = Math.max(hzPerBin, i * hzPerBin)
    let level = floorDb + Math.log2(hz / 500) * tiltDbPerOctave
    for (const [centre, gainDb, widthHz] of formants) {
      const spread = (hz - centre) / widthHz
      level += gainDb * Math.exp(-0.5 * spread * spread)
    }
    decibels[i] = level
  }
  return decibels
}

/** Which shape the estimate landed on. */
function strongest(weights) {
  return VISEMES.reduce((best, viseme) => (weights[viseme] > weights[best] ? viseme : best), VISEMES[0])
}

// The point of the whole module: 啊, 衣 and 乌 have to come out as visibly
// different mouths. Their textbook formant pairs are the input, so a
// regression in the tilt, the smoothing or the scoring shows up here first.
const VOWEL_CASES = [
  ['aa', 'an open 啊', [[800, 34, 110], [1250, 30, 150]]],
  ['ih', 'a close front 衣', [[300, 34, 90], [2350, 30, 220]]],
  ['ou', 'a rounded 乌', [[340, 34, 90], [820, 30, 130]]],
  ['ee', 'a mid front 诶', [[530, 34, 100], [1900, 30, 200]]],
]

for (const [expected, description, formants] of VOWEL_CASES) {
  test(`${description} reads as ${expected}`, () => {
    const weights = visemesFromSpectrum(spectrumWithFormants(formants), SAMPLE_RATE)
    assert.equal(strongest(weights), expected)
  })
}

test('weights always sum to one so the mouth never opens past its level', () => {
  for (const [, , formants] of VOWEL_CASES) {
    const weights = visemesFromSpectrum(spectrumWithFormants(formants), SAMPLE_RATE)
    const total = VISEMES.reduce((sum, viseme) => sum + weights[viseme], 0)
    assert.ok(Math.abs(total - 1) < 1e-9, `weights summed to ${total}`)
  }
})

test('a vowel-shaped spectrum commits to a shape rather than smearing evenly', () => {
  const weights = visemesFromSpectrum(spectrumWithFormants(VOWEL_CASES[1][2]), SAMPLE_RATE)
  // An even split across five shapes is 0.2 each and would read as no shape at
  // all. A real vowel has to be clearly ahead.
  assert.ok(weights.ih > 0.5, `衣 scored only ${weights.ih}`)
})

test('pitch harmonics do not get mistaken for formants', () => {
  // A 120Hz voice puts a sharp peak every 120Hz. Those are louder and narrower
  // than the formants underneath, so without smoothing the peak search would
  // return a harmonic and the vowel would follow the singer's pitch.
  const hzPerBin = SAMPLE_RATE / 2 / BINS
  const base = spectrumWithFormants(VOWEL_CASES[0][2])
  const combed = Float32Array.from(base)
  for (let harmonic = 120; harmonic < 4000; harmonic += 120) {
    const bin = Math.round(harmonic / hzPerBin)
    if (bin < BINS) combed[bin] += 12
  }
  assert.equal(strongest(visemesFromSpectrum(combed, SAMPLE_RATE)), 'aa')
})

test('silence returns the neutral shape instead of guessing a vowel', () => {
  const silent = new Float32Array(BINS).fill(-Infinity)
  assert.deepEqual(visemesFromSpectrum(silent, SAMPLE_RATE), NEUTRAL_VISEMES)
})

test('a flat spectrum has no formants to find and stays neutral', () => {
  assert.deepEqual(visemesFromSpectrum(new Float32Array(BINS).fill(-40), SAMPLE_RATE), NEUTRAL_VISEMES)
})

test('a nonsense sample rate degrades instead of throwing', () => {
  const decibels = spectrumWithFormants(VOWEL_CASES[0][2])
  assert.deepEqual(visemesFromSpectrum(decibels, 0), NEUTRAL_VISEMES)
  assert.deepEqual(visemesFromSpectrum(decibels, Number.NaN), NEUTRAL_VISEMES)
  assert.deepEqual(visemesFromSpectrum(new Float32Array(4), SAMPLE_RATE), NEUTRAL_VISEMES)
})

test('blending moves toward the new shape without jumping to it', () => {
  const to = { aa: 0, ih: 1, ou: 0, ee: 0, oh: 0 }
  const stepped = blendVisemes(NEUTRAL_VISEMES, to, 0.25)
  assert.equal(stepped.ih, 0.25)
  assert.equal(stepped.aa, 0.75)
  // Clamped, so a caller passing a wild alpha cannot overshoot past the target.
  assert.deepEqual(blendVisemes(NEUTRAL_VISEMES, to, 5), to)
  assert.deepEqual(blendVisemes(NEUTRAL_VISEMES, to, -1), NEUTRAL_VISEMES)
})

test('close vowels open a single-parameter mouth less than open ones', () => {
  const open = mouthShape({ aa: 1, ih: 0, ou: 0, ee: 0, oh: 0 })
  const close = mouthShape({ aa: 0, ih: 1, ou: 0, ee: 0, oh: 0 })
  const round = mouthShape({ aa: 0, ih: 0, ou: 1, ee: 0, oh: 0 })
  assert.ok(close.open < open.open * 0.6, '衣 should not open as far as 啊')
  assert.ok(round.open < open.open * 0.6, '乌 should not open as far as 啊')
  // Spread versus pursed: the sign is what a Cubism rig reads.
  assert.ok(close.form > 0.5, '衣 should pull the mouth wide')
  assert.ok(round.form < -0.5, '乌 should purse the mouth')
})

test('the neutral shape drives a single-parameter mouth exactly as before', () => {
  // Callers that never look at visemes must be unaffected: neutral is all
  // `aa`, and `aa` scales the opening by one.
  assert.equal(mouthShape(NEUTRAL_VISEMES).open, 1)
})
