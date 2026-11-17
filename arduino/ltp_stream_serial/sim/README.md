# Simulator test for ltp_stream_serial (ATmega328P)

Runs the real firmware image on a simulated Arduino Uno
([avr8js](https://github.com/wokwi/avr8js)), talks LTP serial protocol v2 to
it over the simulated UART and reads the LED pins cycle by cycle.

```bash
cd arduino/ltp_stream_serial
make sim-test
```

Needs Node.js, plus `g++` and `readelf` on the host. `npm install` fetches
avr8js into `sim/node_modules` (not checked in).

## What it checks

- **Protocol:** HELLO, every GET_INFO type, the control list as
  `ltp_serial_cli/device.py` parses it, setting and refusing controls, pixel
  read-back, device name, save / reset / factory reset.
- **Pixels:** for every pattern, on a strip, on a 16 x 16 matrix in all eight
  wiring layouts and on a matrix wired in columns, the frame on the wire and
  the frame read back equal what the same pattern source produces when
  compiled for the host (`tests/test_firmware/fx_harness.cpp`). This catches
  anything that depends on `int` being 16 bits.
- **WS2812 waveform:** pulse widths of exactly 5 and 13 cycles, and the
  longest low time inside a frame, which has to stay under 80 cycles (5 us).
- **Headroom:** each pattern must still finish its frames with the bit period
  6 cycles shorter than configured.
- **Host traffic during frames:** commands sent while frames go out are all
  acknowledged, and the UART never overruns.
- **Pixel data:** dropped without a reply, and a flood of it during long
  frames does not turn into commands.
- **APA102 output, colour order, shortening the strip, cycle mode.**

It prints a table of frame time, longest low time and the shortest workable
bit period per pattern.

## How far to trust it

The simulator executes the real instructions with their real cycle counts, so
pulse widths and the work done per pixel are exact. Two things differ from
the chip:

- It enters an interrupt 2 cycles sooner. Each bit therefore leaves the
  pattern code 2 cycles more here than on hardware; the headroom check exists
  to cover that.
- Its UART holds one received byte where the chip holds two, which makes the
  overrun check stricter than reality.

`avr_board.js` corrects one simulator error: avr8js clears the UART's
receive-complete flag on any write to `UCSR0B`, losing an unread byte, where
the ATmega328P clears it only when the byte is read or the receiver is
switched off.

Nothing here replaces a test on a real strip. In particular the 5 us limit
on the low time comes from published measurements of older WS2812, not from
the datasheet.

## Files

| File | |
| --- | --- |
| `run_sim.js` | The test: protocol, comparisons, measurements, report |
| `serve_sim.js` | Runs the firmware in real time with its UART on stdin and stdout |
| `avr_board.js` | The simulated board: CPU, timers, UART, EEPROM, pin probes |
| `package.json` | Pins the avr8js version |

## With the real host code

`serve_sim.js` lets the Python side talk to the simulated firmware.
`tests/test_firmware/test_stream_serial_sim.py` bridges it to a
pseudo-terminal and opens that with `ltp_serial_cli.LtpDevice` and with the
serial sink's renderer, exactly as they would open a USB serial port. The
test is skipped unless the Uno build and `sim/node_modules` exist.
