/**
 * Where the MMD camera stands, as arithmetic rather than as a side effect.
 *
 * This lived inside the render loop and had the meaning of `compact` inverted:
 * the flag is `isCompactMode || characterFullBody`, so it says "show the whole
 * body", and the stage read it as "frame a bust". The desk pet therefore showed
 * a cropped head, and so did the desktop stage until someone switched the
 * full-body toggle *off*. Nothing could catch that while the rule was three
 * lines wedged between a camera and a renderer, so it is a function now.
 */

export interface MmdFramingInput {
  /** Height of the model's own bounding box, in scene units. */
  height: number
  /** Y of the top of the head, used as the look-at point for a bust. */
  headY: number
  /** True when the whole body should be in frame. */
  fullBody: boolean
  /** User zoom; larger means closer. */
  zoom: number
  /** Canvas width / height. */
  aspect: number
  /** Vertical field of view in degrees. */
  fov: number
}

export interface MmdFraming {
  /** Y the camera looks at, and sits at. */
  target: number
  /** Distance from the model on +Z. */
  distance: number
}

/** How much of the canvas height the framed model fills. */
export const MODEL_FRACTION = 0.62

/**
 * Below this aspect the frame is tall and thin, and vertical FOV alone crops
 * the sides. An MMD silhouette is much wider than its rig -- twin tails and
 * long sleeves sit well outside the shoulders -- and the desk pet is the
 * narrowest canvas in the app.
 */
export const NARROW_ASPECT = 0.72

export function mmdFraming({ height, headY, fullBody, zoom, aspect, fov }: MmdFramingInput): MmdFraming {
  const safeHeight = Math.max(height, 0.001)
  const safeAspect = Math.max(0.35, aspect || 1)
  const target = fullBody ? safeHeight * 0.55 : headY
  const span = (fullBody ? safeHeight : safeHeight * 0.42) / MODEL_FRACTION
  const widthCorrection = safeAspect < NARROW_ASPECT ? NARROW_ASPECT / safeAspect : 1
  const distance = (span * widthCorrection) / (2 * Math.tan((fov * Math.PI) / 360)) / Math.max(zoom, 0.2)
  return { target, distance }
}
