/**
 * ESP32-C3 OLED Sink - UDP Data Receiver
 *
 * Receives binary pixel data packets over UDP.
 * Supports chunked frames: large frames are split into multiple
 * MTU-safe packets by the sender.  The reserved byte in the packet
 * header carries the chunk_index (0, 1, 2, ...).  Each chunk contains
 * up to MAX_CHUNK_PIXELS pixels starting at chunk_index * MAX_CHUNK_PIXELS.
 *
 * Packet format (from libltp/protocol.py DataPacket):
 *   Header (8 bytes):
 *     - magic: 2 bytes (0x4C54 = "LT")
 *     - ver_flags: 1 byte (version << 4 | flags)
 *     - chunk_index: 1 byte (pixel offset = chunk_index * MAX_CHUNK_PIXELS)
 *     - sequence: 4 bytes (big-endian)
 *   Frame header (4 bytes):
 *     - color_format: 1 byte (0x01 = RGB, 0x04 = GRAYSCALE, 0x05 = MONO_PACKED)
 *     - encoding: 1 byte (0x00 = RAW)
 *     - pixel_count: 2 bytes (big-endian)
 *   Pixel data:
 *     - RGB: 3 * pixel_count bytes
 *     - GRAYSCALE: 1 * pixel_count bytes
 *     - MONO_PACKED: ceil(pixel_count / 8) bytes (1 bit per pixel, MSB-first)
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

// Must match MAX_CHUNK_PIXELS in Python libltp/types.py
#define MAX_CHUNK_PIXELS    480

// Color format values
#define COLOR_FMT_RGB       0x01
#define COLOR_FMT_RGBW      0x02
// COLOR_FMT_GRAYSCALE (0x04) defined in config.h

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
        , totalPixelsThisFrame(0)
        , lastColorFormat(COLOR_FMT_RGB)
    {}

    bool begin(uint16_t listenPort = 0) {
        if (listenPort == 0) {
            listenPort = 5001;
        }

        if (udp.begin(listenPort)) {
            port = listenPort;
            running = true;
            dualOut.printf("UDP receiver started on port %d\r\n", port);
            return true;
        }

        dualOut.println("UDP receiver failed to start");
        return false;
    }

    void stop() {
        if (running) {
            udp.stop();
            running = false;
            dualOut.println("UDP receiver stopped");
        }
    }

    uint16_t getPort() const { return port; }
    bool isRunning() const { return running; }

    // Restrict accepted datagrams to one source IP (empty = accept any).
    void bindSource(const String& ip) { boundSource = ip; }

    // Process incoming packets, read pixels into caller's buffer.
    // Handles chunked frames: each chunk is placed at
    // chunk_index * MAX_CHUNK_PIXELS * 3 in the buffer.
    // Returns total pixels received across all chunks for this frame
    // when a new sequence starts, or 0 if frame is still accumulating.
    // For single-packet frames (chunk_index=0), returns immediately.
    uint16_t receive(uint8_t* pixelBuffer, uint16_t maxPixels) {
        if (!running) return 0;

        uint16_t result = 0;

        // Drain all available packets (chunks arrive in quick succession)
        while (true) {
            int packetSize = udp.parsePacket();
            if (packetSize == 0) break;

            uint16_t got = receiveOne(pixelBuffer, maxPixels);
            if (got > 0) {
                result = got;
            }
        }

        return result;
    }

    // Get the color format from the last received frame
    uint8_t getLastColorFormat() const { return lastColorFormat; }

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
    uint16_t totalPixelsThisFrame;
    uint8_t lastColorFormat;
    String boundSource;   // Layer 2: accept pixels only from this IP

    // Drain any remaining bytes from current UDP packet
    void flushPacket() {
        while (udp.available()) udp.read();
    }

    // Process a single UDP packet (one chunk).
    // Returns total pixels in frame when frame is complete, 0 otherwise.
    uint16_t receiveOne(uint8_t* pixelBuffer, uint16_t maxPixels) {
        // Layer 2 data-plane binding (proposal §2.6): once claimed, only the
        // owner's source IP may feed pixels. Drop everything else.
        if (boundSource.length() > 0 &&
            udp.remoteIP().toString() != boundSource) {
            flushPacket();
            return 0;
        }

        // Read header (12 bytes: 8 packet + 4 frame)
        uint8_t header[PACKET_HEADER_SIZE + FRAME_HEADER_SIZE];
        int headerLen = udp.read(header, sizeof(header));

        if (headerLen < (int)sizeof(header)) {
            flushPacket();
            return 0;
        }

        // Parse packet header (big-endian)
        uint16_t magic = (header[0] << 8) | header[1];
        if (magic != PACKET_MAGIC) {
            flushPacket();
            return 0;
        }

        uint8_t chunkIndex = header[3];
        uint32_t sequence = ((uint32_t)header[4] << 24) |
                           ((uint32_t)header[5] << 16) |
                           ((uint32_t)header[6] << 8) |
                           header[7];

        // Track sequence for drop detection
        if (lastSequence > 0 && sequence > lastSequence + 1) {
            packetsDropped += sequence - lastSequence - 1;
        } else if (lastSequence > 0 && sequence < lastSequence) {
            // Sequence went backward — new stream, reset stats
            packetsDropped = 0;
            packetsReceived = 0;
        }

        // New sequence = new frame, reset accumulator
        if (sequence != lastSequence) {
            totalPixelsThisFrame = 0;
        }
        lastSequence = sequence;

        // Parse frame header
        uint8_t colorFormat = header[PACKET_HEADER_SIZE];
        uint8_t encoding = header[PACKET_HEADER_SIZE + 1];
        uint16_t pixelCount = (header[PACKET_HEADER_SIZE + 2] << 8) |
                              header[PACKET_HEADER_SIZE + 3];

        // Determine data size based on color format
        bool isMonoPacked = (colorFormat == COLOR_FMT_MONO_PACKED);
        uint8_t bytesPerPixel;
        if (colorFormat == COLOR_FMT_RGB) {
            bytesPerPixel = 3;
        } else if (colorFormat == COLOR_FMT_GRAYSCALE) {
            bytesPerPixel = 1;
        } else if (isMonoPacked) {
            bytesPerPixel = 0;  // Sub-byte format, handled specially below
        } else {
            flushPacket();
            return 0;
        }

        if (encoding != ENCODING_RAW) {
            flushPacket();
            return 0;
        }

        lastColorFormat = colorFormat;

        // Calculate pixel offset from chunk index
        uint16_t pixelOffset = (uint16_t)chunkIndex * MAX_CHUNK_PIXELS;
        uint16_t pixelsToRead = pixelCount;

        // Clamp to buffer bounds
        if (pixelOffset >= maxPixels) {
            flushPacket();
            return 0;
        }
        if (pixelOffset + pixelsToRead > maxPixels) {
            pixelsToRead = maxPixels - pixelOffset;
        }

        // Read pixel data directly into caller's buffer at the right offset
        uint16_t bytesToRead;
        uint16_t bufferOffset;
        if (isMonoPacked) {
            // MONO_PACKED: ceil(pixelsToRead / 8) bytes, always single chunk
            bytesToRead = (pixelsToRead + 7) / 8;
            bufferOffset = (pixelOffset + 7) / 8;  // Always 0 for OLED single-packet frames
        } else {
            bytesToRead = pixelsToRead * bytesPerPixel;
            bufferOffset = pixelOffset * bytesPerPixel;
        }
        int bytesRead = udp.read(pixelBuffer + bufferOffset, bytesToRead);

        flushPacket();

        if (bytesRead < (int)bytesToRead) {
            return 0;
        }

        totalPixelsThisFrame += pixelsToRead;
        packetsReceived++;

        // For single-packet frames (chunk_index=0 and all pixels fit),
        // return immediately.  For chunked frames, return total when we
        // have enough pixels to fill the display.
        if (chunkIndex == 0 && pixelCount >= maxPixels) {
            return pixelsToRead;
        }

        // Return total accumulated pixels if we think the frame is complete
        // (we've received enough to fill the display, or this is the last
        // chunk — detectable because pixelCount < MAX_CHUNK_PIXELS)
        if (totalPixelsThisFrame >= maxPixels || pixelCount < MAX_CHUNK_PIXELS) {
            uint16_t total = totalPixelsThisFrame;
            totalPixelsThisFrame = 0;
            return total;
        }

        return 0;
    }
};

#endif // UDP_RECEIVER_H
