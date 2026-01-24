/**
 * ESP32 Ring Controller - UDP Data Receiver
 *
 * Receives binary pixel data packets over UDP.
 * Packet format (from libltp/protocol.py DataPacket):
 *   Header (8 bytes):
 *     - magic: 2 bytes (0x4C54 = "LT")
 *     - ver_flags: 1 byte (version << 4 | flags)
 *     - reserved: 1 byte
 *     - sequence: 4 bytes (big-endian)
 *   Frame header (4 bytes):
 *     - color_format: 1 byte (0x01 = RGB)
 *     - encoding: 1 byte (0x00 = RAW)
 *     - pixel_count: 2 bytes (big-endian)
 *   Pixel data:
 *     - RGB bytes (3 * pixel_count)
 */

#ifndef UDP_RECEIVER_H
#define UDP_RECEIVER_H

#include <Arduino.h>
#include <WiFiUdp.h>
#include "config.h"

// Packet constants
#define PACKET_MAGIC        0x4C54
#define PACKET_HEADER_SIZE  8
#define FRAME_HEADER_SIZE   4
#define MAX_UDP_PACKET      1500

// Color format values
#define COLOR_FMT_RGB       0x01
#define COLOR_FMT_RGBW      0x02

// Encoding values
#define ENCODING_RAW        0x00
#define ENCODING_RLE        0x01

class UdpReceiver {
public:
    UdpReceiver()
        : port(0)
        , running(false)
        , lastSequence(0)
        , packetsReceived(0)
        , packetsDropped(0)
    {}

    bool begin(uint16_t listenPort = 0) {
        // Port 0 means let the system assign
        if (listenPort == 0) {
            // Try a specific port first
            listenPort = 5001;
        }

        if (udp.begin(listenPort)) {
            port = listenPort;
            running = true;
            Serial.printf("UDP receiver started on port %d\r\n", port);
            return true;
        }

        Serial.println("UDP receiver failed to start");
        return false;
    }

    void stop() {
        if (running) {
            udp.stop();
            running = false;
            Serial.println("UDP receiver stopped");
        }
    }

    uint16_t getPort() const { return port; }
    bool isRunning() const { return running; }

    // Process incoming packets, copy pixels to buffer
    // Returns number of pixels received, or 0 if no packet
    uint16_t receive(uint8_t* pixelBuffer, uint16_t maxPixels) {
        if (!running) return 0;

        int packetSize = udp.parsePacket();
        if (packetSize == 0) return 0;

        // Read packet into buffer
        uint8_t packet[MAX_UDP_PACKET];
        int len = udp.read(packet, min(packetSize, (int)MAX_UDP_PACKET));

        if (len < PACKET_HEADER_SIZE + FRAME_HEADER_SIZE) {
            Serial.println("UDP: Packet too small");
            return 0;
        }

        // Parse packet header (big-endian)
        uint16_t magic = (packet[0] << 8) | packet[1];
        if (magic != PACKET_MAGIC) {
            Serial.printf("UDP: Invalid magic 0x%04X\r\n", magic);
            return 0;
        }

        uint8_t verFlags = packet[2];
        // uint8_t reserved = packet[3];
        uint32_t sequence = ((uint32_t)packet[4] << 24) |
                           ((uint32_t)packet[5] << 16) |
                           ((uint32_t)packet[6] << 8) |
                           packet[7];

        // Check for dropped packets
        if (lastSequence > 0 && sequence != lastSequence + 1) {
            uint32_t dropped = sequence - lastSequence - 1;
            packetsDropped += dropped;
        }
        lastSequence = sequence;

        // Parse frame header
        uint8_t colorFormat = packet[PACKET_HEADER_SIZE];
        uint8_t encoding = packet[PACKET_HEADER_SIZE + 1];
        uint16_t pixelCount = (packet[PACKET_HEADER_SIZE + 2] << 8) |
                              packet[PACKET_HEADER_SIZE + 3];

        // Only support RGB raw for now
        if (colorFormat != COLOR_FMT_RGB) {
            Serial.printf("UDP: Unsupported color format %d\r\n", colorFormat);
            return 0;
        }

        if (encoding != ENCODING_RAW) {
            Serial.printf("UDP: Unsupported encoding %d\r\n", encoding);
            return 0;
        }

        // Calculate expected data size
        uint8_t bytesPerPixel = 3;  // RGB
        uint16_t expectedDataSize = pixelCount * bytesPerPixel;
        uint16_t dataOffset = PACKET_HEADER_SIZE + FRAME_HEADER_SIZE;

        if (len < dataOffset + expectedDataSize) {
            Serial.printf("UDP: Insufficient data: got %d, need %d\r\n",
                          len - dataOffset, expectedDataSize);
            return 0;
        }

        // Copy pixel data to buffer
        uint16_t pixelsToCopy = min(pixelCount, maxPixels);
        memcpy(pixelBuffer, packet + dataOffset, pixelsToCopy * bytesPerPixel);

        packetsReceived++;
        return pixelsToCopy;
    }

    // Statistics
    uint32_t getPacketsReceived() const { return packetsReceived; }
    uint32_t getPacketsDropped() const { return packetsDropped; }
    uint32_t getLastSequence() const { return lastSequence; }

    void resetStats() {
        packetsReceived = 0;
        packetsDropped = 0;
        lastSequence = 0;
    }

private:
    WiFiUDP udp;
    uint16_t port;
    bool running;
    uint32_t lastSequence;
    uint32_t packetsReceived;
    uint32_t packetsDropped;
};

#endif // UDP_RECEIVER_H
