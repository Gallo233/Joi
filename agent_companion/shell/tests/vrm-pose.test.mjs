import assert from 'node:assert/strict'
import test from 'node:test'

import {
  VRM_NEUTRAL_ARM_DROP_RADIANS,
  addVrmRotation,
  blendVrmMotionTarget,
  createNeutralVrmRotations,
  neutralVrmRotation,
} from '../src/vrm/pose.ts'

test('neutral VRM pose lowers both arms symmetrically', () => {
  const rotations = createNeutralVrmRotations()
  const left = rotations.get('leftUpperArm')
  const right = rotations.get('rightUpperArm')

  assert.deepEqual(left, [0, 0, VRM_NEUTRAL_ARM_DROP_RADIANS])
  assert.deepEqual(right, [0, 0, -VRM_NEUTRAL_ARM_DROP_RADIANS])
  assert.ok(VRM_NEUTRAL_ARM_DROP_RADIANS > 1 && VRM_NEUTRAL_ARM_DROP_RADIANS < Math.PI / 2)
})

test('neutral rotations are fresh values and missing bones stay at zero', () => {
  const first = neutralVrmRotation('leftUpperArm')
  first[2] = 99

  assert.deepEqual(neutralVrmRotation('leftUpperArm'), [0, 0, VRM_NEUTRAL_ARM_DROP_RADIANS])
  assert.deepEqual(neutralVrmRotation('head'), [0, 0, 0])
})

test('motion targets replace the neutral arm pose instead of stacking on it', () => {
  const neutral = neutralVrmRotation('leftUpperArm')
  const target = [0, 0, 0.68]

  assert.deepEqual(blendVrmMotionTarget('leftUpperArm', neutral, target, 0), neutral)
  assert.deepEqual(blendVrmMotionTarget('leftUpperArm', neutral, target, 1), target)
})

test('idle deltas remain additive on top of the neutral pose', () => {
  assert.deepEqual(addVrmRotation([0, 0, 0], [0.01, -0.02, 0.03], 0.5), [0.005, -0.01, 0.015])
})
