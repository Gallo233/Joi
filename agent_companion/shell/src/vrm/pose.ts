export type VrmMotionBoneName =
  | 'hips'
  | 'spine'
  | 'chest'
  | 'upperChest'
  | 'neck'
  | 'head'
  | 'leftShoulder'
  | 'leftUpperArm'
  | 'leftLowerArm'
  | 'leftHand'
  | 'rightShoulder'
  | 'rightUpperArm'
  | 'rightLowerArm'
  | 'rightHand'
  | 'leftUpperLeg'
  | 'leftLowerLeg'
  | 'rightUpperLeg'
  | 'rightLowerLeg'

export type VrmBoneRotation = [number, number, number]

// VRM normalized humanoids use a T-pose as their zero rotation. Keeping a
// small gap beside the torso avoids clipping sleeves and wide costumes while
// still reading as a relaxed standing pose.
export const VRM_NEUTRAL_ARM_DROP_RADIANS = 1.16

const NEUTRAL_VRM_ROTATIONS: Readonly<Partial<Record<VrmMotionBoneName, readonly [number, number, number]>>> = {
  leftUpperArm: [0, 0, VRM_NEUTRAL_ARM_DROP_RADIANS],
  rightUpperArm: [0, 0, -VRM_NEUTRAL_ARM_DROP_RADIANS],
}

export function neutralVrmRotation(name: VrmMotionBoneName): VrmBoneRotation {
  const value = NEUTRAL_VRM_ROTATIONS[name] || [0, 0, 0]
  return [value[0], value[1], value[2]]
}

export function createNeutralVrmRotations(): Map<VrmMotionBoneName, VrmBoneRotation> {
  const rotations = new Map<VrmMotionBoneName, VrmBoneRotation>()
  for (const name of Object.keys(NEUTRAL_VRM_ROTATIONS) as VrmMotionBoneName[]) {
    rotations.set(name, neutralVrmRotation(name))
  }
  return rotations
}

export function addVrmRotation(
  current: VrmBoneRotation,
  delta: VrmBoneRotation,
  weight = 1,
): VrmBoneRotation {
  return [
    current[0] + delta[0] * weight,
    current[1] + delta[1] * weight,
    current[2] + delta[2] * weight,
  ]
}

/** Blend an authored procedural pose as an absolute target from the T-pose. */
export function blendVrmMotionTarget(
  name: VrmMotionBoneName,
  current: VrmBoneRotation,
  target: VrmBoneRotation,
  weight: number,
): VrmBoneRotation {
  const neutral = neutralVrmRotation(name)
  return [
    current[0] + (target[0] - neutral[0]) * weight,
    current[1] + (target[1] - neutral[1]) * weight,
    current[2] + (target[2] - neutral[2]) * weight,
  ]
}
