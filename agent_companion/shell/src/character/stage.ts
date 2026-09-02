/**
 * What the stage knows about a character, independent of how it is drawn.
 *
 * Every renderer used to import its emotion type and its mapping shape from the
 * Live2D module, so adding a format meant depending on an unrelated one, and the
 * "what does worried look like" answer lived inside whichever renderer happened
 * to implement it. The stage now owns the vocabulary and one table per format
 * translates it; a renderer never decides what an emotion means, only how to
 * show it.
 */

import type { CharacterMotionMapping, CharacterMotionRequest } from '../characterMotion'

/** The closed set of moods Core may ask for. */
export type StageEmotion = 'happy' | 'thinking' | 'alert' | 'worried' | 'serious' | 'neutral'

export const STAGE_EMOTIONS: readonly StageEmotion[] = ['happy', 'thinking', 'alert', 'worried', 'serious', 'neutral']

export function isStageEmotion(value: unknown): value is StageEmotion {
  return typeof value === 'string' && (STAGE_EMOTIONS as readonly string[]).includes(value)
}

/**
 * Formats a character package can be drawn with.
 *
 * `procedural3d` carries no model file: it is this project's own renderer for a
 * character that has no authored body, which is why it is a format rather than
 * a fallback.
 */
export type StageModelFormat = 'static' | 'live2d' | 'vrm' | 'procedural3d' | 'tachie' | 'mmd' | 'spine'

export const STAGE_MODEL_FORMATS: readonly StageModelFormat[] = [
  'static',
  'live2d',
  'vrm',
  'procedural3d',
  'tachie',
  'mmd',
  'spine',
]

export function isStageModelFormat(value: unknown): value is StageModelFormat {
  return typeof value === 'string' && (STAGE_MODEL_FORMATS as readonly string[]).includes(value)
}

/** Short label shown on the stage while a model is mounted. */
export const STAGE_FORMAT_LABEL: Record<StageModelFormat, string> = {
  static: '立绘',
  live2d: 'Live2D',
  vrm: 'VRM',
  procedural3d: '3D',
  tachie: '立绘',
  mmd: 'MMD',
  spine: 'Spine',
}

/** Formats that draw with no model file of their own. */
export const STAGE_FORMATS_WITHOUT_MODEL_URL: readonly StageModelFormat[] = ['procedural3d']

export interface StageExpressionMapping {
  emotion?: string
  /** Live2D: the model's own expression id. */
  expression_id?: string
  motion_group?: string
  motion_index?: number | string
  /** Tachie: this mood's artwork, package-relative. */
  image?: string
  /** Tachie: the same artwork as a URL the Shell can fetch, published by Core. */
  image_url?: string
}

export interface StageRuntimeMapping {
  /** Live2D: emotion -> the model's own expression id or motion group. */
  expressions?: StageExpressionMapping[]
  motions?: CharacterMotionMapping[]
  lipSync?: { parameter?: string }
  /**
   * Semantic motion name -> `.vrma` URL, for VRM characters whose package
   * ships authored clips. Live2D ignores this; its motions come from the
   * model's own motion groups.
   */
  animations?: Record<string, string>
}

/** What every renderer must be able to do, whatever it draws. */
export interface StageController {
  resize: () => void
  setCompact: (compact: boolean) => void
  setEmotion: (emotion: StageEmotion) => void
  playMotion: (request: CharacterMotionRequest) => void
  destroy: () => void
  /**
   * Stage zoom applied to the model's own camera. Optional: formats the stage
   * scales itself do not implement it, so callers must feature-check.
   */
  setZoom?: (zoom: number) => void
}

/**
 * How each format shows a mood.
 *
 * One row per emotion, one column per format that can express it without the
 * character package saying anything. A package may still override the Live2D
 * column through `StageRuntimeMapping.expressions`; the rest are defaults the
 * renderer applies when the model offers a matching channel.
 *
 * `vrm` names a VRM expression preset and the weight to hold it at. `mmd` names
 * a morph, using the vocabulary Japanese MMD models conventionally ship
 * (「笑い」「困る」「怒り」), falling back to nothing when a model lacks it.
 */
export interface StageEmotionRow {
  vrm: readonly [string, number] | null
  mmd: readonly [string, number] | null
}

// The VRM weights are deliberately low and were tuned against real models: a
// VRM expression at full strength morphs the eyes shut, and a value in the
// middle leaves the lids half down, which reads as a squint rather than a
// smile. Do not raise them without looking at a model.
//
// The MMD morph names follow the vocabulary Japanese models conventionally
// ship. They are untuned starting points; a model that lacks a named morph
// simply shows nothing for that mood.
export const STAGE_EMOTION_TABLE: Record<StageEmotion, StageEmotionRow> = {
  happy: { vrm: ['happy', 0.42], mmd: ['笑い', 0.6] },
  thinking: { vrm: ['relaxed', 0.34], mmd: ['困る', 0.35] },
  alert: { vrm: ['surprised', 0.5], mmd: ['驚き', 0.5] },
  worried: { vrm: ['sad', 0.45], mmd: ['困る', 0.6] },
  serious: { vrm: ['angry', 0.18], mmd: ['怒り', 0.3] },
  neutral: { vrm: null, mmd: null },
}

/**
 * Whether a thumbnail can be drawn for this model at all.
 *
 * Pure, and separate from the renderer that would draw it, so the rule can be
 * checked without a DOM: `spine` has no licensed runtime to mount, and a format
 * that needs a model file cannot preview one it was not given.
 */
export function canRenderThumbnail(format: StageModelFormat, modelUrl: string): boolean {
  if (format === 'spine') return false
  if (STAGE_FORMATS_WITHOUT_MODEL_URL.includes(format)) return true
  return Boolean(modelUrl)
}

/** The expression channel a format should drive for this mood, if it has one. */
export function stageEmotionShape(
  format: Extract<StageModelFormat, 'vrm' | 'mmd'>,
  emotion: StageEmotion,
): readonly [string, number] | null {
  return STAGE_EMOTION_TABLE[emotion]?.[format] ?? null
}
