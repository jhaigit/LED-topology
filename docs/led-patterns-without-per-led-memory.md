# LED patterns without per-LED memory

2026-11-16. Research report written by Claude in a Claude Code session on this
repository; "I" and "my" below refer to Claude. Exported from the Claude Doc at
<https://claude.ai/artifact/Hq8j8dENLjrfsbRAUxU27t>, whose bar chart is given
here as a table.

## Summary

Nearly every familiar LED effect can be calculated while the data goes out. Only effects that feed on their own previous frame cannot.

- **What works:** anything that is a function of position, time and a few parameters. That covers chases, comets, waves, rainbows, noise, plasma, text, twinkles and noise-based fire. Trails and random twinkles, usually done with a fade buffer, have calculated forms.
- **What does not:** simulations with feedback (diffusion fire, Game of Life, fluids), general blur or fade, and host frames that must be held.
- **Cost on a 16 MHz ATmega328P, measured in simulation:** 8 to 255 cycles per pixel for string patterns and 6 to 400 for matrix patterns short of fractals, so 0.4 to 25 µs. A divide per pixel adds about 200 cycles. A blended 16-entry palette costs 113–132 cycles against 19 for a 256-entry one.
- **One-wire strips (WS2812):** the calculation has to fit in the gaps between bytes. Older strips allow about 80 cycles per gap, because they latch after 6–9 µs, not the datasheet's 50 µs. Simple patterns fit as they are; palette patterns need splitting across the three colour bytes. Peripheral-driven output (PIO, RMT) gives the CPU the full 30 µs per pixel instead.
- **Clocked strips (APA102, SK9822, LPD8806):** no limit on calculation per pixel; frame rate pays for it. On hardware SPI a 16 MHz AVR refreshes 1,783 LEDs of plasma or 451 LEDs of Julia set at 30 fps. The costs are rolling updates on slow frames and, on the 328P sketch today, a `digitalWrite` output path that caps the strip near 83 LEDs at 30 fps.

## What "no per-LED memory" allows

The pattern has to be a function evaluated in output order: each pixel's colour is calculated from its position, the frame time and a few parameters, then sent and forgotten. RAM use no longer grows with pixel count; only frame time does.

In `ltp_328p_dual` the frame buffer is the limit today: 2 KB of RAM holds `MAX_PIXELS` 80. A streamed pattern uses the same RAM for 80 LEDs or 8,000.

Three things are lost:

- **Read-back.** Nothing can depend on a pixel's previous value, so no fade-to-black, blur, scroll-by-copy or diffusion.
- **Random-access drawing.** "Set pixel 37" becomes a test made at every pixel: does any object cover me?
- **Buffered host frames.** Pixel data from the controller has to be forwarded as it arrives, or not used.

State that is still allowed:

| State | Size | Holds |
| --- | --- | --- |
| Global | a few bytes | frame counter, phase accumulators, PRNG seed, palette and speed settings |
| Per object | 3–6 bytes × K objects | comets, balls, sparks, raindrops, scroll position |
| Per row or column | W or H bytes | spectrum bar heights, column offsets, one row of a 1D automaton |
| ROM tables | flash, not RAM | sine, gamma, palettes, noise permutation, fonts, sprites, coordinate maps |

A per-LED table in flash, such as radius and angle for each pixel of a fixed matrix, fits the rule but costs 1–2 bytes of flash per LED.

## How much calculation fits per pixel

One-wire LEDs give a small fixed window per pixel; clocked LEDs give as much time as you will pay for in frame rate.

### One-wire: WS2812, SK6812

Each pixel is 24 bits at 1.25 µs, so 30 µs on the wire. Only the high pulse is tightly timed. The low gap between bits can stretch until the strip takes it for a latch.

| Chip generation | Latch seen after | Safe pause mid-frame |
| --- | --- | --- |
| Older WS2812 and WS2812B | 6 µs ([Levine](https://wp.josh.com/2014/05/13/ws2812-neopixels-are-not-so-finicky-once-you-get-to-know-them/)) to 9 µs ([cpldcpu](https://cpldcpu.com/2014/01/14/light_ws2812-library-v2-0-part-i-understanding-the-ws2812/)), against 50 µs in the datasheet | about 5 µs |
| Newer WS2812B | 250–280 µs minimum ([Verbeure](https://tomverbeure.github.io/2019/10/13/WS2812B_Reset_Old_and_New.html), Levine's 2020 note) | not measured in any source I found |

FastLED's Teensy 3 driver sits between the two: it [abandons the frame](https://github.com/FastLED/FastLED/blob/3.6.0/src/platforms/arm/k20/clockless_arm_k20.h) when a gap between pixels passes 45 µs.

How the waveform is generated sets the budget:

- **Bit-banged, as on the 328P.** The CPU is busy for all 21 cycles of every bit. Calculation goes into stretched gaps: up to 80 cycles (5 µs at 16 MHz) between bytes, three times per pixel. Levine notes that work can also be spread over all 24 bit gaps if it splits into very short units.
- **Peripheral-driven** (hardware SPI or UART encoding, ESP32 RMT, RP2040 PIO). The CPU has the whole 30 µs per pixel while the peripheral shifts, as long as its FIFO never runs dry. The [Raspberry Pi Pico WS2812 example](https://github.com/raspberrypi/pico-examples/blob/master/pio/ws2812/ws2812.c) works this way: each pattern computes a pixel and pushes it to the PIO FIFO, with no pixel array.

All LEDs change together at the latch, so slow calculation never shows as tearing. A gap that runs too long shows instead as a frame that restarts at LED 0.

### Clocked: APA102, SK9822, LPD8806, WS2801

The controller owns the clock and can stop it between any two bits.

| Chip | Bits per pixel | Pause limit mid-frame | Output changes |
| --- | --- | --- | --- |
| APA102 | 32 | none: "no specific timing requirements" ([Pololu](https://github.com/pololu/apa102-arduino)) | each LED as its data arrives ([cpldcpu](https://cpldcpu.com/2014/11/30/understanding-the-apa102-superled/)) |
| SK9822 | 32 | none | sources disagree: at the next start frame ([cpldcpu](https://cpldcpu.com/2016/12/13/sk9822-a-clone-of-the-apa102/), measured) or on arrival (Pololu) |
| LPD8806 | 24, 7-bit colour | none | each byte as it arrives ([Adafruit driver notes](https://github.com/adafruit/LPD8806/blob/master/LPD8806.cpp)) |
| WS2801 | 24 | under 500 µs of clock low, which latches ([datasheet](https://www.superlightingled.com/PDF/WS2801.pdf)) | all together at the latch |

Wire time per pixel is bits ÷ clock rate: 8 µs for APA102 at 4 MHz and 12 µs for LPD8806 at 2 MHz, the rates `ltp_serial_v2` uses.

To turn a time budget into work: a 16 MHz AVR runs 16 cycles per µs, a 72 MHz Teensy 3.2 runs 72, a 240 MHz ESP32 runs 240.

## Building blocks

A handful of primitives cover almost every pattern below, and on a 16 MHz ATmega328P most cost under 4 µs per pixel. The expensive ones are blended palettes, 2D noise and any divide.

| Primitive | Gives | Flash | Cycles per pixel |
| --- | --- | --- | --- |
| Counter and mask | stripes, chases, bit textures | 0 | 6–8 |
| Distance to an object, tail-shape table | comets, scanners, balls | 32 bytes | 9 for one, about 18 per extra object |
| 256-entry palette, read directly | any colour ramp | 768 bytes | 19 |
| Three brightness scalings (`scale8`) | global or per-pixel dimming | 0 | 19 |
| Phase accumulator, sine table, one scaling | waves | 256 bytes | 22 |
| Integer hash of index and frame | random values that need no storage | 0 | 59 |
| 1D value noise | organic motion along a strip | 512 bytes | about 40 |
| 2D value noise | fire, clouds, lava | 512 bytes | about 120 |
| 16-entry palette, blended | the same ramp in 48 bytes | 48 bytes | 113–132 |
| `hsvToRgb` as in `ltp_328p_dual` | hue to RGB | 0 | about 150 |
| A divide by a variable | nothing worth it | 0 | about 200 |

Measured on a simulated ATmega328P (avr8js), compiled with avr-gcc 7.3 at `-Os` as Arduino builds, plain C with no assembler. Figures exclude the 13-cycle loop.

Five habits follow from the table:

- **Never divide per pixel.** The rainbow as written today (`i * 256 / numPixels`) costs 356 cycles; the same pattern with a per-pixel add costs 151.
- **Accumulate, don't multiply.** Add a step to a 16-bit phase at each pixel; work out the step once per frame.
- **Count coordinates in output order.** For a matrix, x and y come from counters, and serpentine wiring is a direction flip per row. That is the inverse of `mapPixel`, at 15 cycles.
- **Hash instead of store.** `hash(index)` is a per-pixel constant; `hash(index, frame)` is a fresh random value. [TwinkleFox](https://gist.github.com/kriegsman/756ea6dcae8e30845b5a) does the same by reseeding its random generator every frame, so the sequence "stores" each pixel's parameters.
- **Sample time once per frame.** A clock read per pixel shears the pattern along the strip.

The model is an old one. [Pixelblaze](https://github.com/simap/pixelblaze/blob/master/README.expressions.md) patterns are a `render(index)` or `render2D(index, x, y)` function called for each pixel. [tixy.land](https://tixy.land/) patterns are a function of `(t, i, x, y)`. The Atari 2600 had [no frame buffer](https://en.wikipedia.org/wiki/Television_Interface_Adaptor) and drew every scan line on the fly. FastLED's [AVR driver](https://github.com/FastLED/FastLED/blob/3.6.0/src/platforms/avr/clockless_trinket.h) already loads, dithers and scales the next byte "in between writing the bits".

## Patterns for strings

Every common strip effect has a form with no per-LED state, and on the 328P all of them cost under 16 µs per pixel. Where two costs are given, the first uses a 256-entry palette and the second a blended 16-entry one.

| Pattern | How each pixel is found | State kept | Cycles per pixel |
| --- | --- | --- | --- |
| Solid, stripes, chase, theatre chase, binary counter | index plus frame counter, masked | frame counter | 8 |
| Colour wipe, progress bar, level meter | compare index with a moving edge | edge position | about 8 |
| Comet, Larson scanner, bouncing balls, meteors | distance behind each head indexes a tail-shape table; head positions come from closed-form motion once per frame | 2–4 bytes per object | 9 for one, 68 for four, 148 for eight |
| Breathing, strobe, lightning | one brightness for the whole frame | none | 19 |
| Running lights, sine waves | phase accumulator into a sine table | one phase | 22 |
| Rainbow, palette scroll | phase accumulator into a palette | one phase | 19 / 132 |
| Gradient between two colours | blend by position | none | 61 |
| Glitter, static, random sparkle | `hash(index, frame)` under a density threshold | frame seed | 59 |
| Lava, ocean, aurora, clouds | 1D noise of position plus time, through a palette | time | 58 / 172 |
| Interfering waves, with gamma and brightness | two sines summed, palette, gamma table, scaling | two phases | 80 / 205 |
| Twinkle that fades in and out | `hash(index)` gives each pixel a phase, speed and colour; brightness is a triangle wave of time | time | 136 / 255 |
| Fire | 2D noise over position and time, cooled with height, heat ramp | time | 140 |

Layered-wave effects such as FastLED's [Pride2015](https://gist.github.com/kriegsman/964de772d64c502760e5) fit the same mould: its per-pixel core is two accumulators and a sine lookup. It also blends each pixel with the previous frame, which a streamed version has to drop.

Objects are the one family whose cost grows: each pixel tests every object, so eight comets cost 148 cycles. Sorting objects by position, so a pixel only tests its neighbours, removes that growth.

## Patterns for matrices

A matrix adds x and y, and with them the whole family of shader-style effects; all but fractals stay within 25 µs per pixel on the 328P. Costs were measured on a 16 × 16 serpentine matrix and are again direct palette / blended palette.

| Pattern | How each pixel is found | State kept | Cycles per pixel |
| --- | --- | --- | --- |
| Sierpinski triangle, bit-field textures | `x AND (y + t)` is zero; other AND, OR, XOR mixes of x, y, t | frame counter | 6 |
| Bar graph, spectrum analyser | compare y with the level for column x | one byte per column | about 8 |
| Checkerboard, rotozoom | rotate and scale x, y with four multiplies; XOR the integer parts | angle and zoom | 19 |
| XOR plaid through a palette | `(x XOR y) + t` | frame counter | 23 / 135 |
| Scrolling text, icons, sprites | font column from flash, test bit y | scroll offset | 42 |
| Rings from a point | sine of distance squared, which needs no square root | centre, time | 36 / 148 |
| Spiral, pinwheel, starburst, tunnel | angle and radius per LED from a flash map, into a sine | time | 47 / 158 |
| Plasma | three sines of x, y and x + y, summed | three phases | 61 / 174 |
| Digital rain, snow, shooting stars | `hash(x)` gives each column a speed and offset; brightness by distance behind the head | time | 121 |
| Fire | 2D noise scrolling upward, cooled with height | time | 140 |
| Metaballs, lava lamp (three blobs) | sum of 1/d² table lookups | 2 bytes per blob | 139 / 250 |
| Evenly spaced ripples | integer square root of distance squared, into a sine | centre, time | 182 / 293 |
| Clouds, lava (two octaves of 2D noise) | noise at two scales, summed | time | 288 / 400 |
| Julia or Mandelbrot set | 8.8 fixed point, up to 16 iterations | viewport or constant | about 1,000 average, 3,400 worst case |

Three notes on the table:

- **Any string pattern becomes a matrix pattern** by feeding it x, y, x + y or radius in place of the index.
- **Text needs care with division.** Finding the character with `/ 6` and `% 6` per pixel costs 451 cycles; one multiply by 43 and a shift does the same job in 42.
- **Polar effects want a flash map.** Angle and radius per LED cost 2 bytes of flash each, and symmetry can cut that to an eighth. Calculating an arctangent per pixel is the alternative.

## Effects that normally need a buffer

Most buffered effects use the buffer for one of two things, a fading trail or a random choice that must persist, and both have a calculated substitute.

| Buffered effect | What the buffer was for | Stateless substitute | What changes to the eye |
| --- | --- | --- | --- |
| Scanner or comet with a fading trail | each frame dims the whole buffer | tail brightness from a table indexed by distance behind the head | nothing, if the table matches the fade curve |
| Sparkle with fade-out | lit pixels dim over later frames | each pixel runs its own triangle-wave clock, with phase and speed from `hash(index)` | pixels fade in as well as out |
| Random twinkle positions | remembering which pixels were picked | add the pixel's cycle number to the hash, so each cycle picks a new colour and on/off choice | none |
| Fire (heat array, as in Fire2012) | heat diffuses upward frame to frame | noise field scrolled toward the tip, cooled with height | smoother flames, fewer distinct sparks; hash sparks at the base help |
| Confetti, juggling dots | fade-to-black behind moving dots | a few objects with calculated tails | tail length follows speed instead of time |
| Fireworks, particles | additive drawing into the buffer | per-particle state stays; each pixel sums the particles near it | none, but cost grows with particle count |
| Ripples from a touch or event | drawing into the buffer | per ripple: start time and centre; brightness from distance to the expanding edge | none |
| Glow, blur | reading neighbouring pixels | build the width into the shape: a wider tail table or sine | none for simple shapes |
| Scrolling by copying | the previous frame | recalculate at an offset | none |

Two cases fall between calculated and stored:

- **One-dimensional cellular automata** drawn down a matrix need one row of state, W bits. Rule 90 from a single seed needs none: it is the Sierpinski test in the matrix table.
- **Game of Life** needs one bit per cell, 32 bytes for 16 × 16. That is per-LED memory, but a twenty-fourth of a frame buffer.

A whole-frame statistic, such as a power limit that scales the frame to a current budget, has a calculated form too: evaluate the pattern twice, once to total it and once to send it. The price is double the calculation.

## Clocked LEDs: what the loose timing buys

A clocked strip waits for the controller, so every pattern in this report runs at any cost per pixel. The price is frame rate, and on a small controller the output method sets that price more than the pattern does.

```
frames per second = 1 / (N × (t_calculate + t_wire))
```

N is the number of LEDs; both times are per pixel.

**Output method outweighs pattern: 1,783 LEDs of plasma over SPI, 83 through
`digitalWrite`.** APA102 LEDs a 16 MHz ATmega328P can refresh at 30 fps:

| Pattern | Output method | Cycles per pixel (pattern + output) | LEDs at 30 fps |
| --- | --- | --- | --- |
| Chase | hardware SPI, 8 MHz | 8 + 125 | 4,010 |
| Plasma | hardware SPI, 8 MHz | 174 + 125 | 1,783 |
| Two-octave noise | hardware SPI, 8 MHz | 400 + 125 | 1,015 |
| Plasma | direct port writes | 174 + 423 | 893 |
| Julia set, average | hardware SPI, 8 MHz | 1,055 + 125 | 451 |
| Julia set, worst case | hardware SPI, 8 MHz | 3,491 + 125 | 147 |
| Plasma | `digitalWrite` | 174 + 6,236 | 83 |

Measured on a simulated ATmega328P (avr8js), with a blended 16-entry palette.

Per pixel, the 328P spends 125 cycles sending over 8 MHz hardware SPI, 189 at 4 MHz, 423 with direct port writes and 6,236 through `digitalWrite`. Against that, the gap between a chase and a plasma is 166 cycles.

### What becomes possible

- **Slow patterns.** Iterated fractals, several octaves of noise and dozens of objects all run; nothing in the tables is excluded.
- **Supersampling.** Evaluate the pattern at two to four positions per LED and average, which removes the stepping of slow-moving edges.
- **More dynamic range.** Each APA102 frame carries a 5-bit brightness beside the 8-bit colours, and both can be chosen per pixel for finer steps at the dim end. The APA102 implements that field as a second, slower PWM; the SK9822 as drive current ([cpldcpu](https://cpldcpu.com/2016/12/13/sk9822-a-clone-of-the-apa102/)).
- **Pass-through of slow data.** Pixels read from serial, an SD card or the network can be forwarded as they arrive, with no frame held in RAM. At 115,200 baud that is 3,840 pixels a second: 48 fps for 80 LEDs, 13 fps for 300.
- **Interrupts stay on.** Interrupts shorter than a few milliseconds do not disturb an APA102 frame ([Pololu](https://github.com/pololu/apa102-arduino)), so serial input survives it. A bit-banged WS2812 frame holds interrupts off for 31.5 µs per pixel.
- **Very high refresh on short strips.** Sixty LEDs of chase take 0.5 ms over SPI, about 2,000 frames a second. That is enough for temporal dithering driven by nothing more than the frame counter.

### What it costs

- **Rolling updates.** An APA102 shows each pixel as it arrives. A 300-LED Julia frame takes 22 ms to paint from one end to the other, and fast motion shears. An SK9822 that holds data until the next start frame would change all at once; sources disagree on whether it does, so test the strip in hand.
- **WS2801 is the exception.** More than 500 µs of idle clock latches the frame. Even the worst-case Julia pixel (226 µs) fits.
- **Wiring limits the clock before the chip does.** cpldcpu reports no trouble at 4 MHz and others above 10 MHz; Adafruit runs the LPD8806 at 2 MHz because of unshielded wiring. A 16 MHz AVR tops out at 8 MHz, where wire time is already smaller than most calculations.
- **APA102 end frame.** The strip needs half a clock pulse per LED after the last pixel ([cpldcpu](https://cpldcpu.com/2014/11/30/understanding-the-apa102-superled/)). That is a counter, not memory.

## What does not work

Anything where a pixel's next value depends on its own or its neighbours' last value needs stored state, and no calculation replaces it.

- **Simulations with feedback:** heat-diffusion fire, falling sand, fluids, reaction-diffusion, wave-equation ripples, Game of Life.
- **General fade, blur or smear** applied to whatever was drawn last frame. Only shapes with a known history, such as a moving head, have a calculated trail.
- **Frames supplied by a host and held.** Without a buffer a frame can be forwarded once as it arrives, but not redisplayed, re-sent after a glitch or read back.
- **Cross-fades between two stored frames.** A cross-fade between two calculated patterns does work: evaluate both and blend, at the sum of their costs.
- **Per-pixel random walks.** A hash gives each pixel a repeatable random value, not a value that drifts from its previous one.
- **Drawing in layers by read-modify-write.** Layers have to be summed in a local variable while the pixel is being calculated.

## What this means for the LED-topology firmware

All five local modes in `ltp_328p_dual` can be regenerated without `pixelBuffer`, which would lift the 80-LED limit for local modes. Frames sent by the host still need the buffer.

| Local mode | Today | Streamed form | Cycles per pixel |
| --- | --- | --- | --- |
| Chase | already stateless: cleared and redrawn each frame | brightness by distance behind the head | about 9 |
| Cylon | fades the buffer through `getPixel` | tail table behind the eye | about 9 |
| Rainbow | already stateless, with a divide per pixel | hue accumulator | 151 with `hsvToRgb`, 19 with a 256-entry palette |
| Fire | `heat[MAX_PIXELS]` array | noise fire | 140 |
| Sparkle | fades the buffer through `getPixel` | hash twinkle | 136 / 255 |

Three findings from reading and timing the sketches:

1. **The WS2812 gap budget is tighter than the datasheet's 50 µs.** Today's gap between bytes is about 2 µs and safe. A streamed pattern gets 80 cycles per gap on older strips, which latched at 6–9 µs when measured. Chase and Cylon fit in one gap. Palette patterns fit only if the work is split across the three colour bytes. The rainbow as written (356 cycles) would not fit. Newer 280 µs strips should tolerate far more, but I found no measurement of how much.
2. **APA102 output on the 328P is limited by `digitalWrite`, not by RAM.** `apa102SendByte` costs 6,236 cycles per pixel, so 80 LEDs take 31 ms and the strip cannot pass 32 fps. Direct port writes on the same pins (D3 and D2) cut that to 423 cycles. Hardware SPI at 125 cycles needs the strip moved to D11 and D13.
3. **The Teensy sketches gain no RAM from streaming.** Their 64 KB holds every frame, and OctoWS2811 needs its buffer for DMA. The gain there is one shared set of per-pixel pattern functions: buffered boards call them to fill the buffer, the 328P calls them from its send loop.

## Sources

Cycle counts are my own measurements, made for this report: 35 pattern kernels compiled with avr-gcc 7.3 at `-Os` for the ATmega328P and run in the avr8js simulator, plus an APA102 output sketch built with arduino-cli for `arduino:avr:nano`. They have not been checked on hardware.

- Josh Levine, [WS2812 NeoPixel timing](https://wp.josh.com/2014/05/13/ws2812-neopixels-are-not-so-finicky-once-you-get-to-know-them/) (2014, updated 2020): timing limits, the 5 µs gap, driving strings with no pixel buffer
- cpldcpu, [Understanding the WS2812](https://cpldcpu.com/2014/01/14/light_ws2812-library-v2-0-part-i-understanding-the-ws2812/) (2014): measured reset at 8.95 µs
- Tom Verbeure, [WS2812B reset, old and new](https://tomverbeure.github.io/2019/10/13/WS2812B_Reset_Old_and_New.html) (2019): 50 µs versus 280 µs parts
- FastLED 3.6.0, [Teensy 3 clockless driver](https://github.com/FastLED/FastLED/blob/3.6.0/src/platforms/arm/k20/clockless_arm_k20.h) and [AVR clockless driver](https://github.com/FastLED/FastLED/blob/3.6.0/src/platforms/avr/clockless_trinket.h): 45 µs frame abort; work interleaved between bits
- Raspberry Pi, [pico-examples WS2812](https://github.com/raspberrypi/pico-examples/blob/master/pio/ws2812/ws2812.c): patterns pushed pixel by pixel into the PIO FIFO
- cpldcpu, [Understanding the APA102](https://cpldcpu.com/2014/11/30/understanding-the-apa102-superled/) (2014) and [SK9822, a clone of the APA102?](https://cpldcpu.com/2016/12/13/sk9822-a-clone-of-the-apa102/) (2016): frame format, end frame, update timing, brightness field
- Pololu, [apa102-arduino](https://github.com/pololu/apa102-arduino): no timing requirements, send-as-you-compute interface, SK9822 behaviour
- Adafruit, [LPD8806 driver](https://github.com/adafruit/LPD8806/blob/master/LPD8806.cpp): 7-bit colour, latch bytes, 2 MHz clock
- WorldSemi, [WS2801 datasheet](https://www.superlightingled.com/PDF/WS2801.pdf) (preliminary V0.3): 500 µs latch, 25 MHz maximum clock
- Mark Kriegsman, [TwinkleFox](https://gist.github.com/kriegsman/756ea6dcae8e30845b5a) and [Pride2015](https://gist.github.com/kriegsman/964de772d64c502760e5)
- ElectroMage, [Pixelblaze expression language](https://github.com/simap/pixelblaze/blob/master/README.expressions.md)
- [tixy.land](https://tixy.land/)
- Wikipedia, [Television Interface Adaptor](https://en.wikipedia.org/wiki/Television_Interface_Adaptor)
