import assert from 'node:assert/strict'
import test from 'node:test'

import { encodeWav } from '../src/voiceRecorder.ts'

/**
 * The recogniser reads WAV and MP3 and nothing else, so the header this writes
 * is the whole contract. `MediaRecorder` would have given webm or mp4
 * depending on the engine, and that only fails after the upload.
 */

const read = (bytes, offset, length) => String.fromCharCode(...bytes.slice(offset, offset + length))
const u32 = (bytes, offset) => new DataView(bytes.buffer).getUint32(offset, true)
const u16 = (bytes, offset) => new DataView(bytes.buffer).getUint16(offset, true)
const i16 = (bytes, offset) => new DataView(bytes.buffer).getInt16(offset, true)

test('the header says RIFF/WAVE, which is what the service matches on', () => {
  const wav = encodeWav(new Float32Array(8), 16000)
  assert.equal(read(wav, 0, 4), 'RIFF')
  assert.equal(read(wav, 8, 4), 'WAVE')
  assert.equal(read(wav, 12, 4), 'fmt ')
  assert.equal(read(wav, 36, 4), 'data')
})

test('the format block describes 16-bit mono PCM at the given rate', () => {
  const wav = encodeWav(new Float32Array(4), 16000)
  assert.equal(u16(wav, 20), 1, 'uncompressed PCM')
  assert.equal(u16(wav, 22), 1, 'mono')
  assert.equal(u32(wav, 24), 16000, 'sample rate')
  assert.equal(u16(wav, 34), 16, 'bits per sample')
  assert.equal(u32(wav, 28), 32000, 'byte rate is rate x frame size')
  assert.equal(u16(wav, 32), 2, 'frame size')
})

test('the declared sizes match the bytes actually written', () => {
  // A truncated or overlong declared length is the classic way a WAV plays as
  // silence or noise, and nothing about the file looks wrong until it does.
  const wav = encodeWav(new Float32Array(100), 16000)
  assert.equal(wav.length, 44 + 200)
  assert.equal(u32(wav, 40), 200, 'data chunk length')
  assert.equal(u32(wav, 4), 36 + 200, 'riff length excludes the first 8 bytes')
})

test('the rate written is the one recorded at, not the one asked for', () => {
  // Engines may refuse 16 kHz and hand back 48 kHz. Writing the requested rate
  // instead of the real one plays the speech back at the wrong pitch.
  assert.equal(u32(encodeWav(new Float32Array(2), 48000), 24), 48000)
})

test('full scale maps to the ends of the range without wrapping', () => {
  const wav = encodeWav(Float32Array.from([1, -1, 0]), 16000)
  assert.equal(i16(wav, 44), 32767)
  assert.equal(i16(wav, 46), -32768)
  assert.equal(i16(wav, 48), 0)
})

test('samples past full scale clamp instead of wrapping to the opposite sign', () => {
  // Without the clamp, 1.5 wraps to a large negative value -- an audible click
  // on exactly the loudest part of the recording.
  const wav = encodeWav(Float32Array.from([1.5, -1.5]), 16000)
  assert.equal(i16(wav, 44), 32767)
  assert.equal(i16(wav, 46), -32768)
})

test('an empty recording still produces a valid, empty file', () => {
  const wav = encodeWav(new Float32Array(0), 16000)
  assert.equal(wav.length, 44)
  assert.equal(u32(wav, 40), 0)
  assert.equal(read(wav, 0, 4), 'RIFF')
})
