/**
 * LTP Serial Protocol v2 - APA102/DotStar LED Driver
 *
 * Driver for APA102 (DotStar) LED strips.
 *
 * APA102 characteristics:
 * - SPI-based (clock + data)
 * - 8-bit color per channel + 5-bit global brightness per LED
 * - Native color order is BGR
 * - Start frame: 32 zero bits
 * - End frame: (numPixels / 2) bits of ones
 *
 * Pin Configuration:
 *   - Data: Pin 11 (MOSI) on Uno, Pin 51 on Mega
 *   - Clock: Pin 13 (SCK) on Uno, Pin 52 on Mega
 */

#ifndef LTP_LED_DRIVER_APA102_H
#define LTP_LED_DRIVER_APA102_H

#include "led_driver.h"
#include <SPI.h>

class LedDriverAPA102 : public LedDriver {
public:
    /**
     * Constructor
     * @param numPixels Number of LEDs in the strip
     * @param dataPin MOSI pin
     * @param clockPin SCK pin
     * @param useHardwareSPI Use hardware SPI (faster) or bit-bang
     */
    LedDriverAPA102(uint16_t numPixels, uint8_t dataPin = 11, uint8_t clockPin = 13, bool useHardwareSPI = true)
        : LedDriver(numPixels, COLOR_RGB)  // We'll handle BGR conversion internally
        , dataPin(dataPin)
        , clockPin(clockPin)
        , useHardwareSPI(useHardwareSPI)
        , pixelBuffer(nullptr)
    {
        // 4 bytes per pixel: brightness + B + G + R
        pixelBuffer = new uint8_t[numPixels * 4];
        if (pixelBuffer) {
            // Initialize with brightness byte set (0xE0 = max brightness prefix)
            for (uint16_t i = 0; i < numPixels; i++) {
                pixelBuffer[i * 4] = 0xE0 | 31; // Global brightness = 31 (max)
                pixelBuffer[i * 4 + 1] = 0;     // B
                pixelBuffer[i * 4 + 2] = 0;     // G
                pixelBuffer[i * 4 + 3] = 0;     // R
            }
        }
    }

    ~LedDriverAPA102() {
        if (pixelBuffer) {
            delete[] pixelBuffer;
        }
    }

    void begin() override {
        if (useHardwareSPI) {
            SPI.begin();
            SPI.setBitOrder(MSBFIRST);
            SPI.setDataMode(SPI_MODE0);
            SPI.setClockDivider(SPI_CLOCK_DIV4); // 4 MHz on 16 MHz Arduino
        } else {
            pinMode(dataPin, OUTPUT);
            pinMode(clockPin, OUTPUT);
            digitalWrite(dataPin, LOW);
            digitalWrite(clockPin, LOW);
        }
    }

    void show() override {
        if (!pixelBuffer) return;

        // Start frame: 32 bits of zeros
        for (uint8_t i = 0; i < 4; i++) {
            writeByte(0x00);
        }

        // LED data: brightness byte + BGR
        for (uint16_t i = 0; i < numPixels; i++) {
            uint16_t offset = i * 4;
            // Apply global brightness scaling to the per-LED brightness
            uint8_t ledBrightness = ((uint16_t)(pixelBuffer[offset] & 0x1F) * (brightness + 1)) >> 8;
            writeByte(0xE0 | ledBrightness);
            writeByte(pixelBuffer[offset + 1]); // B
            writeByte(pixelBuffer[offset + 2]); // G
            writeByte(pixelBuffer[offset + 3]); // R
        }

        // End frame: at least (numPixels / 2) bits of ones
        // We send (numPixels / 16) + 1 bytes of 0xFF
        uint8_t endBytes = (numPixels / 16) + 1;
        for (uint8_t i = 0; i < endBytes; i++) {
            writeByte(0xFF);
        }
    }

    uint8_t* getPixelBuffer() override {
        return pixelBuffer;
    }

    void setPixel(uint16_t index, uint8_t r, uint8_t g, uint8_t b) override {
        if (index >= numPixels || !pixelBuffer) return;

        uint16_t offset = index * 4;
        // Keep existing brightness byte, set BGR
        pixelBuffer[offset + 1] = scale8(b);
        pixelBuffer[offset + 2] = scale8(g);
        pixelBuffer[offset + 3] = scale8(r);
    }

    void clear() override {
        if (!pixelBuffer) return;
        for (uint16_t i = 0; i < numPixels; i++) {
            pixelBuffer[i * 4 + 1] = 0;
            pixelBuffer[i * 4 + 2] = 0;
            pixelBuffer[i * 4 + 3] = 0;
        }
    }

    uint8_t getLedType() const override {
        return LED_TYPE_APA102;
    }

    // Set per-LED brightness (0-31)
    void setPixelBrightness(uint16_t index, uint8_t ledBrightness) {
        if (index < numPixels && pixelBuffer) {
            pixelBuffer[index * 4] = 0xE0 | (ledBrightness & 0x1F);
        }
    }

private:
    uint8_t dataPin;
    uint8_t clockPin;
    bool useHardwareSPI;
    uint8_t* pixelBuffer;

    void writeByte(uint8_t b) {
        if (useHardwareSPI) {
            SPI.transfer(b);
        } else {
            // Bit-bang SPI, MSB first
            for (uint8_t bit = 0x80; bit; bit >>= 1) {
                digitalWrite(dataPin, (b & bit) ? HIGH : LOW);
                digitalWrite(clockPin, HIGH);
                digitalWrite(clockPin, LOW);
            }
        }
    }
};

#endif // LTP_LED_DRIVER_APA102_H
