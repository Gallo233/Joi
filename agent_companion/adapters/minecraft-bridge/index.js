#!/usr/bin/env node
'use strict'

const fs = require('fs')
const readline = require('readline')
const mineflayer = require('mineflayer')
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder')

const protocol = 'joi.game_adapter.v1'
const controlPath = process.env.JOI_MINECRAFT_CONTROL_FILE || ''

function emit(payload) {
  process.stdout.write(`${JSON.stringify({ protocol, ...payload })}\n`)
}

function readControl() {
  if (!controlPath) return { action: 'resume' }
  try {
    return JSON.parse(fs.readFileSync(controlPath, 'utf8'))
  } catch (_) {
    return { action: 'resume' }
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function waitWhilePaused(bot) {
  let paused = false
  while (true) {
    const control = readControl()
    if (control.action === 'cancel' || control.action === 'user_input') throw new Error('user_takeover')
    if (control.action !== 'pause') return
    if (!paused) {
      bot.pathfinder.setGoal(null)
      paused = true
    }
    await sleep(250)
  }
}

function botOptions() {
  return {
    host: process.env.JOI_MINECRAFT_HOST || 'localhost',
    port: Number(process.env.JOI_MINECRAFT_PORT || 25565),
    username: process.env.JOI_MINECRAFT_USERNAME || 'Joi',
    auth: process.env.JOI_MINECRAFT_AUTH || 'offline',
    version: process.env.JOI_MINECRAFT_VERSION || false,
  }
}

function checkpoint(bot) {
  const position = bot.entity?.position
  return {
    position: position ? { x: position.x, y: position.y, z: position.z } : null,
    dimension: bot.game?.dimension || '',
    health: bot.health,
    food: bot.food,
    inventory: bot.inventory.items().slice(0, 36).map((item) => ({ name: item.name, count: item.count })),
  }
}

async function follow(bot) {
  const player = bot.nearestEntity((entity) => entity.type === 'player' && entity.username !== bot.username)
  if (!player) throw new Error('no_player_to_follow')
  bot.pathfinder.setGoal(new goals.GoalFollow(player, 2), true)
  const until = Date.now() + Number(process.env.JOI_MINECRAFT_FOLLOW_MS || 30000)
  while (Date.now() < until) {
    await waitWhilePaused(bot)
    await sleep(250)
  }
  bot.pathfinder.setGoal(null)
  return 'follow_completed'
}

async function explore(bot) {
  const start = bot.entity.position
  const radius = Math.max(4, Math.min(Number(process.env.JOI_MINECRAFT_EXPLORE_RADIUS || 16), 64))
  const target = new goals.GoalNear(Math.floor(start.x + radius), Math.floor(start.y), Math.floor(start.z + radius), 2)
  await bot.pathfinder.goto(target)
  await waitWhilePaused(bot)
  return 'explore_checkpoint_reached'
}

async function collect(bot) {
  const blockName = process.env.JOI_MINECRAFT_TARGET_BLOCK || 'oak_log'
  const blockType = bot.registry.blocksByName[blockName]
  if (!blockType) throw new Error(`unknown_block:${blockName}`)
  const block = bot.findBlock({ matching: blockType.id, maxDistance: 64 })
  if (!block) throw new Error(`block_not_found:${blockName}`)
  await bot.pathfinder.goto(new goals.GoalNear(block.position.x, block.position.y, block.position.z, 1))
  await waitWhilePaused(bot)
  if (!bot.canDigBlock(block)) throw new Error(`cannot_dig:${blockName}`)
  await bot.dig(block)
  return `collected:${blockName}`
}

async function build(bot) {
  const itemName = process.env.JOI_MINECRAFT_BUILD_ITEM || 'cobblestone'
  const item = bot.inventory.items().find((row) => row.name === itemName)
  if (!item) throw new Error(`missing_build_item:${itemName}`)
  const reference = bot.blockAt(bot.entity.position.offset(0, -1, 0))
  if (!reference) throw new Error('missing_reference_block')
  await waitWhilePaused(bot)
  await bot.equip(item, 'hand')
  await bot.placeBlock(reference, { x: 1, y: 0, z: 0 })
  return `placed:${itemName}`
}

async function performGoal(bot, request) {
  const goal = String(request.goal || '').toLowerCase()
  if (goal.includes('跟随') || goal.includes('follow')) return follow(bot)
  if (goal.includes('采集') || goal.includes('collect') || goal.includes('挖')) return collect(bot)
  if (goal.includes('建造') || goal.includes('build') || goal.includes('放置')) return build(bot)
  return explore(bot)
}

function createConnectedBot() {
  return new Promise((resolve, reject) => {
    const bot = mineflayer.createBot(botOptions())
    const timeout = setTimeout(() => {
      bot.quit('spawn_timeout')
      reject(new Error('spawn_timeout'))
    }, 30000)
    bot.once('spawn', () => {
      clearTimeout(timeout)
      bot.loadPlugin(pathfinder)
      bot.pathfinder.setMovements(new Movements(bot))
      resolve(bot)
    })
    bot.once('error', (error) => {
      clearTimeout(timeout)
      reject(error)
    })
  })
}

async function run(request) {
  let lastError = null
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    let bot = null
    try {
      bot = await createConnectedBot()
      const summary = await performGoal(bot, request)
      const state = checkpoint(bot)
      bot.quit('joi_goal_complete')
      return { ok: true, summary, checkpoint: state, reconnect_attempts: attempt - 1 }
    } catch (error) {
      lastError = error
      if (bot) bot.quit('joi_retry')
      if (String(error?.message || error) === 'user_takeover') throw error
      await sleep(attempt * 1000)
    }
  }
  throw lastError || new Error('minecraft_bridge_failed')
}

const reader = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })
reader.once('line', async (line) => {
  try {
    const request = JSON.parse(line)
    if (request.protocol !== protocol || request.action !== 'run' || request.mode !== 'companion') {
      emit({ ok: false, error: 'invalid_request' })
      process.exitCode = 2
      return
    }
    emit(await run(request))
  } catch (error) {
    emit({ ok: false, error: String(error?.message || error).slice(0, 300) })
    process.exitCode = 1
  } finally {
    reader.close()
  }
})
