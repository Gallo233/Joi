/**
 * Types for the vendored `thinking-orbs` painters.
 *
 * Written here rather than vendored from upstream's `index.d.ts`, which
 * imports React types for the component this copy deliberately drops.
 */

export type OrbMode =
  | 'orbits' | 'globe' | 'rubik' | 'wave' | 'web' | 'braid' | 'ribbon' | 'ring' | 'morph'

export type OrbState =
  | 'working' | 'searching' | 'solving' | 'listening'
  | 'connecting' | 'weaving' | 'composing' | 'breathing' | 'shaping'

/** Upstream ships two hand-tuned designs, not one design scaled. */
export type OrbSize = 64 | 20

/** One frame painter: draws a mode into a 2D context at CSS-px `size`. */
export type ModeDraw = (
  context: CanvasRenderingContext2D,
  size: number,
  time: number,
  dark: boolean,
  options: Record<string, number | undefined>,
) => void

export declare const MODE_DRAWS: Record<OrbMode, ModeDraw>
export declare const STATE_TO_MODE: Record<OrbState, OrbMode>

export declare function resolvePreset(
  state: OrbState,
  size: OrbSize,
): { mode: OrbMode; speed: number; opts: Record<string, number | undefined> }
