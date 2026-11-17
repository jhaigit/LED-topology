# LTP Stream (serial) - pattern-only LED controller

Firmware for an Arduino Uno / Nano (ATmega328P) or a Teensy 3.2 that speaks
LTP serial protocol v2 but takes no pixel data. The device shows its own
patterns, chosen and tuned through controls. Every pixel is calculated while
the frame is being sent, so there is no frame buffer and the pixel count is
not limited by RAM: the same 2 KB Nano that holds 80 pixels in
`ltp_328p_dual` drives 1000 here.

Patterns and controls are listed in
[libraries/LtpStreamFx/README.md](../libraries/LtpStreamFx/README.md).
The ESP32 version, on WiFi, is `ltp_stream_esp32`.

## Hardware

| | ATmega328P (Uno, Nano) | Teensy 3.2 |
| --- | --- | --- |
| Host link | UART, 115200 baud | USB serial |
| WS2812 data | D8 | pin 2 |
| APA102 / SK9822 | D3 data, D2 clock (bit-banged) | 11 data, 13 clock (SPI, 4 MHz) |
| LPD8806 | not supported | 11 data, 13 clock (SPI, 2 MHz) |
| Largest `num_pixels` | 1000 | 4000 |
| Settings | EEPROM, address 128 | EEPROM, address 128 |

The 328P wiring is that of `ltp_328p_dual`, and the settings live at a
different EEPROM address, so a board can be reflashed between the two
firmwares without rewiring or losing either set of settings. Pins and limits
are in `config.h`.

Only one output is active at a time, selected by the `led_type` control.

## Build and upload

```bash
make build                  # Nano with the old bootloader (default BOARD)
make build-uno
make build-nano
make build-teensy32
make upload PORT=/dev/ttyUSB0
make upload-teensy32
```

## Using it

The device runs on its own from power-up: default is a rainbow on 60 WS2812
pixels at a quarter brightness. Everything else is set over the serial
protocol, for example with the CLI:

```bash
python -m ltp_serial_cli /dev/ttyUSB0 info                    # lists the controls
python -m ltp_serial_cli /dev/ttyUSB0 control num_pixels 144
python -m ltp_serial_cli /dev/ttyUSB0 control local_mode Fire
python -m ltp_serial_cli /dev/ttyUSB0 control intensity 200
python -m ltp_serial_cli /dev/ttyUSB0 control save            # keep across power cycles
```

Under `ltp-serial-sink` it appears in the controller like any serial device,
with its controls on the Sinks page.

### What it does with pixel data

Nothing. The firmware sets the `CAPS_NO_PIXEL_INPUT` capability; pixel
commands and SHOW are read and dropped without a reply (NAK
`ERR_NOT_SUPPORTED` if the packet asked for an ACK). `ltp-serial-sink` sees
the capability, refuses stream setup for the device and sends it no frames.

`GET_PIXELS` does work: a pattern is a function of position and time, so the
firmware recalculates the requested pixels of the current frame.

## How the frame is sent

### ATmega328P, WS2812

An older WS2812 takes a gap of 6-9 us in the data as the end of the frame, so
the pattern cannot simply be calculated between pixels: most patterns need
longer than that on a 16 MHz AVR.

Instead a timer interrupt sends one bit every 68 CPU cycles (4.25 us). The
pulse is cycle-counted inside the handler; between interrupts the foreground
calculates pixels and feeds them through a 16-byte FIFO. Pattern code is
therefore ordinary C++ with no cycle counting. The handler also takes one
host byte from the UART per LED byte, so commands sent during a frame are not
lost.

Measured on a simulated ATmega328P (`make sim-test`), not on hardware:

| | |
| --- | --- |
| Frame time | 102 us per pixel: 60 pixels 6 ms, 300 pixels 31 ms, 1000 pixels 102 ms |
| Longest low time within a frame | 68 cycles, 4.25 us |
| Pulse widths | 5 cycles (312 ns) and 13 cycles (812 ns) |
| Shortest bit period each pattern keeps up with | 50-54 cycles for most, 56 for Noise, 58-60 for Fire and for Ripple on a matrix |

If a pattern cannot keep up, the frame is cut short, the counter in
`INFO_STATS` ("buffer overflows") goes up and the bit period is lengthened by
4 cycles, to at most 76, until the pattern is changed. The default of 68
leaves 8 cycles over the slowest pattern. The simulator enters an interrupt
2 cycles sooner than the chip does, which is the reason for the margin.

### ATmega328P, APA102

Bit-banged with direct port writes; the clock is ours, so there is nothing to
keep up with. About 50 us per pixel with the pattern (simulated, Plasma).

### Teensy 3.2

The Cortex-M4 calculates a pixel in a few microseconds, so WS2812 is
bit-banged against the cycle counter with the pattern calculated between
pixels, interrupts off for the frame (USB holds the host's data meanwhile):
30 us per pixel on the wire plus the calculation. APA102 and LPD8806 go out
over hardware SPI.

**The Teensy build compiles but has not been run**, on hardware or in a
simulator. Its pattern engine is the same source the tests cover; its output
timing follows Adafruit_NeoPixel's for this processor.

## Limits

- **UART boards and long WS2812 frames.** While a frame goes out, received
  bytes wait in a 128-byte buffer. Commands are short and fit; a pixel stream
  aimed at the device does not. If the buffer overruns, input is ignored
  until the line has been quiet for 20 ms, so that the middle of a broken
  packet is not taken for a command.
- **Sending the control list** (about 1 KB at 115200 baud) holds the pattern
  for under 0.1 s on the 328P.
- **`millis()`** on the 328P misses the time of each WS2812 frame and has it
  added back afterwards; it is correct to about a millisecond per frame.

## Simulator test

`make sim-test` builds the Uno image and runs it on a simulated ATmega328P.
See [sim/README.md](sim/README.md).
