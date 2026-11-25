// A model of the little of a Teensy 4.0 that
// arduino/ltp_stream_serial/ws2812_teensy4.h touches, so the driver can be
// compiled for the host and its logic tested (ws2812_t4_harness.cpp).
//
// What is modelled: simulated time; an LPUART transmitter with its 4-deep
// FIFO, watermark interrupt and transmission-complete flag; the cycle
// counter. Registers are small objects, so a read or write by the driver
// runs the model. Time moves only when the harness says so - while a pixel
// is "calculated", and a little on every read of a clock, which is what
// makes the driver's busy-wait loops advance.
//
// This is the UART as the IMXRT1060 reference manual and the Teensy core
// describe it, written down by the author of the driver. It can show that
// the driver's logic is right; it cannot show that the reading of the manual
// is.

#ifndef TEENSY4_STUB_ARDUINO_H
#define TEENSY4_STUB_ARDUINO_H

#include <cstddef>
#include <cstdint>
#include <deque>
#include <vector>

// ---- register bits, as in the core's imxrt.h --------------------------------

#define LPUART_CTRL_TXINV        ((uint32_t)(1 << 28))
#define LPUART_CTRL_TIE          ((uint32_t)(1 << 23))
#define LPUART_CTRL_TE           ((uint32_t)(1 << 19))
#define LPUART_STAT_TC           ((uint32_t)(1 << 22))
#define LPUART_BAUD_OSR(n)       ((uint32_t)(((n) & 0x1F) << 24))
#define LPUART_BAUD_SBR(n)       ((uint32_t)(((n) & 0x1FFF) << 0))
#define LPUART_FIFO_TXFE         ((uint32_t)(1 << 7))
#define LPUART_WATER_TXWATER(n)  ((uint32_t)(((n) & 0x03) << 0))

#define CCM_CCGR_ON              3
#define CCM_CCGR0_LPUART2(n)     ((uint32_t)(((n) & 0x03) << 28))
#define IOMUXC_PAD_SRE           ((uint32_t)(1 << 0))
#define IOMUXC_PAD_DSE(n)        ((uint32_t)(((n) & 0x07) << 3))
#define IOMUXC_PAD_SPEED(n)      ((uint32_t)(((n) & 0x03) << 6))
#define IRQ_LPUART2              21

// ---- the model ---------------------------------------------------------------

struct SimSent {
    uint64_t startNs;  // when its start bit began
    uint8_t value;
};

struct Sim {
    static const uint64_t CHAR_NS = 2500;   // ten bits at 4 Mbit/s
    static const uint64_t POLL_NS = 50;     // what one read of a clock costs
    static const unsigned FIFO_DEPTH = 4;

    uint64_t nowNs = 0;
    uint64_t irqLatencyNs = 200;  // from the interrupt condition to the handler running

    // UART state
    uint32_t ctrl = 0, baud = 0, fifoReg = 0, water = 0;
    std::deque<uint8_t> fifo;
    bool shifting = false;
    uint64_t shiftEndNs = 0;
    std::vector<SimSent> sent;
    unsigned fifoOverflows = 0;   // characters written to a full FIFO

    // Interrupt controller
    void (*handler)() = nullptr;
    bool irqEnabled = false;
    bool inHandler = false;
    bool irqPending = false;
    uint64_t irqDueNs = 0;

    // Pin and clock set-up, recorded for the harness to check
    uint32_t clockGate = 0, pinControl = 0, pinFunction = 0xFFFFFFFF;
    int irqPriority = -1;

    bool irqCondition() const {
        return irqEnabled && (ctrl & LPUART_CTRL_TIE) && fifo.size() <= (water & 0x03);
    }

    // Start the next character if the transmitter is free.
    void kick() {
        if (shifting || fifo.empty() || !(ctrl & LPUART_CTRL_TE)) return;
        sent.push_back({ nowNs, fifo.front() });
        fifo.pop_front();
        shifting = true;
        shiftEndNs = nowNs + CHAR_NS;
    }

    // Note a newly true interrupt condition, and run the handler when due.
    void service() {
        if (inHandler) return;
        if (!irqCondition()) {
            irqPending = false;
            return;
        }
        if (!irqPending) {
            irqPending = true;
            irqDueNs = nowNs + irqLatencyNs;
        }
        if (nowNs >= irqDueNs) {
            irqPending = false;
            inHandler = true;
            handler();
            inHandler = false;
            if (irqCondition()) {  // still asking: comes straight back
                irqPending = true;
                irqDueNs = nowNs + irqLatencyNs;
            }
        }
    }

    void advance(uint64_t ns) {
        uint64_t target = nowNs + ns;
        for (;;) {
            service();
            uint64_t next = target;
            if (shifting && shiftEndNs < next) next = shiftEndNs;
            if (irqPending && irqDueNs > nowNs && irqDueNs < next) next = irqDueNs;
            nowNs = next;
            if (shifting && nowNs >= shiftEndNs) {
                shifting = false;
                kick();
            }
            service();
            if (nowNs >= target) break;
        }
    }
};

inline Sim sim;

// ---- registers -----------------------------------------------------------------

struct SimLpuart {
    struct Ctrl {
        operator uint32_t() const { return sim.ctrl; }
        Ctrl& operator=(uint32_t v) { sim.ctrl = v; sim.kick(); sim.service(); return *this; }
        Ctrl& operator|=(uint32_t v) { return *this = sim.ctrl | v; }
        Ctrl& operator&=(uint32_t v) { return *this = sim.ctrl & v; }
    } CTRL;
    struct Data {
        Data& operator=(uint32_t v) {
            if (sim.fifo.size() >= Sim::FIFO_DEPTH) {
                sim.fifoOverflows++;
            } else {
                sim.fifo.push_back((uint8_t)v);
                sim.kick();
            }
            return *this;
        }
    } DATA;
    struct Water {
        operator uint32_t() const { return sim.water | ((uint32_t)sim.fifo.size() << 8); }
        Water& operator=(uint32_t v) { sim.water = v & 0x00030003; return *this; }
    } WATER;
    struct Stat {
        operator uint32_t() const { return (!sim.shifting && sim.fifo.empty()) ? LPUART_STAT_TC : 0; }
    } STAT;
    struct Fifo {
        operator uint32_t() const { return sim.fifoReg; }
        Fifo& operator|=(uint32_t v) { sim.fifoReg |= v; return *this; }
    } FIFO;
    struct Baud {
        Baud& operator=(uint32_t v) { sim.baud = v; return *this; }
    } BAUD;
    uint32_t PINCFG;
};

inline SimLpuart IMXRT_LPUART2;
inline uint32_t& CCM_CCGR0 = sim.clockGate;

inline uint32_t* portControlRegister(int) { return &sim.pinControl; }
inline uint32_t* portConfigRegister(int) { return &sim.pinFunction; }

inline void attachInterruptVector(int, void (*function)()) { sim.handler = function; }
#define NVIC_SET_PRIORITY(irq, priority) (sim.irqPriority = (priority))
#define NVIC_ENABLE_IRQ(irq)             (sim.irqEnabled = true)

// ---- clocks --------------------------------------------------------------------

inline uint32_t F_CPU_ACTUAL = 600000000;

inline uint32_t simCycleCounter() {
    sim.advance(Sim::POLL_NS);
    return (uint32_t)(sim.nowNs * 6 / 10);  // 0.6 cycles per nanosecond at 600 MHz
}
#define ARM_DWT_CYCCNT (simCycleCounter())

inline uint32_t micros() {
    sim.advance(Sim::POLL_NS);
    return (uint32_t)(sim.nowNs / 1000);
}

inline uint32_t millis() {
    return (uint32_t)(sim.nowNs / 1000000);
}

#endif // TEENSY4_STUB_ARDUINO_H
