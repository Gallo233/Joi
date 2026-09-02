/**
 * Read the vowel out of the spectrum, so the mouth has a shape and not just a
 * size.
 *
 * Level alone answers "how far open", which is enough to look like talking and
 * not enough to look like speech: every sound comes out as the same wide `aa`.
 * What separates 啊 from 衣 from 乌 is where the mouth is narrow -- and that is
 * exactly what the first two formants describe. F1 tracks how open the jaw is,
 * F2 how far forward the tongue sits, so a point in the (F1, F2) plane names a
 * vowel regardless of who is speaking or what pitch they are on.
 *
 * This is an estimate, not a phonemic aligner. It has no idea what word is
 * being said; it reports which of the five VRM/Cubism vowel shapes the current
 * 20ms of audio sits closest to. On a sustained vowel that is nearly always
 * right, on a fast consonant cluster it smears between neighbours -- which is
 * roughly what a real mouth does anyway. Getting 啊/衣/乌 visibly distinct is
 * the whole win here; distinguishing 哦 from 乌 is not, and the two rounded
 * shapes are deliberately left to blend into each other.
 */

export type Viseme = 'aa' | 'ih' | 'ou' | 'ee' | 'oh'

export type VisemeWeights = Record<Viseme, number>

export const VISEMES: readonly Viseme[] = ['aa', 'ih', 'ou', 'ee', 'oh']

/** What the mouth does when there is no spectrum to read: the old single shape. */
export const NEUTRAL_VISEMES: VisemeWeights = { aa: 1, ih: 0, ou: 0, ee: 0, oh: 0 }

/**
 * Where each shape lives in the (F1, F2) plane, in Hz.
 *
 * Averaged across male and female speakers rather than taken from either: the
 * character's voice is whatever the user supplies, and a table tuned to one
 * vocal tract misreads the other by close to an octave in F2. Leaning toward
 * Mandarin values because that is what these characters speak -- Mandarin /u/
 * is rounder, and so lower in F2, than the Japanese /u/ the VRM preset names
 * come from.
 */
const FORMANTS: Record<Viseme, readonly [number, number]> = {
  aa: [850, 1300],
  ih: [320, 2400],
  ou: [350, 800],
  ee: [520, 1950],
  oh: [500, 950],
}

/**
 * Where the spectrum gets measured, in Hz.
 *
 * Reading F1 and F2 by hunting for two peaks is the obvious approach and it
 * breaks on exactly the vowel this was built for: 乌 puts its formants around
 * 350 and 800Hz, close enough that any smoothing wide enough to survive pitch
 * harmonics fuses them into one hump, and the hunt then reports a single
 * mid-frequency peak indistinguishable from 啊.
 *
 * Comparing the shape of the whole low spectrum against a template per vowel
 * has no such failure: fused formants are simply what 乌's template looks like
 * too. The bands widen with frequency because hearing does, and because every
 * band ends up wider than any plausible F0 -- which is what averages the
 * harmonic comb away without needing to smooth across formants.
 */
const BAND_EDGES: readonly number[] = [200, 400, 650, 1000, 1500, 2200, 3200]

// How far a formant's influence spreads across neighbouring bands, in log
// units -- about ±20%. Wider than a real formant bandwidth on purpose: the
// template is being matched against band averages, not against a spectrum.
const FORMANT_SPREAD = 0.22

// F2 shapes the mouth as much as F1 but carries less energy in real speech.
const F2_WEIGHT = 0.8

// Cosine similarity runs -1 to 1, and this converts it into a preference. Low
// enough that the nearest vowel clearly wins, high enough that two genuinely
// similar shapes -- 哦 and 乌 -- still blend rather than snapping.
const DECISIVENESS = 0.12

// Below this there is no shape in the spectrum to read: silence, a flat
// synthetic buffer, or a band that came back empty.
const MIN_CONTRAST_DB = 1

// Speech rolls off with frequency, so an uncorrected profile always leans
// low and scores every vowel as a rounded one. Tilting the spectrum back up
// cancels the slope; +6 dB/octave is the conventional pre-emphasis.
const TILT_DB_PER_OCTAVE = 6
const TILT_PIVOT_HZ = 500

/**
 * Nearest vowel shapes for one spectrum, as weights summing to 1.
 *
 * `decibels` is an analyser's `getFloatTimeDomainData` counterpart --
 * `getFloatFrequencyData` output, one dB value per bin, bin `i` centred at
 * `i * sampleRate / (2 * length)`. Silence, garbage, or a spectrum with no
 * usable peak returns the neutral shape rather than an arbitrary vowel.
 */
export function visemesFromSpectrum(decibels: ArrayLike<number>, sampleRate: number): VisemeWeights {
  const bins = decibels.length
  if (bins < 8 || !Number.isFinite(sampleRate) || sampleRate <= 0) return { ...NEUTRAL_VISEMES }
  const heard = bandProfile(decibels, sampleRate / 2 / bins)
  if (!heard) return { ...NEUTRAL_VISEMES }
  return scoreVowels(heard)
}

/** Geometric centre of each band, which is its centre to the ear and to a log kernel. */
const BAND_CENTRES: readonly number[] = BAND_EDGES.slice(0, -1).map((low, index) =>
  Math.sqrt(low * BAND_EDGES[index + 1]),
)

/**
 * Average tilt-corrected level in each band, centred and scaled to unit length.
 *
 * Centring is what makes the comparison about shape rather than loudness: a
 * whisper and a shout on the same vowel land on the same profile. Returns
 * nothing when there is no contrast to read, so silence and empty buffers stay
 * out of the scoring rather than resolving to whichever vowel is flattest.
 */
function bandProfile(decibels: ArrayLike<number>, hzPerBin: number): number[] | null {
  const bins = decibels.length
  const levels: number[] = []
  for (let band = 0; band < BAND_CENTRES.length; band += 1) {
    const first = Math.max(1, Math.ceil(BAND_EDGES[band] / hzPerBin))
    const last = Math.min(bins - 1, Math.floor(BAND_EDGES[band + 1] / hzPerBin))
    let sum = 0
    let count = 0
    for (let i = first; i <= last; i += 1) {
      // A real analyser floors at `minDecibels` and can report -Infinity
      // outright; one such bin would otherwise take the whole average with it.
      if (Number.isFinite(decibels[i])) {
        sum += decibels[i]
        count += 1
      }
    }
    // A band with nothing in it means this spectrum cannot be judged -- too
    // few bins, or a sample rate that does not reach these frequencies.
    if (!count) return null
    levels.push(sum / count)
  }
  // Judged before the tilt correction, which would turn a featureless spectrum
  // into a convincing upward ramp and hand it to whichever vowel leans
  // highest. Silence and synthetic buffers have to fail here, not after.
  if (Math.max(...levels) - Math.min(...levels) < MIN_CONTRAST_DB) return null
  const tilted = levels.map((level, band) => level + Math.log2(BAND_CENTRES[band] / TILT_PIVOT_HZ) * TILT_DB_PER_OCTAVE)
  return normalize(tilted, 0)
}

/** Subtract the mean and scale to unit length, or nothing if there is no shape left. */
function normalize(levels: number[], floor: number): number[] | null {
  const mean = levels.reduce((sum, value) => sum + value, 0) / levels.length
  const centred = levels.map((value) => value - mean)
  const magnitude = Math.sqrt(centred.reduce((sum, value) => sum + value * value, 0))
  if (!(magnitude > floor)) return null
  return centred.map((value) => value / magnitude)
}

/** What each vowel's formants would look like through the same bands. */
const TEMPLATES: Record<Viseme, number[]> = Object.fromEntries(
  VISEMES.map((viseme) => {
    const [f1, f2] = FORMANTS[viseme]
    const levels = BAND_CENTRES.map((centre) => resonance(centre, f1) + F2_WEIGHT * resonance(centre, f2))
    // Templates are built from real formant frequencies, so they always have
    // contrast; the floor only guards the runtime profile.
    return [viseme, normalize(levels, 0) as number[]]
  }),
) as Record<Viseme, number[]>

/** How strongly a resonance at `formant` lifts the band centred at `hz`. */
function resonance(hz: number, formant: number): number {
  const distance = Math.log(hz / formant) / FORMANT_SPREAD
  return Math.exp(-0.5 * distance * distance)
}

/** Weight every shape by how well it matches, then normalise to sum to 1. */
function scoreVowels(heard: number[]): VisemeWeights {
  const scores: number[] = []
  let total = 0
  for (const viseme of VISEMES) {
    const template = TEMPLATES[viseme]
    // Both vectors are unit length, so this dot product is their correlation.
    let similarity = 0
    for (let band = 0; band < heard.length; band += 1) similarity += heard[band] * template[band]
    const score = Math.exp(similarity / DECISIVENESS)
    scores.push(score)
    total += score
  }
  if (!(total > 0) || !Number.isFinite(total)) return { ...NEUTRAL_VISEMES }
  const weights = {} as VisemeWeights
  VISEMES.forEach((viseme, index) => {
    weights[viseme] = scores[index] / total
  })
  return weights
}

/**
 * Blend toward a new shape at a rate a mouth can actually move.
 *
 * Per-frame formant estimates jitter, and a mouth that changes shape every
 * 16ms reads as a glitch rather than as speech. Roughly 60ms to cross most of
 * the way is near the limit of how fast real articulators travel.
 */
export function blendVisemes(from: VisemeWeights, to: VisemeWeights, alpha: number): VisemeWeights {
  const rate = Math.min(1, Math.max(0, alpha))
  const blended = {} as VisemeWeights
  for (const viseme of VISEMES) blended[viseme] = from[viseme] + (to[viseme] - from[viseme]) * rate
  return blended
}

/**
 * How far each shape opens a rig with a single mouth-opening parameter.
 *
 * VRM ships an authored morph per vowel, so aperture is already baked into the
 * shape. Cubism's `ParamMouthOpenY` is one number, and driving it to the same
 * height for 衣 as for 啊 is exactly the "always wide open" look this set out
 * to fix -- close vowels barely part the lips.
 */
const APERTURE: VisemeWeights = { aa: 1, ih: 0.42, ou: 0.38, ee: 0.68, oh: 0.76 }

/**
 * How wide or round each shape pulls a rig's mouth-form parameter.
 *
 * Cubism's `ParamMouthForm` runs -1 (rounded, pursed) to 1 (spread, wide).
 */
const FORM: VisemeWeights = { aa: 0.1, ih: 0.9, ou: -0.9, ee: 0.7, oh: -0.6 }

/**
 * Collapse a shape distribution onto the two parameters a Cubism rig exposes.
 *
 * `open` scales whatever opening the caller already computed, so the level
 * still comes from the audio; this only says how much of that opening this
 * particular vowel is entitled to.
 */
export function mouthShape(weights: VisemeWeights): { open: number; form: number } {
  let open = 0
  let form = 0
  for (const viseme of VISEMES) {
    open += APERTURE[viseme] * weights[viseme]
    form += FORM[viseme] * weights[viseme]
  }
  return { open, form }
}
