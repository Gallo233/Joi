"""Generate original character clips in VRM Animation (.vrma) format.

Joi needs default motion it is allowed to ship. The freely available sample
clips are usable but their terms forbid redistributing them in a form that can
be extracted again, which is exactly what putting one inside a character
package would do. These are authored here instead: plain humanoid rotation
curves, no third-party content.

A .vrma is a glTF 2.0 file whose `VRMC_vrm_animation` extension maps humanoid
bone names onto nodes; the animation channels then target those nodes. Because
the rotations are expressed against the normalized humanoid rig rather than one
model's skeleton, the same file drives any VRM.

Only rotations are written. Hips translation would be rescaled by the target
model's hip height, which is a good way to make a short character skate across
the floor, and none of these clips leave the spot.

    python3 tools/make_vrma.py <output-directory> [clip ...]
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import struct
import sys


FPS = 30

# Matches the arm drop the procedural fallback uses, so switching between an
# authored clip and the fallback does not jump.
ARM_DROP = 1.16

# Parent -> children, in the order the nodes are written. Fingers are included
# because a hand gesture without them is just a fist waved at the camera.
SKELETON: dict[str, list[str]] = {
    "hips": ["spine", "leftUpperLeg", "rightUpperLeg"],
    "spine": ["chest"],
    "chest": ["upperChest"],
    "upperChest": ["neck", "leftShoulder", "rightShoulder"],
    "neck": ["head"],
    "head": [],
    "leftShoulder": ["leftUpperArm"],
    "leftUpperArm": ["leftLowerArm"],
    "leftLowerArm": ["leftHand"],
    "leftHand": [],
    "rightShoulder": ["rightUpperArm"],
    "rightUpperArm": ["rightLowerArm"],
    "rightLowerArm": ["rightHand"],
    "rightHand": [
        "rightThumbMetacarpal",
        "rightIndexProximal",
        "rightMiddleProximal",
        "rightRingProximal",
        "rightLittleProximal",
    ],
    "rightThumbMetacarpal": ["rightThumbProximal"],
    "rightThumbProximal": ["rightThumbDistal"],
    "rightThumbDistal": [],
    "rightIndexProximal": ["rightIndexIntermediate"],
    "rightIndexIntermediate": ["rightIndexDistal"],
    "rightIndexDistal": [],
    "rightMiddleProximal": ["rightMiddleIntermediate"],
    "rightMiddleIntermediate": ["rightMiddleDistal"],
    "rightMiddleDistal": [],
    "rightRingProximal": ["rightRingIntermediate"],
    "rightRingIntermediate": ["rightRingDistal"],
    "rightRingDistal": [],
    "rightLittleProximal": ["rightLittleIntermediate"],
    "rightLittleIntermediate": ["rightLittleDistal"],
    "rightLittleDistal": [],
    "leftUpperLeg": ["leftLowerLeg"],
    "leftLowerLeg": ["leftFoot"],
    "leftFoot": [],
    "rightUpperLeg": ["rightLowerLeg"],
    "rightLowerLeg": ["rightFoot"],
    "rightFoot": [],
}

# How far a finger folds when it is not part of the gesture.
CURLED = 1.25


def to_vrm1_space(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Convert a pose written in VRM 0 bone space into VRM 1 bone space.

    A .vrma is always interpreted as VRM 1.0: three-vrm applies its rotations
    unchanged to a 1.0 model and negates the quaternion's x and z for a 0.x one,
    which is the 180-degree turn between the two conventions. The poses below
    are written to match `pose.ts`, whose numbers were tuned against a VRM 0
    model, so they are flipped once here rather than every value being
    rewritten by hand -- and so the two files stay comparable.
    """

    return (-x, y, -z)


def euler_to_quaternion(x: float, y: float, z: float) -> tuple[float, float, float, float]:
    """XYZ intrinsic euler angles to a glTF (x, y, z, w) quaternion."""

    cx, sx = math.cos(x * 0.5), math.sin(x * 0.5)
    cy, sy = math.cos(y * 0.5), math.sin(y * 0.5)
    cz, sz = math.cos(z * 0.5), math.sin(z * 0.5)
    return (
        sx * cy * cz + cx * sy * sz,
        cx * sy * cz - sx * cy * sz,
        cx * cy * sz + sx * sy * cz,
        cx * cy * cz - sx * sy * sz,
    )


def ease(t: float) -> float:
    """Smoothstep, so a gesture starts and lands softly instead of snapping."""

    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def rest_pose(bone: str) -> tuple[float, float, float]:
    """Arms at the sides, everything else neutral."""

    if bone == "leftUpperArm":
        return (0.0, 0.0, ARM_DROP)
    if bone == "rightUpperArm":
        return (0.0, 0.0, -ARM_DROP)
    if bone == "leftLowerArm":
        return (0.0, 0.0, 0.075)
    if bone == "rightLowerArm":
        return (0.0, 0.0, -0.075)
    return (0.0, 0.0, 0.0)


def blend(a: tuple[float, float, float], b: tuple[float, float, float], t: float) -> tuple[float, float, float]:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def idle_pose(bone: str, phase: float) -> tuple[float, float, float]:
    """A standing idle: breathing, a slow weight shift, no travel."""

    breath = math.sin(phase * 2 * math.pi)
    sway = math.sin(phase * 2 * math.pi)
    shift = math.sin(phase * math.pi)

    table = {
        "hips": (0.0, sway * 0.018, shift * 0.020),
        "spine": (breath * 0.012, sway * -0.010, shift * -0.008),
        "chest": (breath * 0.020, 0.0, shift * -0.006),
        "upperChest": (breath * 0.014, 0.0, 0.0),
        "neck": (breath * -0.010, sway * 0.014, 0.0),
        "head": (breath * -0.008, sway * 0.018, shift * 0.010),
        "leftShoulder": (0.0, 0.0, breath * 0.014),
        "rightShoulder": (0.0, 0.0, breath * -0.014),
        "leftUpperArm": (0.0, 0.0, ARM_DROP - breath * 0.030),
        "rightUpperArm": (0.0, 0.0, -ARM_DROP + breath * 0.030),
        "leftLowerArm": (0.0, breath * 0.030, 0.075),
        "rightLowerArm": (0.0, breath * -0.030, -0.075),
        "leftHand": (0.0, 0.0, breath * 0.020),
        "rightHand": (0.0, 0.0, breath * -0.020),
        "leftUpperLeg": (shift * 0.010, 0.0, shift * 0.006),
        "rightUpperLeg": (shift * -0.010, 0.0, shift * 0.006),
        "leftLowerLeg": (abs(shift) * 0.008, 0.0, 0.0),
        "rightLowerLeg": (abs(shift) * 0.008, 0.0, 0.0),
    }
    return table.get(bone, rest_pose(bone))


def peace_hold(bone: str, wobble: float) -> tuple[float, float, float]:
    """Right hand up beside the face, index and middle out, head tilted in."""

    if bone == "rightShoulder":
        return (0.0, 0.0, -0.42)
    if bone == "rightUpperArm":
        # Close to horizontal, so folding the elbow brings the hand up beside
        # the head rather than in front of the chest.
        return (-0.45, 0.0, -0.26 + wobble * 0.03)
    if bone == "rightLowerArm":
        return (0.0, -1.90, -0.20)
    if bone == "rightHand":
        return (0.0, 0.0, -0.15 + wobble * 0.10)
    if bone == "rightMiddleProximal":
        return (0.0, 0.0, -0.16)
    if bone.startswith("rightIndex") or bone.startswith("rightMiddle"):
        return (0.0, 0.0, 0.0)
    if bone.startswith("rightRing") or bone.startswith("rightLittle"):
        return (0.0, 0.0, CURLED)
    if bone.startswith("rightThumb"):
        return (0.0, 0.0, 0.55)
    if bone == "head":
        return (0.04, -0.16, 0.16 + wobble * 0.02)
    if bone == "neck":
        return (0.0, -0.08, 0.08)
    if bone == "upperChest":
        return (0.0, -0.10, 0.0)
    if bone == "chest":
        return (-0.04, -0.08, 0.0)
    if bone == "hips":
        return (0.0, -0.06, 0.0)
    return rest_pose(bone)


def wave_hold(bone: str, swing: float) -> tuple[float, float, float]:
    """Right arm raised high, hand swinging side to side."""

    if bone == "rightShoulder":
        return (0.0, 0.0, -0.34)
    if bone == "rightUpperArm":
        return (-0.20, 0.0, -0.30)
    if bone == "rightLowerArm":
        return (0.0, -1.20, -0.15)
    if bone == "rightHand":
        return (0.0, 0.0, swing * 0.45)
    if bone.startswith("rightThumb"):
        return (0.0, 0.0, 0.35)
    if bone == "head":
        return (0.02, -0.12, swing * 0.05)
    if bone == "upperChest":
        return (0.0, -0.08, 0.0)
    return rest_pose(bone)


def gesture(hold, duration: float, rise: float, fall: float):
    """Wrap a held pose in a rise from and a return to the resting pose."""

    def pose(bone: str, phase: float) -> tuple[float, float, float]:
        seconds = phase * duration
        wobble = math.sin(seconds * 7.0)
        target = hold(bone, wobble)
        if seconds < rise:
            return blend(rest_pose(bone), target, ease(seconds / rise))
        if seconds > duration - fall:
            return blend(target, rest_pose(bone), ease((seconds - (duration - fall)) / fall))
        return target

    return pose


# name -> (pose function, seconds). Names are Joi's semantic motions, so a
# package can map them straight onto `character.perform`.
CLIPS = {
    "idle": (idle_pose, 6.0),
    "happy": (gesture(peace_hold, 2.2, 0.45, 0.5), 2.2),
    "greet": (gesture(wave_hold, 2.2, 0.4, 0.5), 2.2),
}


def build(name: str, output: Path) -> None:
    pose_at, seconds = CLIPS[name]
    bones = list(SKELETON)
    index_of = {bone: index for index, bone in enumerate(bones)}
    frames = int(FPS * seconds)
    times = [round(frame / FPS, 6) for frame in range(frames + 1)]

    nodes: list[dict] = []
    for bone in bones:
        node: dict = {"name": bone}
        children = [index_of[child] for child in SKELETON[bone]]
        if children:
            node["children"] = children
        nodes.append(node)
    # Rest height only matters as the reference for hips translation, which
    # these clips do not write; it is present so the file is well formed.
    nodes[index_of["hips"]]["translation"] = [0.0, 1.0, 0.0]

    payload = bytearray()
    accessors: list[dict] = []
    views: list[dict] = []

    def add_accessor(values: list[float], stride: int, kind: str) -> int:
        while len(payload) % 4:
            payload.append(0)
        offset = len(payload)
        for value in values:
            payload.extend(struct.pack("<f", value))
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(payload) - offset})
        accessor: dict = {
            "bufferView": len(views) - 1,
            "componentType": 5126,
            "count": len(values) // stride,
            "type": kind,
        }
        if kind == "SCALAR":
            accessor["min"] = [min(values)]
            accessor["max"] = [max(values)]
        accessors.append(accessor)
        return len(accessors) - 1

    time_accessor = add_accessor(times, 1, "SCALAR")

    channels: list[dict] = []
    samplers: list[dict] = []
    for bone in bones:
        values: list[float] = []
        for frame in range(frames + 1):
            # A looping clip wraps so the seam is invisible; a one-shot plays
            # to its final frame, where it has already returned to rest.
            phase = (frame % frames) / frames if name == "idle" else frame / frames
            values.extend(euler_to_quaternion(*to_vrm1_space(*pose_at(bone, phase))))
        samplers.append({"input": time_accessor, "output": add_accessor(values, 4, "VEC4"), "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1, "target": {"node": index_of[bone], "path": "rotation"}})

    gltf = {
        "asset": {"version": "2.0", "generator": "Joi make_vrma"},
        "extensionsUsed": ["VRMC_vrm_animation"],
        "extensions": {
            "VRMC_vrm_animation": {
                "specVersion": "1.0",
                "humanoid": {"humanBones": {bone: {"node": index_of[bone]} for bone in bones}},
            }
        },
        "scene": 0,
        "scenes": [{"nodes": [index_of["hips"]]}],
        "nodes": nodes,
        "buffers": [{"byteLength": len(payload)}],
        "bufferViews": views,
        "accessors": accessors,
        "animations": [{"name": name, "channels": channels, "samplers": samplers}],
    }

    header = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    header += b" " * (-len(header) % 4)
    body = bytes(payload) + b"\0" * (-len(payload) % 4)
    glb = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(header) + 8 + len(body))
    glb += struct.pack("<II", len(header), 0x4E4F534A) + header
    glb += struct.pack("<II", len(body), 0x004E4942) + body
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(glb)
    print(f"wrote {output} ({len(glb)} bytes, {len(bones)} bones, {frames} frames, {seconds}s)")


if __name__ == "__main__":
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    wanted = sys.argv[2:] or list(CLIPS)
    for clip in wanted:
        build(clip, directory / f"{clip}.vrma")
