# LTP Stream (serial) - pattern-only LED controller

Firmware for an Arduino Uno / Nano (ATmega328P), a Teensy 3.2 or a Teensy 4.0
that speaks LTP serial protocol v2 but takes no pixel data. The device shows its own
patterns, chosen and tuned through controls. Every pixel is calculated while
the frame is being sent, so there is no frame buffer and the pixel count is
not limited by RAM: the same 2 KB Nano that holds 80 pixels in
`ltp_328p_dual` drives 1000 here.

Patterns and controls are listed in
[libraries/LtpStreamFx/README.md](../libraries/LtpStreamFx/README.md). Every
board has the twelve base patterns; the Teensy 4.0 has six more (Fireworks,
Bounce, Meteors, Lava Lamp, Aurora, Raindrops) that need its speed.
The ESP32 version, on WiFi, is `ltp_stream_esp32`.

## Hardware

| | ATmega328P (Uno, Nano) | Teensy 3.2 | Teensy 4.0 |
| --- | --- | --- | --- |
| Host link | UART, 115200 baud | USB serial | USB serial |
| WS2812 data | D8 | pin 2 | pin 14 |
| APA102 / SK9822 | D3 data, D2 clock (bit-banged) | 11 data, 13 clock (SPI, 4 MHz) | 11 data, 13 clock (SPI, 4 MHz) |
| LPD8806 | not supported | 11 data, 13 clock (SPI, 2 MHz) | 11 data, 13 clock (SPI, 2 MHz) |
| Largest `num_pixels` | 1000 | 4000 | 4000 |
| WS2812 frame time | 102 us per pixel | 30 us per pixel plus the calculation | 30 us per pixel |
| Patterns | 12 | 12 | 18 |
| Settings | EEPROM, address 128 | EEPROM, address 128 | EEPROM (emulated in flash), address 128 |

The 328P wiring is that of `ltp_328p_dual`, and the settings live at a
different EEPROM address, so a board can be reflashed between the two
firmwares without rewiring or losing either set of settings. Pins and limits
are in `config.h`.

On the Teensy 4.0 the WS2812 data comes from a UART, so it must be a UART
transmit pin: 1, 8, 14, 17 or 20, chosen by `WS2812_PIN_NUMBER` in
`config.h`. The default, 14, is strip 2 of an OctoWS2811 adaptor board, whose
buffer also lifts the Teensy's 3.3 V signal to 5 V. Pin 2, which the Teensy
3.2 build uses, is not a UART pin on the 4.0. Without such a buffer a WS2812
strip on 5 V may not read a 3.3 V signal reliably; that is true of any
3.3 V board.

Only one output is active at a time, selected by the `led_type` control.

## Build and upload

```bash
make build                  # Nano with the old bootloader (default BOARD)
make build-uno
make build-nano
make build-teensy32
make build-teensy40
make upload PORT=/dev/ttyUSB0
make upload-teensy32
make upload-teensy40
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

On a Teensy 4.0, also:

```bash
python -m ltp_serial_cli /dev/ttyACM0 control local_mode Fireworks
python -m ltp_serial_cli /dev/ttyACM0 control count 5         # five shells at a time
python -m ltp_serial_cli /dev/ttyACM0 info                    # the `load` control shows the cost
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

**The Teensy 3.2 build compiles but has not been run**, on hardware or in a
simulator. Its pattern engine is the same source the tests cover, and the
engine as this board's compiler builds it produces the same frames as the
host build when run in a CPU emulator (see Tests). Its output timing follows
Adafruit_NeoPixel's for this processor.

### Teensy 4.0

The data line never waits for a calculation here. A UART sends the frame: at
4 Mbit/s with its output inverted, one UART character (start bit, eight data
bits, stop bit: 2.5 us) is two WS2812 bits, the encoding of PJRC's
WS2812Serial library. Unlike that library nothing is stored per LED. The
foreground calculates a pixel, turns it into twelve characters and puts them
in a 256-character ring; the UART's transmit interrupt moves them from the
ring into its 4-character FIFO.

| | |
| --- | --- |
| Frame time | 30 us per pixel whatever the pattern costs: 300 pixels 9 ms, 4000 pixels 120 ms |
| Bit timing | 0: 0.25 us high, 1.0 us low. 1: 1.0 us high, 0.25 us low |
| Time a pattern has per pixel | 30 us on average, 18 000 CPU cycles at 600 MHz |
| One slow pixel | absorbed: the ring holds 21 pixels and starts 16 ahead |
| Interrupt latency tolerated | 7.5 us before the line pauses at all; the handler has the highest priority |

That budget is what the extended patterns spend. Counted on the engine as
this board's compiler builds it, run in a CPU emulator
(`python tests/test_firmware/arm_emu.py`):

| Pattern, heaviest settings | Instructions per pixel | Share of the pixel time at a pessimistic 2 cycles per instruction |
| --- | --- | --- |
| Base patterns | 70 to 180 | 1 to 2 % |
| Lava Lamp | 250 strip, 420 matrix | 3 %, 5 % |
| Bounce | 230 strip, 1150 matrix | 3 %, 13 % |
| Meteors | 580 strip, 270 matrix | 7 %, 3 % |
| Aurora | 780 strip, 800 matrix | 9 % |
| Raindrops | 380 strip, 1980 matrix | 4 %, 22 % |
| Fireworks | 1170 strip, 2040 matrix | 13 %, 23 % |

These are instruction counts, not timings: the emulator does not model how
long an instruction takes. On the board itself the read-only `load` control
gives the real figure: the time spent calculating as a percentage of the time
the pixels take on the wire, for the last frame. A pattern keeps up while it
stays under 100.

If a pattern does not keep up, the ring runs dry, the frame is cut short at a
pixel boundary and the "buffer overflows" counter in `INFO_STATS` goes up.
Unlike the 328P build, this one cannot slow its bit clock to compensate.

Interrupts stay on during a frame, so USB and `millis()` are unaffected. Host
commands are handled between frames; one sent during a 4000-pixel frame waits
up to 120 ms.

**The Teensy 4.0 build compiles but has not been run on a board.** What has
been checked without one:

- the pattern engine, compiled with this board's compiler and flags, produces
  the same frames as the host build for all 18 patterns (CPU emulator);
- the driver's logic - encoding, ring, interrupt handler, cutting a frame
  short - against a model of the UART, with the characters it transmits
  decoded as a WS2812 would see them
  (`tests/test_firmware/test_ws2812_teensy4.py`).

The model is the UART as I read the reference manual and the Teensy core. If
that reading is wrong somewhere, the first run on a board is what will show
it. The baud rate, inversion, FIFO and pin set-up are the ones WS2812Serial
uses; feeding the UART from a ring by interrupt, in place of that library's
DMA from a frame buffer, is new here.

## Limits

- **Teensy 4.0 patterns on other boards.** The extended patterns are compiled
  only for the Teensy 4.0. The Teensy 3.2 calculates between pixels, where
  they would not fit, and the 328P is far too slow.
- **UART boards and long WS2812 frames.** While a frame goes out, received
  bytes wait in a 128-byte buffer. Commands are short and fit; a pixel stream
  aimed at the device does not. If the buffer overruns, input is ignored
  until the line has been quiet for 20 ms, so that the middle of a broken
  packet is not taken for a command.
- **Sending the control list** (about 1 KB at 115200 baud) holds the pattern
  for under 0.1 s on the 328P.
- **`millis()`** on the 328P misses the time of each WS2812 frame and has it
  added back afterwards; it is correct to about a millisecond per frame.

## Tests

| Board | What runs | How |
| --- | --- | --- |
| ATmega328P | The firmware image on a simulated chip: protocol, every pattern against the host build, WS2812 waveform cycle by cycle | `make sim-test`, see [sim/README.md](sim/README.md) |
| Teensy 3.2 | The pattern engine as its compiler builds it, in a CPU emulator: frames equal the host build's | `pytest tests/test_firmware/test_stream_fx_arm.py` |
| Teensy 4.0 | The same, plus instructions per pixel against the pixel time, plus the WS2812 driver against a model of the UART | `pytest tests/test_firmware` |

The emulator tests need the Teensy core (`make setup-teensy`) and the Unicorn
CPU emulator (`pip install unicorn`); they are skipped without them. Only the
328P test runs a whole firmware image. For the Teensy boards nothing replaces
a run on the hardware.
