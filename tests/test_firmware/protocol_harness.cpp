// Host-side harness for the LTP serial packet parser
// (arduino/libraries/LtpProtocol), built against arduino_stub/Arduino.h.
//
// Reads bytes from stdin, feeds them to the parser and prints one line per
// packet it returns: "cmd=<hex> length=<n> oversize=<0|1> payload=<hex of the
// stored part>".
//
// Usage: protocol_harness <maxPayload> <skipOversize 0|1>

#include <cstdio>
#include <cstdlib>

#include "ltp_protocol.h"

uint32_t stubMillis = 0;

int main(int argc, char** argv) {
    if (argc != 3) return 2;
    uint16_t maxPayload = (uint16_t)std::atoi(argv[1]);
    std::vector<uint8_t> buffer(maxPayload ? maxPayload : 1);
    Stream stream;
    LtpProtocol protocol(stream, buffer.data(), maxPayload);
    protocol.setSkipOversize(std::atoi(argv[2]) != 0);

    int c;
    while ((c = std::getchar()) != EOF) stream.incoming.push_back((uint8_t)c);

    while (stream.available()) {
        if (!protocol.processInput()) continue;
        const LtpPacket& p = protocol.getPacket();
        std::printf("cmd=%02x length=%u oversize=%d payload=", p.cmd, (unsigned)p.length,
                    protocol.isOversize() ? 1 : 0);
        if (!protocol.isOversize()) {
            for (uint16_t i = 0; i < p.length; i++) std::printf("%02x", p.payload[i]);
        }
        std::printf("\n");
    }
    return 0;
}
