// The little of Arduino.h that arduino/libraries/LtpProtocol needs, so its
// parser can be compiled and tested on the host.

#ifndef ARDUINO_STUB_H
#define ARDUINO_STUB_H

#include <cstddef>
#include <cstdint>
#include <deque>
#include <vector>

// Test-controlled clock.
extern uint32_t stubMillis;
inline uint32_t millis() { return stubMillis; }

class Stream {
public:
    std::deque<uint8_t> incoming;   // bytes the "host" sent
    std::vector<uint8_t> outgoing;  // bytes the device wrote

    int available() { return (int)incoming.size(); }
    int read() {
        if (incoming.empty()) return -1;
        uint8_t b = incoming.front();
        incoming.pop_front();
        return b;
    }
    size_t write(uint8_t b) {
        outgoing.push_back(b);
        return 1;
    }
};

#endif // ARDUINO_STUB_H
