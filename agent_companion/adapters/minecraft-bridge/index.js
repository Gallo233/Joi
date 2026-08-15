#!/usr/bin/env node
'use strict'

const crypto = require('crypto')
const fs = require('fs')
const path = require('path')
const Module = require('module')
const readline = require('readline')

const reviewedVendorDir = String(process.env.JOI_MINECRAFT_VENDOR_DIR || '')
if (
  reviewedVendorDir
  && path.isAbsolute(reviewedVendorDir)
  && fs.existsSync(path.join(reviewedVendorDir, 'minecraft-data', 'package.json'))
) {
  process.env.NODE_PATH = [reviewedVendorDir, process.env.NODE_PATH || ''].filter(Boolean).join(path.delimiter)
  Module._initPaths()
}

const protocol = 'joi.game_adapter'
const version = 2
const fakeMode = process.env.JOI_MINECRAFT_FAKE === '1'
const capabilities = [
  'observe',
  'inventory',
  'follow_player',
  'come_to_player',
  'collect',
  'mine',
  'craft',
  'eat',
  'place_blueprint',
  'deposit',
  'attack',
  'flee',
  'guard',
]
const allowedTypes = new Set([
  'session.start',
  'session.stop',
  'goal.submit',
  'goal.pause',
  'goal.resume',
  'goal.cancel',
  'state.snapshot.request',
])
const inputBase = new Set(['protocol', 'version', 'type', 'session_id', 'message_id', 'sequence', 'payload'])
const forbiddenIntentKeys = new Set(['code', 'command', 'commands', 'eval', 'javascript', 'js', 'lua', 'python', 'script', 'shell'])
const dangerousBlocks = new Set([
  'tnt', 'fire', 'lava', 'lava_bucket', 'end_crystal', 'respawn_anchor', 'bedrock',
  'command_block', 'chain_command_block', 'repeating_command_block', 'structure_block', 'jigsaw',
])

function localBlockName(value) {
  return String(value || '').toLowerCase().split(':').pop()
}

// Minecraft ships releases before mineflayer marks them tested, even though minecraft-data
// already carries their protocol. Without this the bridge refuses such a world before it
// opens a socket, which reads in the shell as "waiting to connect" forever. The gate is
// lifted only for releases this bridge carries a verified shim for; anything newer than the
// last entry still fails closed with minecraft_version_unsupported. Keep the list ascending:
// the last entry becomes the supported ceiling.
const compatibleVersions = ['26.1']

function applyVersionCompat() {
  const protocolVersions = require('minecraft-protocol/src/version')
  const botVersions = require('mineflayer/lib/version')
  for (const release of compatibleVersions) {
    if (!protocolVersions.supportedVersions.includes(release)) protocolVersions.supportedVersions.push(release)
    if (!botVersions.testedVersions.includes(release)) botVersions.testedVersions.push(release)
  }
  // mineflayer's loader reads this once, so it has to be raised before mineflayer loads.
  botVersions.latestSupportedVersion = botVersions.testedVersions[botVersions.testedVersions.length - 1]
}

let mineflayer = null
let pathfinder = null
let Movements = null
let goals = null
if (!fakeMode) {
  applyVersionCompat()
  mineflayer = require('mineflayer')
  ;({ pathfinder, Movements, goals } = require('mineflayer-pathfinder'))
}

const bridgeInstanceId = `bridge-${crypto.randomBytes(12).toString('hex')}`
let outputSequence = 0
let expectedInputSequence = 0
let locked = false
let currentSessionId = ''
let sessionConfig = null
let bot = null
let activeGoal = null
let closing = false
let recoveryRequired = false
let scopeAnchor = null
const seenMessages = new Map()
const cachedResponses = new Map()
const MAX_CACHED_MESSAGES = 4096

// Long voice sessions stream many requests and unsolicited events. Replay
// protection only needs the most recent window: message IDs are random and
// Core never retries old ones, so evicting the oldest entries keeps both
// maps bounded without weakening the replay contract.
function capCache(map) {
  if (map.size > MAX_CACHED_MESSAGES) map.delete(map.keys().next().value)
}
const fakeState = {
  position: { x: 0, y: 64, z: 0 },
  dimension: 'overworld',
  health: 20,
  food: 20,
  inventory: new Map([['cobblestone', 16], ['oak_planks', 16], ['oak_log', 4], ['bread', 2]]),
  blocksChanged: 0,
}

function digest(value) {
  return crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex')
}

function emit(type, payload, request = null, options = {}) {
  outputSequence += 1
  const envelope = {
    protocol,
    version,
    type,
    session_id: options.sessionId !== undefined ? options.sessionId : currentSessionId,
    message_id: `bridge-${crypto.randomBytes(16).toString('hex')}`,
    sequence: outputSequence,
    bridge_instance_id: bridgeInstanceId,
    reply_to: options.replyTo !== undefined ? options.replyTo : String(request?.message_id || ''),
    payload: payload && typeof payload === 'object' && !Array.isArray(payload) ? payload : {},
  }
  const goalId = options.goalId !== undefined ? options.goalId : request?.goal_id
  if (goalId) envelope.goal_id = String(goalId)
  process.stdout.write(`${JSON.stringify(envelope)}\n`)
  return { type, payload: envelope.payload, goalId: envelope.goal_id || '' }
}

function cacheAndEmit(request, type, payload, options = {}) {
  const cached = { type, payload, goalId: options.goalId !== undefined ? options.goalId : request.goal_id || '' }
  cachedResponses.set(request.message_id, cached)
  capCache(cachedResponses)
  emit(type, payload, request, options)
}

function replay(request) {
  const cached = cachedResponses.get(request.message_id)
  if (!cached) {
    emit('error', { error: 'duplicate_in_progress' }, request, { goalId: request.goal_id || '' })
    return
  }
  emit(cached.type, { ...cached.payload, replayed: true }, request, { goalId: cached.goalId })
}

function exactFields(value, required, optional = []) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const keys = Object.keys(value)
  return required.every((key) => keys.includes(key)) && keys.every((key) => required.includes(key) || optional.includes(key))
}

function validateEnvelope(request) {
  if (!request || typeof request !== 'object' || Array.isArray(request)) return 'invalid_bridge_envelope'
  const keys = Object.keys(request)
  const allowed = new Set(inputBase)
  if (String(request.type || '').startsWith('goal.')) allowed.add('goal_id')
  if (!Array.from(inputBase).every((key) => keys.includes(key)) || keys.some((key) => !allowed.has(key))) return 'invalid_bridge_envelope'
  if (request.protocol !== protocol || request.version !== version) return 'bridge_protocol_mismatch'
  if (!allowedTypes.has(request.type)) return 'unknown_bridge_message_type'
  if (!request.message_id || typeof request.message_id !== 'string' || !Number.isInteger(request.sequence) || request.sequence < 1) return 'invalid_bridge_envelope'
  if (!request.payload || typeof request.payload !== 'object' || Array.isArray(request.payload)) return 'invalid_bridge_envelope'
  if (request.type.startsWith('goal.') && (!request.goal_id || typeof request.goal_id !== 'string')) return 'missing_goal_id'
  if (request.type === 'session.start') {
    if (currentSessionId || !request.session_id) return 'invalid_session_start'
  } else if (!currentSessionId || request.session_id !== currentSessionId) {
    return 'bridge_session_mismatch'
  }
  if (request.type === 'session.start' && !exactFields(request.payload, ['mode', 'scope', 'budget'])) return 'invalid_session_payload'
  if (request.type === 'goal.submit' && !exactFields(request.payload, ['intent'])) return 'invalid_goal_payload'
  if (['session.stop', 'goal.pause', 'goal.resume', 'goal.cancel', 'state.snapshot.request'].includes(request.type) && !exactFields(request.payload, [])) return 'invalid_control_payload'
  return ''
}

function rejectCodeFields(value) {
  if (Array.isArray(value)) return value.some(rejectCodeFields)
  if (!value || typeof value !== 'object') return false
  return Object.entries(value).some(([key, child]) => forbiddenIntentKeys.has(String(key).toLowerCase()) || rejectCodeFields(child))
}

function validateIntent(intent) {
  if (!intent || typeof intent !== 'object' || Array.isArray(intent) || rejectCodeFields(intent)) return 'invalid_game_intent'
  if (!capabilities.includes(intent.action)) return 'unknown_game_action'
  const fields = {
    observe: [['action', 'dimension', 'radius'], ['action']],
    inventory: [['action'], ['action']],
    follow_player: [['action', 'player', 'distance', 'duration_seconds'], ['action', 'player']],
    come_to_player: [['action', 'player', 'distance'], ['action', 'player']],
    collect: [['action', 'block', 'count', 'radius', 'dimension'], ['action', 'block']],
    mine: [['action', 'block', 'count', 'radius', 'dimension'], ['action', 'block']],
    craft: [['action', 'item', 'count'], ['action', 'item']],
    eat: [['action', 'item'], ['action']],
    place_blueprint: [['action', 'anchor', 'dimension', 'player', 'blocks'], ['action', 'anchor', 'blocks']],
    deposit: [['action', 'container', 'items', 'radius', 'dimension'], ['action', 'items']],
    attack: [['action', 'count', 'radius', 'dimension'], ['action']],
    flee: [['action', 'distance', 'duration_seconds', 'dimension'], ['action']],
    guard: [['action', 'dimension'], ['action']],
  }[intent.action]
  const keys = Object.keys(intent)
  if (!fields[1].every((key) => keys.includes(key)) || keys.some((key) => !fields[0].includes(key))) return 'unexpected_intent_field'
  if (intent.action === 'place_blueprint') {
    if (!Array.isArray(intent.blocks) || intent.blocks.length < 1 || intent.blocks.length > 128) return 'invalid_blueprint'
    const offsets = new Set()
    for (const row of intent.blocks) {
      if (!exactFields(row, ['offset', 'block']) || !Array.isArray(row.offset) || row.offset.length !== 3 || dangerousBlocks.has(localBlockName(row.block))) return 'invalid_blueprint'
      if (!row.offset.every((value) => Number.isInteger(value) && Math.abs(value) <= 64)) return 'invalid_blueprint'
      const key = row.offset.join(':')
      if (offsets.has(key)) return 'invalid_blueprint'
      offsets.add(key)
    }
  }
  return ''
}

function longToBigInt(value) {
  if (Array.isArray(value)) return BigInt.asIntN(64, BigInt(value[0]) << 32n) | BigInt(value[1])
  try {
    return BigInt(value ?? 0)
  } catch (_) {
    return 0n
  }
}

// 26.1 replaced update_time's `time`/`tickDayTime` fields with a `clockUpdates` array that is
// only populated when a clock actually changes. mineflayer's own time plugin assumes the old
// shape and throws on the first packet, so the bridge installs this reader instead. Older
// protocols keep taking the original path, and an empty update keeps the last known time.
function injectTimeCompat(target) {
  target.time = {
    doDaylightCycle: null,
    bigTime: 0n,
    time: 0,
    timeOfDay: 0,
    day: 0,
    isDay: true,
    moonPhase: 0,
    bigAge: 0n,
    age: 0,
  }
  target._client.on('update_time', (packet) => {
    const age = longToBigInt(packet.age)
    target.time.bigAge = age
    target.time.age = Number(age)
    let time = null
    let doDaylightCycle = null
    if (packet.time !== undefined) {
      time = longToBigInt(packet.time)
      doDaylightCycle = packet.tickDayTime !== undefined ? !!packet.tickDayTime : time >= 0n
    } else if (Array.isArray(packet.clockUpdates) && packet.clockUpdates.length) {
      // Clock 0 is the day-time clock the server sends alongside the world clock.
      const clock = packet.clockUpdates.find((row) => Number(row?.id) === 0) || packet.clockUpdates[0]
      time = longToBigInt(clock.totalTicks)
      doDaylightCycle = Number(clock.rate || 0) !== 0
    }
    if (time !== null) {
      const finalTime = doDaylightCycle ? time : (time < 0n ? -time : time)
      target.time.doDaylightCycle = doDaylightCycle
      target.time.bigTime = finalTime
      target.time.time = Number(finalTime)
      target.time.timeOfDay = target.time.time % 24000
      target.time.day = Math.floor(target.time.time / 24000)
      target.time.isDay = target.time.timeOfDay >= 0 && target.time.timeOfDay < 13000
      target.time.moonPhase = target.time.day % 8
    }
    target.emit('time')
  })
}

function botOptions() {
  const options = {
    host: process.env.JOI_MINECRAFT_HOST || 'localhost',
    port: Number(process.env.JOI_MINECRAFT_PORT || 25565),
    username: process.env.JOI_MINECRAFT_USERNAME || 'Joi',
    auth: process.env.JOI_MINECRAFT_AUTH || 'offline',
    version: process.env.JOI_MINECRAFT_VERSION || false,
    plugins: { time: false, joi_time: injectTimeCompat },
  }
  if (process.env.JOI_MINECRAFT_PROFILES_FOLDER) options.profilesFolder = process.env.JOI_MINECRAFT_PROFILES_FOLDER
  const viewer = viewerOptions()
  if (viewer) options.viewer = viewer
  return options
}

// Web POV (prismarine-viewer) is an optional, dev-only enhancement: when the
// package is absent the bridge still joins and plays, it just has no viewer.
function viewerOptions() {
  if (process.env.JOI_MINECRAFT_VIEWER !== '1') return null
  try {
    require('prismarine-viewer')
  } catch (_error) {
    process.stderr.write('joi.minecraft viewer: prismarine-viewer is not installed; POV viewer disabled\n')
    return null
  }
  return { port: Number(process.env.JOI_MINECRAFT_VIEWER_PORT || 3007), firstPerson: true }
}

function watchChat(candidate) {
  if (fakeMode || !candidate) return
  candidate.on('chat', (username, message) => {
    if (closing || recoveryRequired) return
    const text = String(message || '').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '').slice(0, 500)
    if (!text) return
    emit('chat.observed', { player: String(username || '').slice(0, 32), text }, null, { sessionId: currentSessionId })
  })
}

// Connect failures used to collapse into one opaque code, which left the shell showing
// "waiting to connect" with nothing to act on. Each cause now carries its own code, and the
// underlying message goes to stderr for the core log.
function connectFailureCode(error) {
  const message = String(error?.message || error || '')
  if (/not supported|unsupported|no data for version/i.test(message)) return 'minecraft_version_unsupported'
  if (/ECONNREFUSED/i.test(message)) return 'minecraft_connect_refused'
  if (/ENOTFOUND|EAI_AGAIN|EHOSTUNREACH|ENETUNREACH|ETIMEDOUT/i.test(message)) return 'minecraft_host_unreachable'
  return 'minecraft_connect_failed'
}

function createConnectedBot() {
  if (fakeMode) return Promise.resolve({ fake: true })
  return new Promise((resolve, reject) => {
    const connectTimeout = Math.max(1000, Math.min(Number(process.env.JOI_MINECRAFT_CONNECT_TIMEOUT_MS || 30000), 120000))
    let timeout = null
    let settled = false
    const fail = (code, detail) => {
      if (settled) return
      settled = true
      if (timeout) clearTimeout(timeout)
      process.stderr.write(`joi.minecraft connect: ${code}: ${String(detail || '').slice(0, 400)}\n`)
      reject(new Error(code))
    }
    let candidate = null
    try {
      // An unsupported server version throws here, before any socket is opened.
      candidate = mineflayer.createBot(botOptions())
    } catch (error) {
      fail(connectFailureCode(error), error?.message)
      return
    }
    timeout = setTimeout(() => {
      // Settle first: quitting can emit 'end', which would otherwise report the wrong cause.
      fail('spawn_timeout', `no spawn within ${connectTimeout}ms`)
      candidate.quit('spawn_timeout')
    }, connectTimeout)
    candidate.once('spawn', () => {
      settled = true
      clearTimeout(timeout)
      candidate.loadPlugin(pathfinder)
      configureSafeMovements(candidate)
      watchCombat(candidate)
      watchChat(candidate)
      resolve(candidate)
    })
    candidate.on('error', (error) => {
      // Kept attached after spawn: mineflayer throws on an unhandled 'error' event.
      if (settled) {
        process.stderr.write(`joi.minecraft session error: ${String(error?.message || error).slice(0, 400)}\n`)
        return
      }
      fail(connectFailureCode(error), error?.message)
    })
    candidate.once('kicked', (reason) => fail('minecraft_login_rejected', JSON.stringify(reason)))
    candidate.once('end', (reason) => fail('minecraft_connect_failed', `ended before spawn: ${reason}`))
  })
}

function configureSafeMovements(candidate) {
  if (fakeMode || !candidate?.pathfinder) return
  const movements = new Movements(candidate)
  movements.canDig = false
  movements.allow1by1towers = false
  movements.scafoldingBlocks = []
  if (scopeAnchor && Array.isArray(movements.exclusionAreasStep)) {
    movements.exclusionAreasStep.push((block) => positionInScope(block?.position) ? 0 : Infinity)
  }
  candidate.pathfinder.setMovements(movements)
}

function normalizedDimension(value) {
  return String(value || '').replace(/^minecraft:/, '')
}

function currentPosition() {
  return fakeMode ? fakeState.position : bot?.entity?.position
}

function currentDimension() {
  return normalizedDimension(fakeMode ? fakeState.dimension : bot?.game?.dimension)
}

function positionInScope(position) {
  if (!position || !scopeAnchor || !sessionConfig?.scope) return false
  const radius = Number(sessionConfig.scope.max_radius || 0)
  const dx = Number(position.x || 0) - Number(scopeAnchor.x || 0)
  const dy = Number(position.y || 0) - Number(scopeAnchor.y || 0)
  const dz = Number(position.z || 0) - Number(scopeAnchor.z || 0)
  return radius > 0 && (dx * dx + dy * dy + dz * dz) <= radius * radius
}

// Combat awareness: type-and-count only, never coordinates. The fake world
// toggles on a timer so Core-side listeners can be tested deterministically;
// a real world reports being attacked and only clears when no hostile stays
// within melee range.
let combatActive = false

function isHostileMob(entity) {
  if (!entity || entity.type !== 'mob') return false
  if (entity.kind === 'Hostile mobs') return true
  const category = String(entity.mobType || '').toLowerCase()
  return category === 'hostile'
}

function nearbyHostiles() {
  if (fakeMode) return combatActive ? [{ name: 'zombie', count: 2 }] : []
  const position = currentPosition()
  if (!bot || !position || !sessionConfig?.scope) return []
  const radius = Number(sessionConfig.scope.max_radius || 0)
  const counts = new Map()
  for (const entity of Object.values(bot.entities)) {
    if (!isHostileMob(entity) || !entity.position) continue
    if (entity.position.distanceTo(position) > radius) continue
    const name = String(entity.mobType || entity.name || 'hostile').toLowerCase()
    counts.set(name, (counts.get(name) || 0) + 1)
  }
  return Array.from(counts.entries()).map(([name, count]) => ({ name, count }))
}

function watchCombat(candidate) {
  if (fakeMode || !candidate) return
  candidate.on('entityHurt', (entity) => {
    if (entity !== candidate.entity || combatActive || closing) return
    combatActive = true
    emit('combat.started', { state: 'active' }, null, { sessionId: currentSessionId })
  })
  candidate.on('physicTick', () => {
    if (!combatActive || closing) return
    const position = currentPosition()
    const threatened = position && Object.values(candidate.entities).some(
      (entity) => isHostileMob(entity) && entity.position && entity.position.distanceTo(position) <= 8,
    )
    if (!threatened) {
      combatActive = false
      emit('combat.ended', { state: 'clear' }, null, { sessionId: currentSessionId })
    }
  })
}

function assertCurrentScope() {
  if (!Array.isArray(sessionConfig?.scope?.dimensions) || !sessionConfig.scope.dimensions.includes(currentDimension())) {
    throw new Error('dimension_out_of_scope')
  }
  if (!positionInScope(currentPosition())) throw new Error('spatial_scope_exceeded')
}

function assertIntentDimension(intent) {
  if (intent.dimension && normalizedDimension(intent.dimension) !== currentDimension()) throw new Error('dimension_out_of_scope')
}

function assertPositionScope(position) {
  if (!positionInScope(position)) throw new Error('spatial_scope_exceeded')
}

function privateCheckpoint() {
  if (fakeMode) {
    return {
      position: { ...fakeState.position },
      dimension: fakeState.dimension,
      health: fakeState.health,
      food: fakeState.food,
      inventory: Array.from(fakeState.inventory.entries()).map(([name, count]) => ({ name, count })),
      blocks_changed: fakeState.blocksChanged,
    }
  }
  const position = bot?.entity?.position
  return {
    position: position ? { x: position.x, y: position.y, z: position.z } : null,
    dimension: bot?.game?.dimension || '',
    health: Number(bot?.health || 0),
    food: Number(bot?.food || 0),
    inventory: bot ? bot.inventory.items().slice(0, 36).map((item) => ({ name: item.name, count: item.count })) : [],
  }
}

function safeWorld() {
  // Time, weather and entity census: type-and-count only, never coordinates.
  if (fakeMode) {
    return {
      time_of_day: 6000,
      raining: false,
      entities: combatActive ? [{ type: 'mob', kind: 'hostile', name: 'zombie', count: 2 }] : [],
    }
  }
  const world = {
    time_of_day: Number(bot?.time?.timeOfDay || 0),
    raining: Boolean(bot?.isRaining),
    entities: [],
  }
  const position = currentPosition()
  if (bot && position && sessionConfig?.scope) {
    const radius = Number(sessionConfig.scope.max_radius || 0)
    const counts = new Map()
    for (const entity of Object.values(bot.entities)) {
      if (!entity.position || entity.position.distanceTo(position) > radius) continue
      const key = entity.type === 'player' ? 'player' : `${entity.type}:${entity.kind || ''}:${entity.name || ''}`
      const row = counts.get(key) || {
        type: String(entity.type || 'unknown'),
        kind: String(entity.kind || entity.mobType || ''),
        name: String(entity.name || ''),
        count: 0,
      }
      row.count += 1
      counts.set(key, row)
    }
    world.entities = Array.from(counts.values()).slice(0, 16)
  }
  return world
}

function safeObservation(checkpoint) {
  const observation = {
    dimension: String(checkpoint.dimension || ''),
    health: Number(checkpoint.health || 0),
    food: Number(checkpoint.food || 0),
    inventory_slots: Array.isArray(checkpoint.inventory) ? checkpoint.inventory.length : 0,
    inventory_total: Array.isArray(checkpoint.inventory) ? checkpoint.inventory.reduce((total, row) => total + Number(row.count || 0), 0) : 0,
    world: safeWorld(),
  }
  const hostiles = nearbyHostiles()
  if (hostiles.length) observation.nearby_hostiles = hostiles
  return observation
}

function inventoryCount(rows, name) {
  return Array.isArray(rows) ? rows.filter((row) => row.name === name).reduce((total, row) => total + Number(row.count || 0), 0) : 0
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function waitControlled(goal, delay = 25) {
  if (goal.cancelled) throw new Error('goal_cancelled')
  while (goal.paused) {
    if (goal.cancelled) throw new Error('goal_cancelled')
    await sleep(25)
  }
  if (delay > 0) await sleep(delay)
  if (goal.cancelled) throw new Error('goal_cancelled')
}

async function runAtomic(goal, operation) {
  if (goal.cancelled) throw new Error('goal_cancelled')
  const pending = Promise.resolve().then(operation)
  goal.inFlight = pending
  try {
    return await pending
  } finally {
    if (goal.inFlight === pending) goal.inFlight = null
  }
}

async function waitForInFlight(goal) {
  if (!goal?.inFlight) return
  try {
    await goal.inFlight
  } catch (_) {
    // The action runner owns the typed failure. Control only waits until no
    // mutation remains in flight before acknowledging the state change.
  }
}

async function waitForInventoryIncrease(name, beforeCount, goal, timeoutMs = 3000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (inventoryCount(privateCheckpoint().inventory, name) > beforeCount) return true
    await waitControlled(goal, 100)
  }
  return inventoryCount(privateCheckpoint().inventory, name) > beforeCount
}

function findPlayer(name) {
  if (!bot) return null
  return bot.players?.[name]?.entity || bot.nearestEntity((entity) => entity.type === 'player' && entity.username === name)
}

async function safeGoto(goal) {
  while (true) {
    await waitControlled(activeGoal)
    try {
      await bot.pathfinder.goto(goal)
      await waitControlled(activeGoal, 0)
      return
    } catch (error) {
      if (activeGoal?.cancelled) throw new Error('goal_cancelled')
      if (activeGoal?.paused) {
        await waitControlled(activeGoal, 0)
        continue
      }
      throw error
    }
  }
}

async function executeFake(intent, goal) {
  assertCurrentScope()
  assertIntentDimension(intent)
  const delay = Math.max(1, Math.min(Number(process.env.JOI_MINECRAFT_FAKE_DELAY_MS || 10), 2000))
  let changes = 0
  let effects = 0
  if (intent.action === 'follow_player' || intent.action === 'come_to_player') {
    await waitControlled(goal, delay)
    fakeState.position.x += 1
    assertCurrentScope()
    effects += 1
  } else if (intent.action === 'collect' || intent.action === 'mine') {
    for (let index = 0; index < intent.count; index += 1) {
      await waitControlled(goal, delay)
      fakeState.inventory.set(intent.block, (fakeState.inventory.get(intent.block) || 0) + 1)
      changes += 1
      goal.changes = changes
      effects += 1
      goal.effects = effects
      fakeState.blocksChanged += 1
      await maybeDisconnectAfterEffect(goal, changes)
    }
  } else if (intent.action === 'craft') {
    await waitControlled(goal, delay)
    fakeState.inventory.set(intent.item, (fakeState.inventory.get(intent.item) || 0) + intent.count)
    effects += intent.count
    goal.effects = effects
  } else if (intent.action === 'eat') {
    await waitControlled(goal, delay)
    fakeState.food = 20
    effects += 1
    goal.effects = effects
  } else if (intent.action === 'place_blueprint') {
    for (const row of intent.blocks) {
      await waitControlled(goal, delay)
      fakeState.inventory.set(row.block, Math.max(0, (fakeState.inventory.get(row.block) || 1) - 1))
      changes += 1
      goal.changes = changes
      effects += 1
      goal.effects = effects
      fakeState.blocksChanged += 1
      await maybeDisconnectAfterEffect(goal, changes)
    }
  } else if (intent.action === 'deposit') {
    await waitControlled(goal, delay)
    for (const row of intent.items) {
      const available = fakeState.inventory.get(row.item) || 0
      if (available < row.count) throw new Error('deposit_item_count_insufficient')
      fakeState.inventory.set(row.item, available - row.count)
      effects += row.count
      goal.effects = effects
    }
  } else if (intent.action === 'attack') {
    for (let index = 0; index < intent.count; index += 1) {
      await waitControlled(goal, delay)
      effects += 1
      goal.effects = effects
    }
  } else if (intent.action === 'flee') {
    await waitControlled(goal, delay)
    fakeState.position.x += 1
    assertCurrentScope()
    effects += 1
    goal.effects = effects
  } else if (intent.action === 'guard') {
    await waitControlled(goal, delay)
    effects += 1
    goal.effects = effects
  } else {
    await waitControlled(goal, delay)
  }
  return { changes, effects, summary: `${intent.action}_completed`, verified: true }
}

async function maybeDisconnectAfterEffect(goal, changes) {
  if (process.env.JOI_MINECRAFT_FAKE_DISCONNECT_AFTER_EFFECT !== '1' || changes < 1) return
  const checkpoint = privateCheckpoint()
  const payload = { error: 'bridge_disconnected_after_uncertain_effect', status: 'partial', changes, effects: goal.effects || changes, verified: false, recovery_required: true, before: goal.before || safeObservation(checkpoint), after: safeObservation(checkpoint), checkpoint }
  emit('recovery.required', payload, goal.request, { goalId: goal.id })
  cachedResponses.set(goal.request.message_id, { type: 'recovery.required', payload, goalId: goal.id })
  await sleep(5)
  process.exit(23)
}

async function executeReal(intent, goal) {
  assertCurrentScope()
  assertIntentDimension(intent)
  let changes = 0
  let effects = 0
  const beforeInventory = privateCheckpoint().inventory
  const beforeFood = Number(privateCheckpoint().food || 0)
  if (intent.action === 'observe' || intent.action === 'inventory') return { changes, effects, summary: `${intent.action}_completed`, verified: true }
  if (intent.action === 'follow_player') {
    const player = findPlayer(intent.player)
    if (!player) throw new Error('player_not_found')
    bot.pathfinder.setGoal(new goals.GoalFollow(player, intent.distance), true)
    const deadline = Date.now() + intent.duration_seconds * 1000
    while (Date.now() < deadline) {
      await waitControlled(goal, 100)
      assertCurrentScope()
    }
    bot.pathfinder.stop()
    const playerAfter = findPlayer(intent.player)
    const verified = Boolean(playerAfter && bot.entity.position.distanceTo(playerAfter.position) <= intent.distance + 2)
    return { changes, effects: 1, summary: 'follow_player_completed', verified }
  }
  if (intent.action === 'come_to_player') {
    const player = findPlayer(intent.player)
    if (!player) throw new Error('player_not_found')
    assertPositionScope(player.position)
    await safeGoto(new goals.GoalNear(Math.floor(player.position.x), Math.floor(player.position.y), Math.floor(player.position.z), intent.distance))
    return { changes, effects: 1, summary: 'come_to_player_completed', verified: bot.entity.position.distanceTo(player.position) <= intent.distance + 2 }
  }
  if (intent.action === 'collect' || intent.action === 'mine') {
    const blockType = bot.registry.blocksByName[intent.block]
    if (!blockType) throw new Error('unknown_block')
    for (let index = 0; index < intent.count; index += 1) {
      await waitControlled(goal, 0)
      const block = bot.findBlock({ matching: blockType.id, maxDistance: intent.radius })
      if (!block) throw new Error('block_not_found')
      assertPositionScope(block.position)
      await safeGoto(new goals.GoalNear(block.position.x, block.position.y, block.position.z, 1))
      await waitControlled(goal, 0)
      if (!bot.canDigBlock(block)) throw new Error('cannot_dig_block')
      const beforeCount = inventoryCount(privateCheckpoint().inventory, intent.block)
      await runAtomic(goal, async () => {
        await bot.dig(block)
        const afterBlock = bot.blockAt(block.position)
        const inventoryDelta = inventoryCount(privateCheckpoint().inventory, intent.block) - beforeCount
        if (afterBlock?.name === intent.block && inventoryDelta <= 0) throw new Error('verification_failed')
        changes += 1
        effects += 1
        goal.changes = changes
        goal.effects = effects
      })
      if (intent.action === 'collect' && !await waitForInventoryIncrease(intent.block, beforeCount, goal)) {
        throw new Error('collect_item_not_acquired')
      }
      await waitControlled(goal, 0)
    }
    return { changes, effects, summary: `${intent.action}_completed`, verified: changes === intent.count }
  }
  if (intent.action === 'craft') {
    const item = bot.registry.itemsByName[intent.item]
    if (!item) throw new Error('unknown_item')
    const tableType = bot.registry.blocksByName.crafting_table
    const table = tableType ? bot.findBlock({ matching: tableType.id, maxDistance: 16 }) : null
    if (table) assertPositionScope(table.position)
    const recipe = bot.recipesFor(item.id, null, intent.count, table)[0]
    if (!recipe) throw new Error('recipe_not_found')
    const recipeOutput = Math.max(1, Number(recipe.result?.count || 1))
    const recipeExecutions = Math.max(1, Math.ceil(intent.count / recipeOutput))
    await waitControlled(goal, 0)
    let crafted = 0
    await runAtomic(goal, async () => {
      await bot.craft(recipe, recipeExecutions, table)
      crafted = inventoryCount(privateCheckpoint().inventory, intent.item) - inventoryCount(beforeInventory, intent.item)
      effects = Math.max(0, crafted)
      goal.effects = effects
    })
    await waitControlled(goal, 0)
    return { changes, effects, summary: 'craft_completed', verified: crafted >= intent.count }
  }
  if (intent.action === 'eat') {
    const food = bot.inventory.items().find((item) => !intent.item || item.name === intent.item)
    if (!food) throw new Error('food_not_found')
    await bot.equip(food, 'hand')
    await waitControlled(goal, 0)
    let foodChanged = false
    await runAtomic(goal, async () => {
      await bot.consume()
      foodChanged = Number(bot.food || 0) > beforeFood
      effects = 1
      goal.effects = effects
    })
    await waitControlled(goal, 0)
    return { changes, effects, summary: 'eat_completed', verified: foodChanged || Number(bot.food || 0) >= 20 }
  }
  if (intent.action === 'place_blueprint') {
    const anchorEntity = intent.anchor === 'player' ? findPlayer(intent.player) : bot.entity
    if (!anchorEntity) throw new Error('blueprint_anchor_not_found')
    const anchor = anchorEntity.position.floored()
    assertPositionScope(anchor)
    for (const row of intent.blocks) {
      await waitControlled(goal, 0)
      const target = anchor.offset(row.offset[0], row.offset[1], row.offset[2])
      assertPositionScope(target)
      const existing = bot.blockAt(target)
      if (existing && existing.name === row.block) continue
      const item = bot.inventory.items().find((candidate) => candidate.name === row.block)
      if (!item) throw new Error('missing_build_item')
      await safeGoto(new goals.GoalNear(target.x, target.y, target.z, 3))
      const neighbours = [[0, -1, 0], [0, 1, 0], [-1, 0, 0], [1, 0, 0], [0, 0, -1], [0, 0, 1]]
      let reference = null
      let face = null
      for (const offset of neighbours) {
        const candidate = bot.blockAt(target.offset(offset[0], offset[1], offset[2]))
        if (candidate && candidate.boundingBox === 'block') {
          reference = candidate
          face = target.minus(candidate.position)
          break
        }
      }
      if (!reference || !face) throw new Error('missing_reference_block')
      await bot.equip(item, 'hand')
      await waitControlled(goal, 0)
      await runAtomic(goal, async () => {
        await bot.placeBlock(reference, face)
        changes += 1
        effects += 1
        goal.changes = changes
        goal.effects = effects
      })
      await waitControlled(goal, 0)
    }
    const verified = intent.blocks.every((row) => bot.blockAt(anchor.offset(row.offset[0], row.offset[1], row.offset[2]))?.name === row.block)
    return { changes, effects, summary: 'place_blueprint_completed', verified }
  }
  if (intent.action === 'deposit') {
    const blockTypes = [bot.registry.blocksByName[intent.container]?.id].filter(Boolean)
    const containerBlock = bot.findBlock({ matching: blockTypes, maxDistance: intent.radius })
    if (!containerBlock) throw new Error('container_not_found')
    assertPositionScope(containerBlock.position)
    await safeGoto(new goals.GoalNear(containerBlock.position.x, containerBlock.position.y, containerBlock.position.z, 2))
    const container = await bot.openContainer(containerBlock)
    try {
      for (const row of intent.items) {
        await waitControlled(goal, 0)
        const item = bot.inventory.items().find((candidate) => candidate.name === row.item)
        if (!item) throw new Error('deposit_item_not_found')
        if (item.count < row.count) throw new Error('deposit_item_count_insufficient')
        await runAtomic(goal, async () => {
          await container.deposit(item.type, null, row.count)
          effects += row.count
          goal.effects = effects
        })
        await waitControlled(goal, 0)
      }
    } finally {
      container.close()
    }
    const deposited = intent.items.every((row) => inventoryCount(beforeInventory, row.item) - inventoryCount(privateCheckpoint().inventory, row.item) >= row.count)
    return { changes, effects, summary: 'deposit_completed', verified: deposited }
  }
  if (intent.action === 'attack') {
    // PvP is structurally impossible here: the filter only ever admits hostile
    // mobs, so a player can never be resolved as the target (contract side
    // offers no target field either - the two layers enforce together).
    let attacked = 0
    for (let index = 0; index < intent.count; index += 1) {
      await waitControlled(goal, 0)
      const target = bot.nearestEntity(
        (entity) => isHostileMob(entity) && entity.position && bot.entity.position.distanceTo(entity.position) <= intent.radius,
      )
      if (!target) throw new Error('hostile_not_found')
      assertPositionScope(target.position)
      await safeGoto(new goals.GoalNear(target.position.x, target.position.y, target.position.z, 3))
      await waitControlled(goal, 0)
      await runAtomic(goal, async () => {
        await bot.attack(target)
        attacked += 1
        effects += 1
        goal.effects = effects
      })
      await waitControlled(goal, 0)
    }
    return { changes, effects, summary: 'attack_completed', verified: attacked >= intent.count }
  }
  if (intent.action === 'flee') {
    const hostile = bot.nearestEntity(
      (entity) => isHostileMob(entity) && entity.position && bot.entity.position.distanceTo(entity.position) <= 32,
    )
    if (!hostile) return { changes, effects: 0, summary: 'flee_completed', verified: true }
    bot.pathfinder.setGoal(new goals.GoalInvert(new goals.GoalFollow(hostile, intent.distance)), true)
    const deadline = Date.now() + intent.duration_seconds * 1000
    while (Date.now() < deadline) {
      await waitControlled(goal, 100)
      assertCurrentScope()
    }
    bot.pathfinder.stop()
    const after = bot.nearestEntity(
      (entity) => isHostileMob(entity) && entity.position && bot.entity.position.distanceTo(entity.position) <= 8,
    )
    effects = 1
    goal.effects = effects
    return { changes, effects, summary: 'flee_completed', verified: !after || after.position.distanceTo(bot.entity.position) >= intent.distance - 2 }
  }
  if (intent.action === 'guard') {
    if (bot?.pathfinder) bot.pathfinder.stop()
    if (bot && typeof bot.stopDigging === 'function') bot.stopDigging()
    const threatened = nearbyHostiles().length > 0
    return { changes, effects: threatened ? 1 : 0, summary: 'guard_completed', verified: true }
  }
  throw new Error('unknown_game_action')
}

function safeConnectError(error) {
  const value = String(error?.message || error || 'minecraft_connect_failed')
  const allow = new Set([
    'minecraft_connect_failed', 'minecraft_connect_refused', 'minecraft_host_unreachable',
    'minecraft_login_rejected', 'minecraft_version_unsupported', 'spawn_timeout',
  ])
  return allow.has(value) ? value : 'minecraft_connect_failed'
}

function safeError(error) {
  const value = String(error?.message || error || 'minecraft_goal_failed')
  const allow = new Set([
    'goal_cancelled', 'goal_timeout', 'player_not_found', 'unknown_block', 'block_not_found',
    'cannot_dig_block', 'unknown_item', 'recipe_not_found', 'food_not_found',
    'blueprint_anchor_not_found', 'missing_build_item', 'missing_reference_block',
    'collect_item_not_acquired', 'container_not_found', 'deposit_item_not_found', 'deposit_item_count_insufficient', 'minecraft_connect_failed', 'spawn_timeout',
    'dimension_out_of_scope', 'spatial_scope_exceeded', 'hostile_not_found',
  ])
  return allow.has(value) ? value : 'minecraft_goal_failed'
}

async function runGoal(request, goal) {
  const beforeCheckpoint = privateCheckpoint()
  goal.before = safeObservation(beforeCheckpoint)
  const timeoutMs = Math.max(1000, Math.min(Number(process.env.JOI_MINECRAFT_ACTION_TIMEOUT_MS || 120000), 900000))
  let timer = null
  try {
    const execution = fakeMode ? executeFake(goal.intent, goal) : executeReal(goal.intent, goal)
    const result = await Promise.race([
      execution.then((value) => ({ kind: 'result', value }), (error) => ({ kind: 'error', error })),
      new Promise((resolve) => { timer = setTimeout(() => resolve({ kind: 'timeout' }), timeoutMs) }),
    ])
    if (result.kind === 'timeout') {
      goal.cancelled = true
      recoveryRequired = true
      if (bot?.pathfinder) bot.pathfinder.stop()
      if (bot && typeof bot.stopDigging === 'function') bot.stopDigging()
      if (bot && !fakeMode && typeof bot.quit === 'function') bot.quit('joi_goal_timeout')
      const payload = {
        error: 'goal_timeout',
        verified: false,
        status: goal.effects > 0 ? 'partial' : 'unverified',
        changes: goal.changes || 0,
        effects: goal.effects || 0,
        recovery_required: true,
        before: goal.before,
        after: safeObservation(privateCheckpoint()),
        checkpoint: privateCheckpoint(),
      }
      cacheAndEmit(request, 'recovery.required', payload, { goalId: goal.id })
      setTimeout(() => process.exit(24), 10)
      return
    }
    if (result.kind === 'error') throw result.error
    if (goal.cancelled) return
    const actionResult = result.value
    const afterCheckpoint = privateCheckpoint()
    const payload = {
      verified: actionResult.verified === true,
      status: actionResult.verified === true ? 'completed' : 'unverified',
      summary: actionResult.summary,
      changes: actionResult.changes,
      effects: actionResult.effects || 0,
      before: safeObservation(beforeCheckpoint),
      after: safeObservation(afterCheckpoint),
      checkpoint: afterCheckpoint,
    }
    if (actionResult.verified !== true) payload.error = 'verification_failed'
    cacheAndEmit(request, actionResult.verified === true ? 'goal.completed' : 'goal.failed', payload, { goalId: goal.id })
  } catch (error) {
    if (goal.cancelled || String(error?.message || error) === 'goal_cancelled') return
    if (bot?.pathfinder) bot.pathfinder.stop()
    const payload = {
      error: safeError(error),
      verified: false,
      status: (goal.effects || 0) > 0 ? 'partial' : 'failed',
      changes: goal.changes || 0,
      effects: goal.effects || 0,
      before: goal.before,
      after: safeObservation(privateCheckpoint()),
      checkpoint: privateCheckpoint(),
    }
    cacheAndEmit(request, 'goal.failed', payload, { goalId: goal.id })
  } finally {
    if (timer) clearTimeout(timer)
    if (activeGoal === goal) activeGoal = null
  }
}

async function handleFresh(request) {
  if (request.type === 'session.start') {
    if (!['companion', 'delegate'].includes(request.payload.mode)) {
      cacheAndEmit(request, 'error', { error: 'invalid_game_mode' })
      return
    }
    currentSessionId = request.session_id
    sessionConfig = { mode: request.payload.mode, scope: request.payload.scope, budget: request.payload.budget }
    const expectedServer = String(process.env.JOI_MINECRAFT_SERVER_ID || '').toLowerCase()
    const expectedWorld = String(process.env.JOI_MINECRAFT_WORLD || '').toLowerCase()
    if (!expectedServer || !expectedWorld || request.payload.scope?.server_id !== expectedServer || request.payload.scope?.world !== expectedWorld) {
      currentSessionId = ''
      sessionConfig = null
      cacheAndEmit(request, 'error', { error: 'scope_identity_mismatch' }, { sessionId: request.session_id })
      return
    }
    try {
      bot = await createConnectedBot()
      const position = currentPosition()
      scopeAnchor = position ? { x: Number(position.x || 0), y: Number(position.y || 0), z: Number(position.z || 0) } : null
      configureSafeMovements(bot)
      if (!fakeMode) {
        bot.on('end', () => {
          if (closing) return
          recoveryRequired = true
          const goal = activeGoal
          if (goal) {
            const checkpoint = privateCheckpoint()
            const payload = { error: 'minecraft_disconnected', verified: false, status: (goal.effects || 0) > 0 ? 'partial' : 'unverified', changes: goal.changes || 0, effects: goal.effects || 0, recovery_required: true, before: goal.before || safeObservation(checkpoint), after: safeObservation(checkpoint), checkpoint }
            cachedResponses.set(goal.request.message_id, { type: 'recovery.required', payload, goalId: goal.id })
            emit('recovery.required', payload, goal.request, { goalId: goal.id })
            goal.cancelled = true
          }
        })
      }
      cacheAndEmit(request, 'session.ready', { capabilities, state: 'ready' })
      // Fake-world diagnostic push: periodically emit an unsolicited, sanitized
      // state.snapshot so Core-side listeners (and their tests) can exercise
      // the push channel without a real server.
      const fakePushSnapshotMs = Math.max(0, Number(process.env.JOI_MINECRAFT_FAKE_PUSH_SNAPSHOT_MS || 0))
      if (fakeMode && fakePushSnapshotMs > 0) {
        const pushSnapshot = () => {
          if (closing || recoveryRequired) return
          const checkpoint = privateCheckpoint()
          emit('state.snapshot', { observation: safeObservation(checkpoint), checkpoint }, null, { sessionId: currentSessionId })
          setTimeout(pushSnapshot, fakePushSnapshotMs)
        }
        setTimeout(pushSnapshot, fakePushSnapshotMs)
      }
      // Fake-world combat toggle: alternates started/ended so combat events and
      // nearby_hostiles observations are deterministic for Core-side tests.
      const fakeCombatMs = Math.max(0, Number(process.env.JOI_MINECRAFT_FAKE_COMBAT_MS || 0))
      if (fakeMode && fakeCombatMs > 0) {
        const toggleCombat = () => {
          if (closing || recoveryRequired) return
          combatActive = !combatActive
          emit(combatActive ? 'combat.started' : 'combat.ended', { state: combatActive ? 'active' : 'clear' }, null, { sessionId: currentSessionId })
          setTimeout(toggleCombat, fakeCombatMs)
        }
        setTimeout(toggleCombat, fakeCombatMs)
      }
      // Fake-world chat lines: emit each entry once so the chat.observed
      // channel is deterministic for Core-side tests.
      if (fakeMode) {
        try {
          const lines = JSON.parse(process.env.JOI_MINECRAFT_FAKE_CHAT_LINES || '[]')
          if (Array.isArray(lines)) {
            lines.slice(0, 8).forEach((row, index) => {
              if (!row || typeof row !== 'object') return
              setTimeout(() => {
                if (closing || recoveryRequired) return
                emit('chat.observed', { player: String(row.player || '').slice(0, 32), text: String(row.text || '').slice(0, 500) }, null, { sessionId: currentSessionId })
              }, 50 + index * 50)
            })
          }
        } catch (_) {
          // Invalid fake-chat JSON is a test setup mistake; ignore it.
        }
      }
      const fakeIdleDisconnectMs = Number(process.env.JOI_MINECRAFT_FAKE_IDLE_DISCONNECT_MS || 0)
      if (fakeMode && fakeIdleDisconnectMs > 0) {
        setTimeout(() => { if (!closing && !activeGoal) recoveryRequired = true }, fakeIdleDisconnectMs)
      }
    } catch (error) {
      currentSessionId = ''
      sessionConfig = null
      cacheAndEmit(request, 'error', { error: safeConnectError(error) }, { sessionId: request.session_id })
    }
    return
  }
  if (request.type === 'goal.submit') {
    if (recoveryRequired) {
      const checkpoint = privateCheckpoint()
      cacheAndEmit(request, 'recovery.required', { error: 'recovery_required', status: 'unverified', verified: false, changes: 0, effects: 0, recovery_required: true, before: safeObservation(checkpoint), after: safeObservation(checkpoint), checkpoint }, { goalId: request.goal_id })
      return
    }
    if (activeGoal) {
      cacheAndEmit(request, 'error', { error: 'goal_already_running' }, { goalId: request.goal_id })
      return
    }
    const error = validateIntent(request.payload.intent)
    if (error) {
      cacheAndEmit(request, 'error', { error }, { goalId: request.goal_id })
      return
    }
    const goal = { id: request.goal_id, intent: request.payload.intent, request, paused: false, cancelled: false, changes: 0, effects: 0, inFlight: null }
    activeGoal = goal
    cacheAndEmit(request, 'goal.accepted', { state: 'acting' }, { goalId: goal.id })
    void runGoal(request, goal)
    return
  }
  if (request.type === 'goal.pause') {
    if (!activeGoal || activeGoal.id !== request.goal_id) {
      cacheAndEmit(request, 'error', { error: 'goal_not_active' }, { goalId: request.goal_id })
      return
    }
    if (process.env.JOI_MINECRAFT_FAKE_IGNORE_CONTROL === '1') return
    activeGoal.paused = true
    if (bot?.pathfinder) bot.pathfinder.stop()
    if (bot && typeof bot.stopDigging === 'function') bot.stopDigging()
    await waitForInFlight(activeGoal)
    cacheAndEmit(request, 'goal.paused', { state: 'paused' }, { goalId: request.goal_id })
    return
  }
  if (request.type === 'goal.resume') {
    if (!activeGoal || activeGoal.id !== request.goal_id || !activeGoal.paused) {
      cacheAndEmit(request, 'error', { error: 'goal_not_paused' }, { goalId: request.goal_id })
      return
    }
    if (process.env.JOI_MINECRAFT_FAKE_IGNORE_CONTROL === '1') return
    activeGoal.paused = false
    if (activeGoal.intent.action === 'follow_player' && bot?.pathfinder) {
      const player = findPlayer(activeGoal.intent.player)
      if (player) bot.pathfinder.setGoal(new goals.GoalFollow(player, activeGoal.intent.distance), true)
    }
    cacheAndEmit(request, 'goal.resumed', { state: 'acting' }, { goalId: request.goal_id })
    return
  }
  if (request.type === 'goal.cancel') {
    if (!activeGoal || activeGoal.id !== request.goal_id) {
      cacheAndEmit(request, 'error', { error: 'goal_not_active' }, { goalId: request.goal_id })
      return
    }
    if (process.env.JOI_MINECRAFT_FAKE_IGNORE_CONTROL === '1') return
    const cancelledGoal = activeGoal
    cancelledGoal.cancelled = true
    if (bot?.pathfinder) bot.pathfinder.stop()
    if (bot && typeof bot.stopDigging === 'function') bot.stopDigging()
    await waitForInFlight(cancelledGoal)
    await Promise.resolve()
    const checkpoint = privateCheckpoint()
    const payload = { state: 'cancelled', status: (cancelledGoal.effects || 0) > 0 ? 'partial' : 'failed', verified: false, changes: cancelledGoal.changes || 0, effects: cancelledGoal.effects || 0, before: cancelledGoal.before || safeObservation(checkpoint), after: safeObservation(checkpoint), checkpoint }
    cacheAndEmit(request, 'goal.cancelled', payload, { goalId: request.goal_id })
    cachedResponses.set(cancelledGoal.request.message_id, { type: 'goal.cancelled', payload, goalId: request.goal_id })
    emit('goal.cancelled', payload, cancelledGoal.request, { goalId: request.goal_id })
    activeGoal = null
    return
  }
  if (request.type === 'state.snapshot.request') {
    cacheAndEmit(request, 'state.snapshot', { observation: safeObservation(privateCheckpoint()), checkpoint: privateCheckpoint() })
    return
  }
  if (request.type === 'session.stop') {
    if (process.env.JOI_MINECRAFT_FAKE_IGNORE_CONTROL === '1') return
    closing = true
    if (activeGoal) activeGoal.cancelled = true
    if (bot?.pathfinder) bot.pathfinder.stop()
    if (bot && !fakeMode && typeof bot.quit === 'function') bot.quit('joi_session_stop')
    cacheAndEmit(request, 'session.stopped', { state: 'stopped' })
    return
  }
}

async function handleLine(line) {
  if (locked || line.length > 256 * 1024) return
  let request
  try {
    request = JSON.parse(line)
  } catch (_) {
    locked = true
    emit('error', { error: 'invalid_json' }, null, { sessionId: currentSessionId })
    return
  }
  const messageDigest = digest(request)
  const previous = seenMessages.get(request.message_id)
  if (previous) {
    if (previous !== messageDigest) {
      locked = true
      emit('error', { error: 'message_id_conflict' }, request, { goalId: request.goal_id || '' })
      return
    }
    replay(request)
    return
  }
  const envelopeError = validateEnvelope(request)
  if (envelopeError) {
    emit('error', { error: envelopeError }, request, { sessionId: request.session_id || currentSessionId, goalId: request.goal_id || '' })
    return
  }
  if (request.sequence !== expectedInputSequence + 1) {
    locked = true
    emit('error', { error: 'bridge_sequence_violation' }, request, { goalId: request.goal_id || '' })
    return
  }
  expectedInputSequence = request.sequence
  seenMessages.set(request.message_id, messageDigest)
  capCache(seenMessages)
  await handleFresh(request)
}

emit('bridge.ready', { capabilities, fake: fakeMode }, null, { sessionId: '' })

const reader = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })
reader.on('line', (line) => { void handleLine(line) })
reader.on('close', () => {
  closing = true
  if (activeGoal) activeGoal.cancelled = true
  if (bot?.pathfinder) bot.pathfinder.stop()
  if (bot && !fakeMode && typeof bot.quit === 'function') bot.quit('joi_parent_closed')
  process.exit(0)
})
