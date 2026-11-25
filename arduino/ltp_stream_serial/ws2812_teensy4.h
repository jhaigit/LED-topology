/**
 * LTP Stream (serial) - WS2812 output and clock for Teensy 4.x (IMXRT1062)
 *
 * A UART sends the frame while the processor calculates the pixels to come.
 * At 4 Mbit/s with its output inverted, one UART character - start bit, eight
 * data bits, stop bit, 2.5 us in all - carries two WS2812 bits. That is the
 * encoding of PJRC's WS2812Serial library. Unlike that library, nothing is
 * stored per LED: the foreground calculates a pixel, turns it into twelve
 * characters and puts them in a small ring; the UART's transmit interrupt
 * moves them from the ring into the UART's FIFO.
 *
 * The data line therefore never waits for a calculation. A pattern has the
 * 30 us a pixel takes on the wire - 18 000 CPU cycles at 600 MHz - on
 * average, and one pixel may take much longer, because the ring holds 21.
 *
 * If a pattern does not keep up, the ring runs dry and the frame is cut
 * short and counted (INFO_STATS, "buffer overflows"). The `load` control
 * says how close a pattern comes to that.
 */

#ifndef LTP_STREAM_WS2812_TEENSY4_H
#define LTP_STREAM_WS2812_TEENSY4_H

#include <Arduino.h>

// ============================================================================
// CLOCK
// ============================================================================

// Interrupts stay on during a frame, so the core's millisecond tick is good.
static void clockInit() {}

static uint16_t platformElapsedMs() {
    static uint32_t last;
    uint32_t now = millis();
    uint32_t ms = now - last;
    last = now;
    return ms > 0xFFFF ? 0xFFFF : (uint16_t)ms;
}

// ============================================================================
// UART
// ============================================================================

// The UART that transmits on WS2812_PIN_NUMBER, its interrupt and its clock
// gate: the Teensy 4.0 rows of the tables in the core's HardwareSerial*.cpp.
#if WS2812_PIN_NUMBER == 1          // Serial1
#define WS_UART             IMXRT_LPUART6
#define WS_UART_IRQ         IRQ_LPUART6
#define WS_UART_CLOCK_ON()  (CCM_CCGR3 |= CCM_CCGR3_LPUART6(CCM_CCGR_ON))
#elif WS2812_PIN_NUMBER == 8        // Serial2
#define WS_UART             IMXRT_LPUART4
#define WS_UART_IRQ         IRQ_LPUART4
#define WS_UART_CLOCK_ON()  (CCM_CCGR1 |= CCM_CCGR1_LPUART4(CCM_CCGR_ON))
#elif WS2812_PIN_NUMBER == 14       // Serial3
#define WS_UART             IMXRT_LPUART2
#define WS_UART_IRQ         IRQ_LPUART2
#define WS_UART_CLOCK_ON()  (CCM_CCGR0 |= CCM_CCGR0_LPUART2(CCM_CCGR_ON))
#elif WS2812_PIN_NUMBER == 17       // Serial4
#define WS_UART             IMXRT_LPUART3
#define WS_UART_IRQ         IRQ_LPUART3
#define WS_UART_CLOCK_ON()  (CCM_CCGR0 |= CCM_CCGR0_LPUART3(CCM_CCGR_ON))
#elif WS2812_PIN_NUMBER == 20       // Serial5
#define WS_UART             IMXRT_LPUART8
#define WS_UART_IRQ         IRQ_LPUART8
#define WS_UART_CLOCK_ON()  (CCM_CCGR6 |= CCM_CCGR6_LPUART8(CCM_CCGR_ON))
#else
#error "WS2812_PIN_NUMBER must be a UART transmit pin of the Teensy 4.0: 1, 8, 14, 17 or 20"
#endif

// Each of those pins reaches its UART through pin function 2.
#define WS_UART_PIN_FUNCTION  2

// The core clocks every UART from the 24 MHz oscillator (startup.c). Six
// samples per bit and a divisor of 1 make 4 Mbit/s: a bit is 0.25 us.
#define WS_UART_CLOCK_HZ      24000000
#define WS_UART_BAUD          4000000
#define WS_UART_OVERSAMPLING  6
#define WS_UART_DIVISOR       (WS_UART_CLOCK_HZ / (WS_UART_BAUD * WS_UART_OVERSAMPLING))
static_assert(WS_UART_DIVISOR * WS_UART_BAUD * WS_UART_OVERSAMPLING == WS_UART_CLOCK_HZ,
              "the UART clock does not divide down to the baud rate exactly");

// The transmit FIFO holds four characters on this chip. Ask for more when two
// are left: with the one that has just started going out, the interrupt then
// has 7.5 us to arrive before the line falls idle, and it runs at the highest
// priority.
#define WS_UART_FIFO_DEPTH    4
#define WS_UART_TX_WATERMARK  2
#define WS_UART_IRQ_PRIORITY  0
// Characters now in the transmit FIFO: bits 8-10 of the WATER register.
#define WS_UART_TX_COUNT()    ((WS_UART.WATER >> 8) & 0x07)

// ============================================================================
// ENCODING
// ============================================================================

// UART character for two WS2812 bits; the index holds the first bit in its
// bit 1 and the second in its bit 0. The line is inverted and idles low. The
// start bit is the rising edge of the first WS2812 bit, data bit 4 that of
// the second, and three more character bits kept high turn a 0 into a 1:
//   WS2812 0: 0.25 us high, 1.0 us low.   WS2812 1: 1.0 us high, 0.25 us low.
static const uint8_t wsUartCode[4] = { 0xEF, 0x0F, 0xE8, 0x08 };

#define WS_CHARS_PER_BYTE   4
#define WS_CHARS_PER_PIXEL  (3 * WS_CHARS_PER_BYTE)
#define WS_PIXEL_US         30     // 12 characters of 2.5 us

// ============================================================================
// RING
// ============================================================================

#define WS_RING_SIZE        256    // characters; a power of two. 21 pixels, 640 us
#define WS_RING_MASK        (WS_RING_SIZE - 1)
// Pixels calculated before the first bit goes out, so the frame starts with
// half a millisecond in hand.
#define WS_PRELOAD_PIXELS   16
static_assert(WS_PRELOAD_PIXELS * WS_CHARS_PER_PIXEL <= WS_RING_SIZE, "the preload does not fit the ring");

// If the UART takes nothing for this long, something is wrong with it: give
// up on the frame rather than wait for ever, so the host link stays alive.
#define WS_STALL_US         2000

static uint8_t wsRing[WS_RING_SIZE];
static volatile uint32_t wsHead;     // characters put in so far, by the foreground
static volatile uint32_t wsTail;     // characters taken out so far, by the interrupt
static volatile bool wsFrameOpen;    // the foreground has more pixels to add
static volatile bool wsRanDry;       // the ring emptied while the frame was open
static uint32_t wsFrameEndUs;        // micros() when the data line last went quiet

// Transmit interrupt: the FIFO is down to its watermark. Top it up.
static void wsUartIsr() {
    uint32_t tail = wsTail;
    uint32_t head = wsHead;
    while (WS_UART_TX_COUNT() < WS_UART_FIFO_DEPTH) {
        if (tail == head) {
            // Nothing to send: stop asking. If the frame is still open the
            // pattern has fallen behind, and the gap about to appear on the
            // line would end the frame at the LEDs anyway.
            WS_UART.CTRL &= ~LPUART_CTRL_TIE;
            if (wsFrameOpen) wsRanDry = true;
            break;
        }
        WS_UART.DATA = wsRing[tail & WS_RING_MASK];
        tail++;
    }
    wsTail = tail;
}

static void wsInit() {
    WS_UART_CLOCK_ON();
    WS_UART.CTRL = 0;  // transmitter off while it is set up
    WS_UART.BAUD = LPUART_BAUD_OSR(WS_UART_OVERSAMPLING - 1) | LPUART_BAUD_SBR(WS_UART_DIVISOR);
    WS_UART.PINCFG = 0;
    WS_UART.WATER = LPUART_WATER_TXWATER(WS_UART_TX_WATERMARK);
    WS_UART.FIFO |= LPUART_FIFO_TXFE;

    attachInterruptVector(WS_UART_IRQ, wsUartIsr);
    NVIC_SET_PRIORITY(WS_UART_IRQ, WS_UART_IRQ_PRIORITY);
    NVIC_ENABLE_IRQ(WS_UART_IRQ);

    // Inverted, so the idle line is low, which is what a WS2812 wants.
    WS_UART.CTRL = LPUART_CTRL_TE | LPUART_CTRL_TXINV;
    *(portControlRegister(WS2812_PIN_NUMBER)) = IOMUXC_PAD_SRE | IOMUXC_PAD_DSE(3) | IOMUXC_PAD_SPEED(3);
    *(portConfigRegister(WS2812_PIN_NUMBER)) = WS_UART_PIN_FUNCTION;
}

// True once `cycles` CPU cycles have passed since `from`.
static inline bool wsCyclesPassed(uint32_t from, uint32_t cycles) {
    return (uint32_t)(ARM_DWT_CYCCNT - from) > cycles;
}

static bool wsShow(uint16_t count, uint16_t total, const uint8_t order[3]) {
    if (total == 0) return true;

    // Let the previous frame latch.
    while ((uint32_t)(micros() - wsFrameEndUs) < LED_LATCH_US) {}

    const uint32_t cyclesPerUs = F_CPU_ACTUAL / 1000000;
    const uint32_t stallCycles = cyclesPerUs * WS_STALL_US;
    uint32_t busyCycles = 0;  // spent calculating and encoding pixels
    uint16_t done = 0;        // pixels handed to the ring
    bool sending = false;
    bool ok = true;

    wsHead = 0;
    wsTail = 0;
    wsRanDry = false;
    wsFrameOpen = true;

    for (uint16_t i = 0; i < total; i++) {
        uint32_t began = ARM_DWT_CYCCNT;
        uint8_t rgb[3];
        if (i < count) {
            fxNext(rgb);
        } else {
            rgb[0] = rgb[1] = rgb[2] = 0;
        }
        uint8_t chars[WS_CHARS_PER_PIXEL];
        uint8_t* c = chars;
        for (uint8_t k = 0; k < 3; k++) {
            uint8_t value = rgb[order[k]];  // most significant bit first
            *c++ = wsUartCode[value >> 6];
            *c++ = wsUartCode[(value >> 4) & 3];
            *c++ = wsUartCode[(value >> 2) & 3];
            *c++ = wsUartCode[value & 3];
        }
        busyCycles += ARM_DWT_CYCCNT - began;

        // Wait for room. Normally the ring is full and this is where the
        // foreground spends the time the pattern did not need.
        uint32_t waitFrom = ARM_DWT_CYCCNT;
        while (WS_RING_SIZE - (wsHead - wsTail) < WS_CHARS_PER_PIXEL) {
            if (wsCyclesPassed(waitFrom, stallCycles)) {
                ok = false;
                break;
            }
        }
        if (wsRanDry) ok = false;
        if (!ok) break;

        uint32_t head = wsHead;
        for (uint8_t n = 0; n < WS_CHARS_PER_PIXEL; n++) {
            wsRing[(head + n) & WS_RING_MASK] = chars[n];
        }
        // Closed before the last pixel is handed over: an empty ring after
        // that is the end of the frame, not a shortfall.
        if (i == total - 1) wsFrameOpen = false;
        wsHead = head + WS_CHARS_PER_PIXEL;
        done++;

        if (!sending && (done >= WS_PRELOAD_PIXELS || i == total - 1)) {
            sending = true;
            WS_UART.CTRL |= LPUART_CTRL_TIE;  // the interrupt fires at once and fills the FIFO
        }
    }
    wsFrameOpen = false;

    if (ok) {
        // All handed over. Wait for the ring, then the UART, to empty.
        uint32_t left = wsHead - wsTail;
        uint32_t waitFrom = ARM_DWT_CYCCNT;
        while (left != 0 || !(WS_UART.STAT & LPUART_STAT_TC)) {
            uint32_t now = wsHead - wsTail;
            if (now != left) {
                left = now;
                waitFrom = ARM_DWT_CYCCNT;
            } else if (wsCyclesPassed(waitFrom, stallCycles)) {
                ok = false;
                break;
            }
        }
        if (wsRanDry) ok = false;
    }
    if (!ok) {
        // Cut short: stop feeding the UART and let it finish the few
        // characters it holds.
        WS_UART.CTRL &= ~LPUART_CTRL_TIE;
        uint32_t waitFrom = ARM_DWT_CYCCNT;
        while (!(WS_UART.STAT & LPUART_STAT_TC) && !wsCyclesPassed(waitFrom, stallCycles)) {}
    }
    wsFrameEndUs = micros();

    // Load: calculation time as a share of the time the pixels take on the
    // wire. It includes the transmit interrupts that fell into it, so the
    // pattern keeps up as long as this stays under 100.
    uint32_t onePercent = (cyclesPerUs * WS_PIXEL_US * (done ? done : 1)) / 100;
    uint32_t load = busyCycles / (onePercent ? onePercent : 1);
    fxRuntime.load = load > 255 ? 255 : (uint8_t)load;
    return ok;
}

#endif // LTP_STREAM_WS2812_TEENSY4_H
