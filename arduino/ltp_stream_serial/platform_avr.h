/**
 * LTP Stream (serial) - ATmega328P hardware layer
 *
 * Three things here differ from an ordinary Arduino sketch, all for the same
 * reason: a WS2812 frame is sent while the pattern is being calculated, and
 * nothing may stretch a gap in the data beyond about 5 us.
 *
 * 1. WS2812 bits are sent by the Timer2 compare interrupt, one bit per
 *    interrupt, at a fixed period. The pulse itself is cycle-counted inside
 *    the handler; the pattern code runs in the foreground in the time that is
 *    left and feeds bytes through a small FIFO. Pattern code therefore needs
 *    no cycle counting and no splitting into stages.
 * 2. The host UART is our own, not Arduino's Serial. During a frame its
 *    receive interrupt is off (it would delay a bit); the bit clock handler
 *    looks at the UART once per LED byte instead, every 34 us against a byte
 *    time of 87 us, so host bytes are not lost while LEDs update, however
 *    long a pixel takes to calculate.
 * 3. Time comes from Timer1 as a free-running counter. Timer0's interrupt is
 *    off during a frame, so millis() misses that time; the frame's length is
 *    added back afterwards, because the protocol parser's inter-byte timeout
 *    uses millis().
 */

#ifndef LTP_STREAM_PLATFORM_AVR_H
#define LTP_STREAM_PLATFORM_AVR_H

#include <Arduino.h>
#include <avr/io.h>
#include <avr/interrupt.h>

#if F_CPU != 16000000UL
#error "WS2812 pulse timing in platform_avr.h is counted for a 16 MHz clock"
#endif

// ============================================================================
// HOST UART
// ============================================================================

// Power of two. Commands are under 25 bytes; the size is for what arrives
// while a frame is going out, when the foreground cannot empty the buffer:
// 128 bytes cover an 11 ms frame (about 105 WS2812 pixels) of solid traffic.
#define HOST_RX_SIZE 128
#define HOST_RX_MASK (HOST_RX_SIZE - 1)

// The WS2812 bit clock handler is hand-written and reaches these by name.
extern "C" {
    volatile uint8_t hostRxBuf[HOST_RX_SIZE] __attribute__((used, externally_visible));
    volatile uint8_t hostRxHead __attribute__((used, externally_visible));
    volatile uint8_t hostRxTail __attribute__((used, externally_visible));
    // Set when a byte had to be dropped.
    volatile uint8_t hostRxOverflow __attribute__((used, externally_visible));
}

static inline void hostRxStore(uint8_t c) __attribute__((always_inline));
static inline void hostRxStore(uint8_t c) {
    uint8_t next = (uint8_t)(hostRxHead + 1) & HOST_RX_MASK;
    if (next == hostRxTail) {
        hostRxOverflow = 1;
        return;
    }
    hostRxBuf[hostRxHead] = c;
    hostRxHead = next;
}

ISR(USART_RX_vect) {
    hostRxStore(UDR0);
}

class HostPort : public Stream {
public:
    void begin(uint32_t baud) {
        // Double-speed mode, as Arduino does: 115200 baud comes out 2.1% fast
        // at 16 MHz, within what a UART tolerates.
        UBRR0 = (uint16_t)((F_CPU / 4 / baud - 1) / 2);
        UCSR0A = _BV(U2X0);
        UCSR0C = _BV(UCSZ01) | _BV(UCSZ00);  // 8N1
        UCSR0B = _BV(RXEN0) | _BV(TXEN0) | _BV(RXCIE0);
    }

    int available() override {
        return (uint8_t)(hostRxHead - hostRxTail) & HOST_RX_MASK;
    }

    int read() override {
        if (hostRxHead == hostRxTail) return -1;
        uint8_t c = hostRxBuf[hostRxTail];
        hostRxTail = (uint8_t)(hostRxTail + 1) & HOST_RX_MASK;
        return c;
    }

    int peek() override {
        return hostRxHead == hostRxTail ? -1 : hostRxBuf[hostRxTail];
    }

    // Blocking: replies are sent between frames, and the longest (the control
    // list, about 1 KB) holds the pattern for under 0.1 s.
    size_t write(uint8_t b) override {
        while (!(UCSR0A & _BV(UDRE0))) {}
        UDR0 = b;
        return 1;
    }
    using Print::write;

    void flush() override {
        while (!(UCSR0A & _BV(UDRE0))) {}
    }

    // Throw away whatever has been received.
    void discard() {
        hostRxTail = hostRxHead;
    }

    bool overflowed() {
        if (!hostRxOverflow) return false;
        hostRxOverflow = 0;
        return true;
    }
};

static HostPort hostPort;

// ============================================================================
// CLOCK (Timer1, free running)
// ============================================================================

// Prescaler 256: one tick is 16 us and the counter wraps after 1.05 s, far
// longer than the longest frame.
#define CLOCK_TICK_US        16
#define CLOCK_TICKS_PER_2MS  125

static void clockInit() {
    TCCR1A = 0;
    TCCR1B = _BV(CS12);
    TIMSK1 = 0;
}

static inline uint16_t clockTicks() {
    return TCNT1;
}

// Arduino's millisecond count, in wiring.c.
extern volatile unsigned long timer0_millis;

// Give millis() back the time it missed while Timer0's interrupt was off.
static void clockRestoreMillis(uint16_t missedTicks) {
    uint16_t ms = (uint16_t)(((uint32_t)missedTicks * CLOCK_TICK_US) / 1000);
    uint8_t sreg = SREG;
    cli();
    timer0_millis += ms;
    SREG = sreg;
}

// Milliseconds since the previous call, in steps of 2.
static uint16_t platformElapsedMs() {
    static uint16_t last;
    static uint16_t pending;  // ticks not yet handed out as milliseconds
    uint16_t now = clockTicks();
    pending += (uint16_t)(now - last);
    last = now;
    uint16_t ms = (uint16_t)(pending / CLOCK_TICKS_PER_2MS) * 2;
    pending %= CLOCK_TICKS_PER_2MS;
    return ms;
}

// ============================================================================
// WS2812 (timer-driven bit clock)
// ============================================================================

// Bytes waiting to be shifted out. Sixteen holds five pixels, which evens out
// pixels that take longer than average to calculate.
#define WS_FIFO_SIZE      16
#define WS_FIFO_MASK      (WS_FIFO_SIZE - 1)
#define WS_PRELOAD_PIXELS 4   // pixels calculated before the first bit goes out

// The interrupt handler below is hand-written and reaches these by name.
extern "C" {
    volatile uint8_t ltpWsFifo[WS_FIFO_SIZE] __attribute__((used, externally_visible));
    volatile uint8_t ltpWsHead __attribute__((used, externally_visible));     // next free slot
    volatile uint8_t ltpWsRunning __attribute__((used, externally_visible));  // bit clock active
}

// GPIOR0-2 are spare I/O registers: one-cycle access and no SREG side
// effects, which keeps the handler short.
#define WS_SHIFT_REG  GPIOR0  // byte being sent; bit 7 goes out next
#define WS_BITS_REG   GPIOR1  // bits of that byte still to send
#define WS_TAIL_REG   GPIOR2  // FIFO index of the next byte to load

static uint8_t wsBitPeriod = WS2812_BIT_PERIOD;
static uint16_t wsFrameEndTick;  // when the data line last went quiet

// One WS2812 bit per interrupt. Counting from the rising edge at 62.5 ns per
// cycle: a 0 falls after 5 cycles (312 ns), a 1 after 13 cycles (812 ns) -
// the pulse widths of ltp_328p_dual. The line then stays low until the next
// interrupt.
//
// Two bits of each byte do extra work in the low time. After the last bit the
// next byte is loaded from the FIFO; an empty FIFO stops the clock, which is
// both the normal end of a frame and what happens if the foreground falls
// behind. After the fourth bit a waiting host byte is moved from the UART to
// the receive buffer. Keeping the two apart keeps either within one period.
ISR(TIMER2_COMPA_vect, ISR_NAKED) {
    asm volatile(
        "   sbi  %[port], %[bit]"        "\n\t"  // T=0   line high
        "   sbis %[shift], 7"            "\n\t"  //       skip next if the bit is 1
        "   rjmp 1f"                     "\n\t"
        "   push r24"                    "\n\t"  // 1 bit: T=2
        "   in   r24, __SREG__"          "\n\t"  // T=4
        "   push r24"                    "\n\t"  // T=5
        "   in   r24, %[shift]"          "\n\t"  // T=7
        "   lsl  r24"                    "\n\t"  // T=8
        "   out  %[shift], r24"          "\n\t"  // T=9
        "   nop"                         "\n\t"  // T=10
        "   cbi  %[port], %[bit]"        "\n\t"  // T=13  line low
        "   rjmp 2f"                     "\n\t"
        "1: cbi  %[port], %[bit]"        "\n\t"  // 0 bit: T=5 line low
        "   push r24"                    "\n\t"
        "   in   r24, __SREG__"          "\n\t"
        "   push r24"                    "\n\t"
        "   in   r24, %[shift]"          "\n\t"
        "   lsl  r24"                    "\n\t"
        "   out  %[shift], r24"          "\n\t"
        "2: in   r24, %[bits]"           "\n\t"
        "   dec  r24"                    "\n\t"
        "   out  %[bits], r24"           "\n\t"
        "   breq 5f"                     "\n\t"
        "   cpi  r24, 4"                 "\n\t"
        "   brne 9f"                     "\n\t"
        // Mid-byte: take a host byte if one is waiting.
        "   lds  r24, %[ucsra]"          "\n\t"
        "   sbrs r24, %[rxc]"            "\n\t"
        "   rjmp 9f"                     "\n\t"
        "   push r30"                    "\n\t"
        "   push r31"                    "\n\t"
        "   lds  r24, %[udr]"            "\n\t"
        "   lds  r30, hostRxHead"        "\n\t"
        "   ldi  r31, 0"                 "\n\t"
        "   subi r30, lo8(-(hostRxBuf))" "\n\t"
        "   sbci r31, hi8(-(hostRxBuf))" "\n\t"
        "   st   Z, r24"                 "\n\t"  // the slot at head is always free
        "   lds  r30, hostRxHead"        "\n\t"
        "   inc  r30"                    "\n\t"
        "   andi r30, %[rxmask]"         "\n\t"
        "   lds  r24, hostRxTail"        "\n\t"
        "   cp   r30, r24"               "\n\t"
        "   breq 6f"                     "\n\t"
        "   sts  hostRxHead, r30"        "\n\t"
        "   rjmp 7f"                     "\n\t"
        "6: ldi  r24, 1"                 "\n\t"  // buffer full: drop the byte
        "   sts  hostRxOverflow, r24"    "\n\t"
        "   rjmp 7f"                     "\n\t"
        // Byte finished: load the next one.
        "5: push r30"                    "\n\t"
        "   push r31"                    "\n\t"
        "   in   r30, %[tail]"           "\n\t"
        "   lds  r24, ltpWsHead"         "\n\t"
        "   cp   r30, r24"               "\n\t"
        "   breq 8f"                     "\n\t"
        "   ldi  r31, 0"                 "\n\t"
        "   subi r30, lo8(-(ltpWsFifo))" "\n\t"
        "   sbci r31, hi8(-(ltpWsFifo))" "\n\t"
        "   ld   r24, Z"                 "\n\t"
        "   out  %[shift], r24"          "\n\t"
        "   in   r24, %[tail]"           "\n\t"
        "   inc  r24"                    "\n\t"
        "   andi r24, %[mask]"           "\n\t"
        "   out  %[tail], r24"           "\n\t"
        "   ldi  r24, 8"                 "\n\t"
        "   out  %[bits], r24"           "\n\t"
        "   rjmp 7f"                     "\n\t"
        // FIFO empty: stop the bit clock.
        "8: ldi  r24, 0"                 "\n\t"
        "   sts  %[timsk], r24"          "\n\t"
        "   sts  ltpWsRunning, r24"      "\n\t"
        "7: pop  r31"                    "\n\t"
        "   pop  r30"                    "\n\t"
        "9: pop  r24"                    "\n\t"
        "   out  __SREG__, r24"          "\n\t"
        "   pop  r24"                    "\n\t"
        "   reti"                        "\n\t"
        :
        : [port]   "I" (_SFR_IO_ADDR(WS2812_PORT)),
          [bit]    "I" (WS2812_BIT),
          [shift]  "I" (_SFR_IO_ADDR(WS_SHIFT_REG)),
          [bits]   "I" (_SFR_IO_ADDR(WS_BITS_REG)),
          [tail]   "I" (_SFR_IO_ADDR(WS_TAIL_REG)),
          [mask]   "M" (WS_FIFO_MASK),
          [rxmask] "M" (HOST_RX_MASK),
          [rxc]    "I" (RXC0),
          [timsk]  "i" (_SFR_MEM_ADDR(TIMSK2)),
          [ucsra]  "i" (_SFR_MEM_ADDR(UCSR0A)),
          [udr]    "i" (_SFR_MEM_ADDR(UDR0))
    );
}

static void wsInit() {
    WS2812_PORT &= ~_BV(WS2812_BIT);
    WS2812_DDR |= _BV(WS2812_BIT);
    TCCR2A = 0;
    TCCR2B = 0;
    TIMSK2 = 0;
}

// Start shifting out what is in the FIFO.
static void wsStartClock() {
    WS_SHIFT_REG = ltpWsFifo[0];
    WS_TAIL_REG = 1;
    WS_BITS_REG = 8;
    ltpWsRunning = 1;
    TCCR2A = _BV(WGM21);  // clear on compare match
    OCR2A = (uint8_t)(wsBitPeriod - 1);
    TCNT2 = 0;
    TIFR2 = _BV(OCF2A);
    TIMSK2 = _BV(OCIE2A);
    TCCR2B = _BV(CS20);   // no prescaler: counts CPU cycles
}

// Queue one pixel. Before the clock starts the FIFO is known to have room.
// Afterwards, wait for room; false means the clock ran dry first.
static inline bool wsPush(uint8_t b0, uint8_t b1, uint8_t b2, bool started) {
    if (started) {
        for (;;) {
            uint8_t used = (uint8_t)(ltpWsHead - WS_TAIL_REG) & WS_FIFO_MASK;
            if (used <= WS_FIFO_SIZE - 1 - 3) break;
            if (!ltpWsRunning) return false;
        }
    }
    uint8_t head = ltpWsHead;
    ltpWsFifo[head] = b0;
    head = (uint8_t)(head + 1) & WS_FIFO_MASK;
    ltpWsFifo[head] = b1;
    head = (uint8_t)(head + 1) & WS_FIFO_MASK;
    ltpWsFifo[head] = b2;
    ltpWsHead = (uint8_t)(head + 1) & WS_FIFO_MASK;
    // A clock that has already stopped did so before it saw these bytes.
    return !started || ltpWsRunning;
}

// Send `count` pattern pixels, then black up to `total`. False if the frame
// was cut short because the pattern could not keep up with the bit clock.
static bool wsShow(uint16_t count, uint16_t total, const uint8_t order[3]) {
    if (total == 0) return true;

    // Let the previous frame latch.
    const uint16_t latchTicks = LED_LATCH_US / CLOCK_TICK_US + 1;
    while ((uint16_t)(clockTicks() - wsFrameEndTick) < latchTicks) {}

    ltpWsHead = 0;
    uint8_t savedTimsk0 = TIMSK0;
    uint16_t maskedAtTick = 0;
    bool started = false;
    bool complete = true;
    uint8_t rgb[3];
    for (uint16_t i = 0; i < total; i++) {
        if (i < count) {
            fxNext(rgb);
        } else {
            rgb[0] = rgb[1] = rgb[2] = 0;
        }
        if (!wsPush(rgb[order[0]], rgb[order[1]], rgb[order[2]], started)) {
            complete = false;
            break;
        }
        if (!started && (i + 1 == WS_PRELOAD_PIXELS || i + 1 == total)) {
            // From here to the end of the frame nothing but the bit clock
            // may interrupt; it watches the UART itself.
            maskedAtTick = clockTicks();
            TIMSK0 = 0;
            UCSR0B &= (uint8_t)~_BV(RXCIE0);
            wsStartClock();
            started = true;
        }
    }
    while (ltpWsRunning) {}  // the clock stops itself when the FIFO empties

    TCCR2B = 0;
    wsFrameEndTick = clockTicks();
    UCSR0B |= _BV(RXCIE0);
    TIMSK0 = savedTimsk0;
    clockRestoreMillis((uint16_t)(wsFrameEndTick - maskedAtTick));

    if (!complete && wsBitPeriod < WS2812_BIT_PERIOD_MAX) {
        wsBitPeriod += WS2812_BIT_PERIOD_STEP;  // more time per pixel next frame
    }
    return complete;
}

// Go back to the fastest bit clock; called when the pattern changes.
static void wsResetPace() {
    wsBitPeriod = WS2812_BIT_PERIOD;
}

// ============================================================================
// APA102 / SK9822 (bit-banged, direct port writes)
// ============================================================================

#define APA102_PIXEL_HEADER  0xFF  // 111 + 5-bit brightness, full
#define APA102_BLACK_HEADER  0xE0  // brightness zero

static void apaInit() {
    APA102_PORT &= (uint8_t)~(_BV(APA102_DATA_BIT) | _BV(APA102_CLOCK_BIT));
    APA102_DDR |= _BV(APA102_DATA_BIT) | _BV(APA102_CLOCK_BIT);
}

static void apaWrite(uint8_t b) {
    for (uint8_t i = 0; i < 8; i++) {
        if (b & 0x80) APA102_PORT |= _BV(APA102_DATA_BIT);
        else APA102_PORT &= (uint8_t)~_BV(APA102_DATA_BIT);
        APA102_PORT |= _BV(APA102_CLOCK_BIT);
        b <<= 1;
        APA102_PORT &= (uint8_t)~_BV(APA102_CLOCK_BIT);
    }
}

// The clock is ours, so there is no timing to keep and interrupts stay on.
static bool apaShow(uint16_t count, uint16_t total, const uint8_t order[3]) {
    for (uint8_t i = 0; i < 4; i++) apaWrite(0x00);  // start frame

    uint8_t rgb[3];
    for (uint16_t i = 0; i < total; i++) {
        if (i < count) {
            fxNext(rgb);
            apaWrite(APA102_PIXEL_HEADER);
            apaWrite(rgb[order[0]]);
            apaWrite(rgb[order[1]]);
            apaWrite(rgb[order[2]]);
        } else {
            apaWrite(APA102_BLACK_HEADER);
            apaWrite(0);
            apaWrite(0);
            apaWrite(0);
        }
    }

    // End frame: half a clock per LED pushes the last pixels through the
    // chain, and 32 more zero bits make an SK9822 show the frame. Zeros, not
    // ones, so LEDs beyond `total` are not lit white.
    uint16_t endBytes = total / 16 + 1 + 4;
    for (uint16_t i = 0; i < endBytes; i++) apaWrite(0x00);
    return true;
}

// ============================================================================
// PLATFORM INTERFACE
// ============================================================================

#define HOST hostPort

static void platformInit() {
    clockInit();
    wsInit();
    apaInit();
    hostPort.begin(SERIAL_BAUD);
#ifdef HEARTBEAT_PIN
    pinMode(HEARTBEAT_PIN, OUTPUT);
#endif
}

// Send one frame from the pattern engine: `count` pixels, then black up to
// `total`. False if the frame did not go out whole.
static bool platformShow(uint8_t ledType, uint16_t count, uint16_t total, const uint8_t order[3]) {
    return ledType == FXLED_APA102 ? apaShow(count, total, order) : wsShow(count, total, order);
}

static void platformPatternChanged() {
    wsResetPace();
}

// True once if host bytes were lost since the last call.
static bool platformHostOverflow() {
    return hostPort.overflowed();
}

static void platformHostDiscard() {
    hostPort.discard();
}

static void platformReboot() {
    hostPort.flush();
    delay(10);  // let the last byte leave the shift register
    cli();
    // Jump to the reset vector. Not the watchdog: old Nano bootloaders leave
    // it running and reset forever.
    asm volatile("jmp 0");
}

static uint8_t platformLedTypeCode(uint8_t ledType) {
    return ledType == FXLED_APA102 ? LED_TYPE_APA102 : LED_TYPE_WS2812;
}

static uint8_t platformDataPin(uint8_t ledType) {
    return ledType == FXLED_APA102 ? APA102_DATA_PIN_NUMBER : WS2812_PIN_NUMBER;
}

static uint8_t platformClockPin(uint8_t ledType) {
    return ledType == FXLED_APA102 ? APA102_CLOCK_PIN_NUMBER : 0;
}

#endif // LTP_STREAM_PLATFORM_AVR_H
