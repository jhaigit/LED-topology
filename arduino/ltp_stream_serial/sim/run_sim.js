// Test the ltp_stream_serial AVR firmware on a simulated ATmega328P.
//
//   node run_sim.js ../build/uno/ltp_stream_serial.ino.hex
//
// Talks LTP serial protocol v2 to the firmware over its simulated UART and
// reads the LED pins directly. Checks:
//   - the protocol: HELLO, GET_INFO, controls, pixel read-back, settings;
//   - the pixels: every frame equals what the same pattern code produces when
//     compiled for the host (tests/test_firmware/fx_harness.cpp);
//   - the WS2812 waveform: pulse widths, and the longest low time in a frame;
//   - that host commands sent while frames are going out are not lost.
//
// Needs g++ and readelf on the host, and the .elf next to the .hex.

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const { Board, F_CPU } = require('./avr_board');

const ROOT = path.resolve(__dirname, '..', '..', '..');
const CYCLES_PER_US = F_CPU / 1e6;

// Pins, as config.h has them for the ATmega328P.
const PINS = { wsPort: 'B', wsBit: 0, apaPort: 'D', apaData: 3, apaClock: 2 };

// Limits the waveform is held to, in CPU cycles of 62.5 ns.
const WS_T0H = 5;             // 312 ns
const WS_T1H = 13;            // 812 ns
const WS_MAX_LOW = 80;        // 5 us: older WS2812 latch at 6-9 us
const WS_LATCH = 1600;        // 100 us of low separates two frames here
// The simulator enters an interrupt 2 cycles sooner than the chip does, so on
// hardware each bit leaves the pattern code 2 cycles less. Require every
// pattern to keep up with the bit period shortened by this much, which covers
// that difference with room to spare.
const WS_PERIOD_HEADROOM = 6;
const APA_FRAME_GAP = 8000;   // 0.5 ms without a clock edge separates APA102 frames

// ---------------------------------------------------------------------------
// Protocol
// ---------------------------------------------------------------------------

const CMD = {
  NOP: 0x00, RESET: 0x01, ACK: 0x02, NAK: 0x03, HELLO: 0x04, SHOW: 0x05,
  GET_INFO: 0x10, GET_PIXELS: 0x11, GET_CONTROL: 0x12,
  INFO_RESPONSE: 0x20, PIXEL_RESPONSE: 0x21, CONTROL_RESPONSE: 0x22,
  PIXEL_FRAME: 0x33, SET_CONTROL: 0x40, SAVE_CONFIG: 0x42, LOAD_CONFIG: 0x43,
  RESET_CONFIG: 0x44, SET_NAME: 0x46,
};
const INFO = { ALL: 0, VERSION: 1, STRIPS: 2, STATUS: 3, CONTROLS: 4, STATS: 5, INPUTS: 6, BUILD: 7 };
const FLAG_ACK_REQ = 0x02;
const TYPE_UINT16 = 0x03;
const TYPE_ENUM = 0x10;

function buildPacket(cmd, payload = [], flags = 0) {
  const body = [flags, payload.length & 0xff, payload.length >> 8, cmd, ...payload];
  return [0xaa, ...body, body.reduce((a, b) => a ^ b, 0)];
}

class PacketParser {
  constructor() { this.buffer = []; }
  feed(bytes) {
    this.buffer.push(...bytes);
    const packets = [];
    for (;;) {
      while (this.buffer.length && this.buffer[0] !== 0xaa) this.buffer.shift();
      if (this.buffer.length < 6) break;
      const length = this.buffer[2] | (this.buffer[3] << 8);
      if (this.buffer.length < 6 + length) break;
      const body = this.buffer.slice(1, 5 + length);
      const checksum = this.buffer[5 + length];
      if (body.reduce((a, b) => a ^ b, 0) !== checksum) {
        this.buffer.shift();
        continue;
      }
      packets.push({ flags: body[0], cmd: body[3], payload: body.slice(4) });
      this.buffer.splice(0, 6 + length);
    }
    return packets;
  }
}

class Device {
  constructor(board) {
    this.board = board;
    this.parser = new PacketParser();
    this.inbox = [];
  }

  pump() {
    if (this.board.fromDevice.length) {
      this.inbox.push(...this.parser.feed(this.board.fromDevice.splice(0)));
    }
  }

  /** Wait for a packet of one of `cmds`; null on timeout. */
  wait(cmds, timeoutMs = 400) {
    let found = null;
    this.board.runUntil(() => {
      this.pump();
      const i = this.inbox.findIndex((p) => cmds.includes(p.cmd));
      if (i < 0) return false;
      found = this.inbox.splice(i, 1)[0];
      return true;
    }, timeoutMs);
    return found;
  }

  request(cmd, payload, replyCmds, timeoutMs) {
    this.board.send(buildPacket(cmd, payload));
    return this.wait([...replyCmds, CMD.NAK], timeoutMs);
  }

  getInfo(type) {
    return this.request(CMD.GET_INFO, [type], [CMD.INFO_RESPONSE]);
  }

  /** Set a control. Returns 'ack', 'nak' or 'timeout'. */
  set(control, value) {
    const payload = control.type === TYPE_UINT16
      ? [control.id, value & 0xff, value >> 8] : [control.id, value & 0xff];
    const reply = this.request(CMD.SET_CONTROL, payload, [CMD.ACK]);
    if (!reply) return 'timeout';
    return reply.cmd === CMD.ACK ? 'ack' : 'nak';
  }

  get(control) {
    const reply = this.request(CMD.GET_CONTROL, [control.id], [CMD.CONTROL_RESPONSE]);
    if (!reply || reply.cmd !== CMD.CONTROL_RESPONSE) return null;
    const p = reply.payload;
    return control.type === TYPE_UINT16 ? p[1] | (p[2] << 8) : p[1];
  }

  getPixels(start, count) {
    const reply = this.request(CMD.GET_PIXELS,
      [0, start & 0xff, start >> 8, count & 0xff, count >> 8], [CMD.PIXEL_RESPONSE], 2000);
    if (!reply || reply.cmd !== CMD.PIXEL_RESPONSE) return reply;
    const pixels = [];
    for (let i = 5; i + 2 < reply.payload.length; i += 3) {
      pixels.push(reply.payload.slice(i, i + 3));
    }
    return pixels;
  }
}

function cString(bytes, offset) {
  let end = offset;
  while (end < bytes.length && bytes[end] !== 0) end++;
  return [String.fromCharCode(...bytes.slice(offset, end)), end + 1];
}

/** Parse an INFO_CONTROLS reply the way ltp_serial_cli/device.py does. */
function parseControls(payload) {
  const controls = {};
  let o = 1;
  for (let i = 0; i < payload[0]; i++) {
    const c = { id: payload[o], type: payload[o + 1], flags: payload[o + 2] };
    c.min = payload[o + 3] | (payload[o + 4] << 8);
    c.max = payload[o + 5] | (payload[o + 6] << 8);
    o += 7;
    if (c.type === TYPE_UINT16) { c.value = payload[o] | (payload[o + 1] << 8); o += 2; }
    else { c.value = payload[o]; o += 1; }
    [c.name, o] = cString(payload, o);
    [c.description, o] = cString(payload, o);
    c.options = [];
    if (c.type === TYPE_ENUM) {
      const n = payload[o++];
      for (let j = 0; j < n; j++) {
        const value = payload[o++];
        let label;
        [label, o] = cString(payload, o);
        c.options.push({ value, label });
      }
    }
    controls[c.name] = c;
  }
  return { controls, consumed: o };
}

// ---------------------------------------------------------------------------
// Pin decoders
// ---------------------------------------------------------------------------

/**
 * Split WS2812 pin edges into frames and decode them. `now` is the cycle
 * count at the end of the capture: a frame still going out then is left out.
 */
function decodeWs(edges, now) {
  const frames = [];
  let frame = null;
  for (let i = 0; i + 1 < edges.length; i++) {
    const [cycle, level] = edges[i];
    if (level !== 1) continue;
    const fall = edges[i + 1][0];
    const nextRise = i + 2 < edges.length ? edges[i + 2][0] : null;
    if (!frame) frame = { start: cycle, highs: [], lows: [], bits: [] };
    const high = fall - cycle;
    frame.highs.push(high);
    frame.bits.push(high > (WS_T0H + WS_T1H) / 2 ? 1 : 0);
    const low = (nextRise === null ? now : nextRise) - fall;
    if (low > WS_LATCH) {
      frame.end = fall;
      frames.push(frame);
      frame = null;
    } else {
      frame.lows.push(low);
    }
  }
  for (const f of frames) {
    f.bytes = [];
    for (let i = 0; i + 7 < f.bits.length; i += 8) {
      f.bytes.push(f.bits.slice(i, i + 8).reduce((a, b) => (a << 1) | b, 0));
    }
    f.maxLow = f.lows.length ? Math.max(...f.lows) : 0;
    f.pixels = f.bits.length / 24;
  }
  return frames;
}

/** Split APA102 clocked bits into frames of bytes. */
function decodeApa(bits) {
  const frames = [];
  let current = [];
  for (let i = 0; i < bits.length; i++) {
    if (i && bits[i][0] - bits[i - 1][0] > APA_FRAME_GAP) {
      frames.push(current);
      current = [];
    }
    current.push(bits[i][1]);
  }
  if (current.length) frames.push(current);
  return frames.map((f) => {
    const bytes = [];
    for (let i = 0; i + 7 < f.length; i += 8) {
      bytes.push(f.slice(i, i + 8).reduce((a, b) => (a << 1) | b, 0));
    }
    return bytes;
  });
}

// ---------------------------------------------------------------------------
// Reference: the same pattern code compiled for the host
// ---------------------------------------------------------------------------

function buildHarness() {
  const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'ltp-sim-')), 'fx_harness');
  const lib = path.join(ROOT, 'arduino', 'libraries', 'LtpStreamFx', 'src');
  execFileSync('g++', ['-std=gnu++11', '-O1', `-I${lib}`,
    path.join(ROOT, 'tests', 'test_firmware', 'fx_harness.cpp'),
    path.join(lib, 'ltp_fx.cpp'), '-o', out]);
  return out;
}

/** One frame: { wire: [[r,g,b]...] in wiring order, logical: [...] } */
function reference(harness, s, effect, fxTime) {
  const args = [s.num_pixels, s.matrix_width, s.matrix_layout, effect, s.brightness, s.scale,
    s.palette, s.hue, s.saturation, s.intensity, s.count, s.reverse, fxTime >>> 0, 0, 1];
  const raw = execFileSync(harness, args.map(String));
  const n = s.num_pixels;
  const triplets = (offset) => Array.from({ length: n },
    (_, i) => Array.from(raw.slice(offset + i * 3, offset + i * 3 + 3)));
  return { wire: triplets(0), logical: triplets(n * 3) };
}

/** RAM address of a firmware variable whose mangled name contains `name`. */
function symbolAddress(elfPath, name) {
  const table = execFileSync('readelf', ['-sW', elfPath], { encoding: 'utf8' });
  for (const line of table.split('\n')) {
    const parts = line.trim().split(/\s+/);
    if (parts.length >= 8 && parts[3] === 'OBJECT' && parts[7].includes(name)) {
      return parseInt(parts[1], 16) & 0xffff;
    }
  }
  throw new Error(`symbol ${name} not found in ${elfPath}`);
}

// ---------------------------------------------------------------------------
// Test runner
// ---------------------------------------------------------------------------

let failures = 0;
let checks = 0;

function check(name, ok, detail = '') {
  checks++;
  if (!ok) {
    failures++;
    console.log(`FAIL  ${name}${detail ? '  ' + detail : ''}`);
  }
  return ok;
}

function sameFrame(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  return a.every((p, i) => p[0] === b[i][0] && p[1] === b[i][1] && p[2] === b[i][2]);
}

function firstDifference(a, b) {
  if (!a || !b) return 'missing frame';
  if (a.length !== b.length) return `lengths ${a.length} and ${b.length}`;
  const i = a.findIndex((p, k) => p.join() !== b[k].join());
  return `pixel ${i}: device ${a[i]} reference ${b[i]}`;
}

const EFFECTS = ['Off', 'Scanner', 'Rainbow', 'Fire', 'Sparkle', 'Chase', 'Solid', 'Comet',
  'Waves', 'Twinkle', 'Noise', 'Plasma', 'Ripple'];

function main() {
  const hexPath = process.argv[2];
  if (!hexPath) {
    console.error('usage: node run_sim.js <firmware.hex>');
    process.exit(2);
  }
  const elfPath = hexPath.replace(/\.hex$/, '.elf');
  const harness = buildHarness();
  const runtimeAddress = symbolAddress(elfPath, 'fxRuntime');
  const periodAddress = symbolAddress(elfPath, 'wsBitPeriod');

  const board = new Board(hexPath, PINS);
  const dev = new Device(board);
  const fxTimeNow = () => board.ram(runtimeAddress, 4).reduce((a, b, i) => a + b * 2 ** (8 * i), 0);
  const cycleEffectNow = () => board.ram(runtimeAddress + 8, 1)[0];

  // ---- Boot ----
  const hello = dev.wait([CMD.HELLO], 1000);
  check('HELLO at boot', !!hello);
  if (!hello) return finish();
  check('HELLO payload', hello.payload.length === 11 && hello.payload[0] === 2
    && (hello.payload[5] | (hello.payload[6] << 8)) === 60 && hello.payload[8] === 0x81
    && hello.payload[9] === 0x86, JSON.stringify(hello.payload));

  // ---- Information ----
  const all = dev.getInfo(INFO.ALL);
  const [name] = cString(all.payload, 11);
  check('INFO_ALL name', name === 'LTP-Stream-328P', name);
  check('INFO_ALL strip geometry', all.payload.length === 11 + name.length + 1 + 4
    && all.payload.slice(-4).join() === '1,0,0,0', all.payload.slice(-4).join());

  const listReply = dev.getInfo(INFO.CONTROLS);
  const { controls, consumed } = parseControls(listReply.payload);
  check('INFO_CONTROLS parses to its end', consumed === listReply.payload.length,
    `${consumed} of ${listReply.payload.length}`);
  const names = Object.keys(controls);
  check('INFO_CONTROLS count matches HELLO', names.length === hello.payload[10],
    `${names.length} vs ${hello.payload[10]}`);
  const expected = ['brightness', 'local_mode', 'cycle_time', 'num_pixels', 'led_type',
    'matrix_width', 'matrix_layout', 'color_order', 'speed', 'scale', 'palette', 'hue',
    'saturation', 'intensity', 'count', 'reverse', 'max_fps', 'fps', 'save', 'reboot'];
  check('control names', expected.every((n) => n in controls), names.join(' '));
  check('local_mode options', controls.local_mode.options.length === 14
    && controls.local_mode.options[13].value === 255);
  console.log(`controls: ${names.length}, INFO_CONTROLS payload ${listReply.payload.length} bytes`);

  const build = dev.getInfo(INFO.BUILD);
  check('INFO_BUILD', cString(build.payload, 0)[0] === 'ltp-stream');
  const strips = dev.getInfo(INFO.STRIPS);
  check('INFO_STRIPS', strips.payload.join() === '1,0,60,0,3,0,8,0,1', strips.payload.join());
  check('INFO_STATUS', dev.getInfo(INFO.STATUS).payload.length === 7);
  check('INFO_INPUTS', dev.getInfo(INFO.INPUTS).payload.join() === '0');
  check('INFO_VERSION', dev.getInfo(INFO.VERSION).payload.slice(0, 2).join() === '2,0');

  // Mirror of the settings, kept in step with what is sent.
  const settings = {};
  for (const n of names) settings[n] = controls[n].value;
  const set = (n, value) => {
    const result = dev.set(controls[n], value);
    if (result === 'ack') settings[n] = value;
    return result;
  };
  const stats = () => {
    const p = dev.getInfo(INFO.STATS).payload;
    const u32 = (o) => p[o] + p[o + 1] * 256 + p[o + 2] * 65536 + p[o + 3] * 16777216;
    return { ignored: u32(0), frames: u32(4), trouble: p[14] | (p[15] << 8) };
  };

  // ---- Control handling ----
  check('auto_show accepted (host compatibility)', dev.set({ id: 3, type: 1 }, 1) === 'ack');
  check('frame_ack accepted (host compatibility)', dev.set({ id: 4, type: 1 }, 1) === 'ack');
  check('unknown control refused', dev.set({ id: 99, type: 2 }, 1) === 'nak');
  check('count 0 refused', dev.set(controls.count, 0) === 'nak');
  check('count 9 refused', dev.set(controls.count, 9) === 'nak');
  check('local_mode 77 refused', dev.set(controls.local_mode, 77) === 'nak');
  check('matrix_width 7 refused for 60 pixels', dev.set(controls.matrix_width, 7) === 'nak');
  check('fps is read-only', dev.set(controls.fps, 5) === 'nak');
  check('GET_PIXELS beyond the strip refused',
    dev.request(CMD.GET_PIXELS, [0, 50, 0, 20, 0], [CMD.PIXEL_RESPONSE]).cmd === CMD.NAK);
  check('defaults readable', dev.get(controls.brightness) === 64
    && dev.get(controls.num_pixels) === 60 && dev.get(controls.local_mode) === 2);

  check('max_fps 200', set('max_fps', 200) === 'ack');

  // ---- Frames ----

  /** Frames of the active output since `mark`. */
  const wsFramesSince = (mark) => {
    // Start at a rising edge that follows a latch gap, so no frame is cut in two.
    const edges = board.wsEdges;
    let i = edges.findIndex((e) => e[0] >= mark);
    if (i < 0) return [];
    while (i < edges.length && !(edges[i][1] === 1
      && (i === 0 || edges[i][0] - edges[i - 1][0] > WS_LATCH))) i++;
    return decodeWs(edges.slice(i), board.cycles);
  };

  /**
   * Freeze the animation, then compare the frame on the wire and the frame
   * read back with the host-compiled reference.
   */
  const comparePixels = (label, order = [1, 0, 2]) => {
    // Frames from here on may still be animated; only the last one, sent
    // after the clock has stopped, is compared. An effect that never changes
    // sends a single frame when the setting arrives, so start looking now.
    const mark = board.cycles;
    set('speed', 0);
    const frameMs = settings.num_pixels * 0.11 + 8;
    board.runMs(frameMs * 3 + 8);
    const effect = settings.local_mode === 255 ? cycleEffectNow() : settings.local_mode;
    const ref = reference(harness, settings, effect, fxTimeNow());
    const frames = wsFramesSince(mark).filter((f) => f.end !== undefined);
    const frame = frames[frames.length - 1];
    let wire = null;
    if (frame && frame.bits.length === settings.num_pixels * 24) {
      wire = [];
      for (let i = 0; i < frame.bytes.length; i += 3) {
        const rgb = [0, 0, 0];
        order.forEach((channel, k) => { rgb[channel] = frame.bytes[i + k]; });
        wire.push(rgb);
      }
    }
    check(`${label}: wire frame equals reference`, sameFrame(wire, ref.wire),
      firstDifference(wire, ref.wire));
    const readback = dev.getPixels(0, 0);
    check(`${label}: read-back equals reference`, Array.isArray(readback)
      && sameFrame(readback, ref.logical),
    Array.isArray(readback) ? firstDifference(readback, ref.logical)
      : `reply ${JSON.stringify(readback)}`);
    set('speed', 128);
    return ref;
  };

  /** Run the animation and measure the WS2812 waveform. */
  const measure = (label, ms) => {
    const before = stats();
    const mark = board.cycles;
    board.runMs(ms);
    const frames = wsFramesSince(mark).filter((f) => f.end !== undefined).slice(1);
    const after = stats();
    const highs = new Set();
    let maxLow = 0;
    let usPerPixel = 0;
    for (const f of frames) {
      f.highs.forEach((h) => highs.add(h));
      maxLow = Math.max(maxLow, f.maxLow);
      usPerPixel = (f.end - f.start) / CYCLES_PER_US / f.pixels;
    }
    const cut = after.trouble - before.trouble;
    const period = board.ram(periodAddress, 1)[0];
    const whole = frames.every((f) => f.bits.length === settings.num_pixels * 24);
    check(`${label}: frames captured`, frames.length >= 2, `${frames.length}`);
    check(`${label}: pulse widths are ${WS_T0H} and ${WS_T1H} cycles`,
      [...highs].every((h) => h === WS_T0H || h === WS_T1H), [...highs].join(','));
    check(`${label}: longest low ${maxLow} cycles within ${WS_MAX_LOW}`, maxLow <= WS_MAX_LOW);
    check(`${label}: no frame cut short`, cut === 0 && whole, `${cut} cut, whole=${whole}`);
    return { frames: frames.length, maxLow, usPerPixel, period, cut };
  };

  /**
   * Shortest bit period, in cycles, at which the current pattern still
   * finishes every frame. The firmware lengthens the period itself after a
   * cut frame, so a period that is still in place afterwards was enough.
   */
  let knownTrouble = 0;  // frames cut short on purpose by the search below
  const shortestPeriod = (ms) => {
    const normal = board.ram(periodAddress, 1)[0];
    let shortest = normal;
    for (let period = normal - 2; period >= 40; period -= 2) {
      board.poke(periodAddress, period);
      const before = stats().trouble;
      board.runMs(ms);
      const held = board.ram(periodAddress, 1)[0] === period && stats().trouble === before;
      if (!held) break;
      shortest = period;
    }
    board.poke(periodAddress, normal);
    board.runMs(ms);
    knownTrouble = stats().trouble;
    return shortest;
  };

  const report = [];
  const STATIC_EFFECTS = [0, 6];  // Off and Solid: one frame per change, nothing to measure
  const runEffects = (geometry, effectIds, measureMs) => {
    for (const id of effectIds) {
      const label = `${EFFECTS[id]} ${geometry}`;
      check(`${label}: selected`, set('local_mode', id) === 'ack');
      comparePixels(label);
      if (STATIC_EFFECTS.includes(id)) continue;
      const m = measure(label, measureMs);
      m.shortest = shortestPeriod(measureMs);
      check(`${label}: keeps up with the bit period ${WS_PERIOD_HEADROOM} cycles shorter`,
        m.shortest <= m.period - WS_PERIOD_HEADROOM, `shortest workable ${m.shortest}`);
      report.push({ label, ...m });
    }
  };

  const allEffects = EFFECTS.map((_, i) => i).filter((i) => i > 0);
  runEffects('strip 60', allEffects, 60);

  // A second set of parameters, so the comparison is not only of defaults.
  set('scale', 97); set('intensity', 211); set('count', 5); set('reverse', 1);
  set('hue', 150); set('saturation', 190); set('brightness', 201); set('palette', 7);
  for (const id of allEffects) {
    set('local_mode', id);
    comparePixels(`${EFFECTS[id]} strip 60, second parameter set`);
  }
  set('palette', 1);
  for (const id of [2, 3, 8, 10, 11, 12]) {
    set('local_mode', id);
    comparePixels(`${EFFECTS[id]} strip 60, one-colour palette`);
  }
  set('scale', 32); set('intensity', 128); set('count', 2); set('reverse', 0);
  set('hue', 0); set('saturation', 255); set('brightness', 64); set('palette', 0);

  // Matrix, every wiring layout.
  check('num_pixels 256', set('num_pixels', 256) === 'ack');
  check('matrix_width 16', set('matrix_width', 16) === 'ack');
  const allInfo = dev.getInfo(INFO.ALL);
  check('INFO_ALL reports 16 x 16', allInfo.payload.slice(-4).join() === '16,0,16,0',
    allInfo.payload.slice(-4).join());
  for (let layout = 0; layout < 8; layout++) {
    set('matrix_layout', layout);
    set('local_mode', 11);
    comparePixels(`Plasma 16x16 layout ${layout}`);
    set('local_mode', 7);
    comparePixels(`Comet 16x16 layout ${layout}`);
  }
  set('matrix_layout', 1);
  set('scale', 128);
  runEffects('16x16', allEffects, 110);
  set('scale', 32);

  // A non-square matrix wired in columns.
  set('matrix_width', 32); set('matrix_layout', 3);
  for (const id of [1, 3, 12]) {
    set('local_mode', id);
    comparePixels(`${EFFECTS[id]} 32x8 vertical serpentine`);
  }
  set('matrix_width', 1); set('matrix_layout', 0);

  // A long strip: the frame takes longer than the frame interval.
  check('num_pixels 300', set('num_pixels', 300) === 'ack');
  runEffects('strip 300', [1, 3, 7, 9, 10, 12], 130);

  // ---- Host commands while frames are going out ----
  set('local_mode', 3);
  let acked = 0;
  const commands = 40;
  for (let i = 0; i < commands; i++) {
    board.runMs(3 + (i * 7) % 11);  // lands at varying points of the 31 ms frame
    if (dev.set(controls.brightness, 20 + i) === 'ack') acked++;
  }
  check('commands sent during frames are all acknowledged', acked === commands,
    `${acked} of ${commands}`);
  check('last value arrived', dev.get(controls.brightness) === 20 + commands - 1);
  settings.brightness = 20 + commands - 1;
  check('no host bytes lost', stats().trouble === knownTrouble,
    `${stats().trouble - knownTrouble}`);

  // ---- Pixel data is ignored ----
  // With no frame going out the parser sees the whole packet: it is read to
  // its end, unstored, and dropped without a reply.
  set('local_mode', 0);
  board.runMs(40);
  let before = stats();
  const pixels = Array.from({ length: 300 * 3 }, (_, i) => (i * 37) & 0xff);
  const pixelFrame = buildPacket(CMD.PIXEL_FRAME, [0, 0, 0, 44, 1, ...pixels]);
  board.send(pixelFrame);
  board.send(buildPacket(CMD.SHOW));
  board.runMs(120);
  dev.pump();
  check('pixel frame and SHOW draw no reply', dev.inbox.length === 0,
    JSON.stringify(dev.inbox.map((p) => p.cmd)));
  let after = stats();
  check('pixel frame and SHOW counted as ignored', after.ignored - before.ignored === 2,
    `${after.ignored - before.ignored}`);
  check('no trouble counted for ignored pixel data', after.trouble === before.trouble);
  board.send(buildPacket(CMD.SHOW, [], FLAG_ACK_REQ));
  const refusal = dev.wait([CMD.NAK, CMD.ACK]);
  check('SHOW with ACK requested is refused', refusal && refusal.cmd === CMD.NAK
    && refusal.payload[1] === 0x08, JSON.stringify(refusal));
  check('controls still answer after pixel data', dev.get(controls.num_pixels) === 300);

  // A pixel stream aimed at the device while long frames go out overruns the
  // receive buffer. The firmware must not act on the debris, and must answer
  // again once the line has been quiet.
  set('local_mode', 3);
  board.runMs(40);
  before = stats();
  for (let i = 0; i < 4; i++) board.send(pixelFrame);
  board.runMs(4 * 80 + 40);
  dev.pump();
  check('pixel flood during frames draws no reply', dev.inbox.length === 0,
    JSON.stringify(dev.inbox.map((p) => p.cmd)));
  board.runMs(60);
  check('controls answer again after a pixel flood', dev.get(controls.num_pixels) === 300
    && dev.get(controls.local_mode) === 3 && dev.get(controls.brightness) === settings.brightness);
  after = stats();
  check('flood counted as lost host bytes', after.trouble > before.trouble);

  // ---- Shorter strip: the dropped pixels are turned off once ----
  set('local_mode', 2);
  board.runMs(40);
  let mark = board.cycles;
  check('num_pixels 20', set('num_pixels', 20) === 'ack');
  board.runMs(80);
  let frames = wsFramesSince(mark).filter((f) => f.end !== undefined);
  const blanking = frames.find((f) => f.pixels === 300);
  check('one 300-pixel frame blanks the dropped pixels', !!blanking
    && blanking.bytes.slice(20 * 3).every((b) => b === 0)
    && blanking.bytes.slice(0, 60).some((b) => b !== 0));
  check('later frames are 20 pixels', frames[frames.length - 1].pixels === 20,
    `${frames[frames.length - 1].pixels}`);

  // ---- Colour order ----
  set('local_mode', 2);
  check('color_order RGB', set('color_order', 1) === 'ack');
  comparePixels('Rainbow strip 20, RGB order', [0, 1, 2]);
  set('color_order', 6);
  comparePixels('Rainbow strip 20, BGR order', [2, 1, 0]);
  set('color_order', 0);

  // ---- APA102 ----
  set('local_mode', 11);
  mark = board.cycles;
  const apaMark = board.apaBits.length;
  check('led_type APA102', set('led_type', 1) === 'ack');
  set('speed', 0);
  board.runMs(60);
  frames = wsFramesSince(mark).filter((f) => f.end !== undefined);
  const lastWs = frames[frames.length - 1];
  check('WS2812 output blanked when switching away', !!lastWs && lastWs.pixels === 20
    && lastWs.bytes.every((b) => b === 0));
  const wsEdgeCount = board.wsEdges.length;
  board.runMs(30);
  check('WS2812 output stays idle on APA102', board.wsEdges.length === wsEdgeCount);
  const apaFrames = decodeApa(board.apaBits.slice(apaMark));
  const apa = apaFrames[apaFrames.length - 2];  // the last one may be incomplete
  const ref = reference(harness, settings, 11, fxTimeNow());
  let apaOk = !!apa && apa.length === 4 + 20 * 4 + (Math.floor(20 / 16) + 1 + 4)
    && apa.slice(0, 4).every((b) => b === 0) && apa.slice(4 + 80).every((b) => b === 0);
  for (let i = 0; apaOk && i < 20; i++) {
    const p = apa.slice(4 + i * 4, 8 + i * 4);
    apaOk = p[0] === 0xff && p[1] === ref.wire[i][2] && p[2] === ref.wire[i][1]
      && p[3] === ref.wire[i][0];
  }
  check('APA102 frame equals reference (start frame, BGR pixels, end frame)', apaOk,
    apa ? `${apa.length} bytes` : 'no frame');
  // Time from the first to the last clock edge of one whole frame.
  const apaEdges = board.apaBits.slice(apaMark).map((e) => e[0]);
  let frameStart = 0;
  let frameSpan = 0;
  for (let i = 1; i < apaEdges.length; i++) {
    if (apaEdges[i] - apaEdges[i - 1] > APA_FRAME_GAP) {
      frameSpan = apaEdges[i - 1] - apaEdges[frameStart];
      frameStart = i;
    }
  }
  const perPixel = frameSpan / CYCLES_PER_US / 20;
  set('speed', 128);
  check('led_type WS2812', set('led_type', 0) === 'ack');
  board.runMs(30);

  // ---- Cycle mode ----
  set('cycle_time', 1);
  set('local_mode', 255);
  board.runMs(300);
  const first = cycleEffectNow();
  board.runMs(1000);
  const second = cycleEffectNow();
  check('cycle mode starts at effect 1 and steps', first === 1 && second === 2,
    `${first} then ${second}`);

  // ---- Settings survive a reset ----
  set('local_mode', 9);
  set('hue', 77);
  dev.request(CMD.SET_NAME, [...Buffer.from('Sim strip')], [CMD.ACK]);
  check('save', dev.set(controls.save, 1) === 'ack');
  board.send(buildPacket(CMD.RESET));
  check('RESET acknowledged', !!dev.wait([CMD.ACK]));
  check('HELLO after reset', !!dev.wait([CMD.HELLO], 1500));
  check('settings restored after reset', dev.get(controls.hue) === 77
    && dev.get(controls.local_mode) === 9 && dev.get(controls.num_pixels) === 20);
  check('name restored after reset', cString(dev.getInfo(INFO.ALL).payload, 11)[0] === 'Sim strip');
  check('RESET_CONFIG', !!dev.request(CMD.RESET_CONFIG, [], [CMD.ACK], 1500));
  check('defaults after RESET_CONFIG', dev.get(controls.hue) === 0
    && dev.get(controls.num_pixels) === 60 && dev.get(controls.local_mode) === 2);
  check('default name after RESET_CONFIG',
    cString(dev.getInfo(INFO.ALL).payload, 11)[0] === 'LTP-Stream-328P');

  check('UART never overran (one-byte receive model)', board.uartOverruns.length === 0,
    `${board.uartOverruns.length}`);

  // ---- Report ----
  console.log('\nWS2812 waveform, 62.5 ns cycles');
  console.log('pattern                 frames  us/pixel  longest low        bit period  shortest workable');
  for (const r of report) {
    console.log(`${r.label.padEnd(24)}${String(r.frames).padStart(6)}  ${r.usPerPixel.toFixed(1).padStart(8)}`
      + `  ${String(r.maxLow).padStart(4)} cy ${(r.maxLow / CYCLES_PER_US).toFixed(2)} us`
      + `  ${String(r.period).padStart(6)} cy  ${String(r.shortest).padStart(10)} cy`);
  }
  console.log(`\nAPA102 (Plasma, 20 pixels): ${perPixel.toFixed(1)} us per pixel, frame overhead included`);
  finish();
}

function finish() {
  console.log(`\n${checks - failures} of ${checks} checks passed`);
  process.exit(failures ? 1 : 0);
}

main();
