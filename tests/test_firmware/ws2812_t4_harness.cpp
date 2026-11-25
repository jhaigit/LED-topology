// Host harness for the Teensy 4.0 WS2812 driver
// (arduino/ltp_stream_serial/ws2812_teensy4.h), run against the UART model in
// teensy4_stub/Arduino.h.
//
// Usage: ws2812_t4_harness total count costNs stallPixel stallNs irqLatencyNs frames
//   total, count   as wsShow() takes them: pixels on the strip, pixels of pattern
//   costNs         time the "pattern" takes per pixel
//   stallPixel     pixel before which the foreground stalls once per frame (-1: never)
//   stallNs        for how long
//   irqLatencyNs   from the UART asking for characters to its handler running
//   frames         frames to send
//
// Pixel i of the pattern is (7i + 1, 13i + 5, 29i + 11) modulo 256, sent in
// GRB order.
//
// Output, text: one "setup" line, then per frame a "frame" line and one "c"
// line per UART character with the time its start bit began, then an
// "elapsed" line with what the driver's millisecond clock made of it all.

#include <cinttypes>
#include <cstdio>
#include <cstdlib>

#include <Arduino.h>

#define LED_LATCH_US       300
#define WS2812_PIN_NUMBER  14

static struct { uint8_t load; } fxRuntime;

static uint64_t costNs;
static long stallPixel;
static uint64_t stallNs;
static long pixelIndex;

static void fxNext(uint8_t rgb[3]) {
    long i = pixelIndex++;
    rgb[0] = (uint8_t)(7 * i + 1);
    rgb[1] = (uint8_t)(13 * i + 5);
    rgb[2] = (uint8_t)(29 * i + 11);
    sim.advance(costNs + (i == stallPixel ? stallNs : 0));
}

#include "ws2812_teensy4.h"

int main(int argc, char** argv) {
    if (argc != 8) {
        std::fprintf(stderr, "expected 7 arguments, got %d\n", argc - 1);
        return 2;
    }
    uint16_t total = (uint16_t)std::atoi(argv[1]);
    uint16_t count = (uint16_t)std::atoi(argv[2]);
    costNs = std::strtoull(argv[3], nullptr, 0);
    stallPixel = std::atol(argv[4]);
    stallNs = std::strtoull(argv[5], nullptr, 0);
    sim.irqLatencyNs = std::strtoull(argv[6], nullptr, 0);
    int frames = std::atoi(argv[7]);

    clockInit();
    platformElapsedMs();
    wsInit();
    std::printf("setup baud=%" PRIu32 " ctrl=%" PRIu32 " fifo=%" PRIu32 " water=%" PRIu32
                " clock=%" PRIu32 " pinfn=%" PRIu32 " priority=%d irq=%d\n",
                sim.baud, sim.ctrl, sim.fifoReg, sim.water, sim.clockGate, sim.pinFunction,
                sim.irqPriority, sim.irqEnabled ? 1 : 0);

    const uint8_t grb[3] = { 1, 0, 2 };
    for (int f = 0; f < frames; f++) {
        sim.sent.clear();
        pixelIndex = 0;
        uint64_t beganNs = sim.nowNs;
        bool ok = wsShow(count, total, grb);
        // Whatever the driver left in the UART goes out before the next frame.
        sim.advance(100000);
        std::printf("frame ok=%d load=%u began=%" PRIu64 " ended=%" PRIu64 " chars=%zu overflows=%u\n",
                    ok ? 1 : 0, (unsigned)fxRuntime.load, beganNs, sim.nowNs, sim.sent.size(),
                    sim.fifoOverflows);
        for (const SimSent& s : sim.sent) {
            std::printf("c %" PRIu64 " %u\n", s.startNs, (unsigned)s.value);
        }
    }
    std::printf("elapsed ms=%u now=%" PRIu64 "\n", (unsigned)platformElapsedMs(), sim.nowNs);
    return 0;
}
