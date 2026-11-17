// Run the ltp_stream_serial AVR firmware on a simulated ATmega328P with its
// UART on this process's stdin and stdout, in real time.
//
//   node serve_sim.js ../build/uno/ltp_stream_serial.ino.hex
//
// Bytes written to stdin arrive at the firmware's UART at its baud rate;
// bytes the firmware sends appear on stdout. Used by
// tests/test_firmware/test_stream_serial_sim.py, which connects the two to a
// pseudo-terminal so that the real host code (ltp_serial_cli,
// ltp_serial_sink) can talk to the firmware.

'use strict';

const { Board, F_CPU } = require('./avr_board');

const PINS = { wsPort: 'B', wsBit: 0, apaPort: 'D', apaData: 3, apaClock: 2 };
const SLICE_MS = 1;  // simulated time per turn of the event loop

const hexPath = process.argv[2];
if (!hexPath) {
  console.error('usage: node serve_sim.js <firmware.hex>');
  process.exit(2);
}

const board = new Board(hexPath, PINS);
const startedAt = process.hrtime.bigint();

process.stdin.on('data', (chunk) => board.send([...chunk]));
process.stdin.on('end', () => process.exit(0));

function slice() {
  board.runMs(SLICE_MS);
  // The pin probes are not read here; keep them from growing without bound.
  board.wsEdges.length = 0;
  board.apaBits.length = 0;
  if (board.fromDevice.length) {
    process.stdout.write(Buffer.from(board.fromDevice.splice(0)));
  }
  // Do not run ahead of real time: the host's timeouts are real.
  const simulatedMs = board.cycles / (F_CPU / 1000);
  const elapsedMs = Number(process.hrtime.bigint() - startedAt) / 1e6;
  if (simulatedMs > elapsedMs + SLICE_MS) {
    setTimeout(slice, simulatedMs - elapsedMs);
  } else {
    setImmediate(slice);
  }
}

slice();
