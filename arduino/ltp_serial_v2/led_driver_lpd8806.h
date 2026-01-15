/**
 * LTP Serial Protocol v2 - LPD8806 LED Driver
 *
 * Driver for LPD8806 LED strips (SPI-based, 7-bit color per channel).
 *
 * LPD8806 characteristics:
 * - Uses SPI clock + data (hardware or software SPI)
 * - 7-bit color depth per channel (0-127), MSB always set (0x80 | value)
 * - Native color order is GRB
 * - Requires latch bytes (zeros) at end of data
 *
 * To use a different LED chip, create a new driver class inheriting from LedDriver.
 */

#ifndef LTP_LED_DRIVER_LPD8806_H
#define LTP_LED_DRIVER_LPD8806_H

#include "led_driver.h"
#include <SPI.h>

class LedDriverLPD8806 : public LedDriver {
public:
    /**
     * Constructor for hardware SPI
     * @param numPixels Number of LEDs in the strip
     * @param dataPin MOSI pin (usually 11 on Uno, 51 on Mega)
     * @param clockPin SCK pin (usually 13 on Uno, 52 on Mega)
     * @param useHardwareSPI Use hardware SPI (faster) or bit-bang
     */
    LedDriverLPD8806(uint16_t numPixels, uint8_t dataPin = 11, uint8_t clockPin = 13, bool useHardwareSPI = true)
        : LedDriver(numPixels, COLOR_GRB)
        , dataPin(dataPin)
        , clockPin(clockPin)
        , useHardwareSPI(useHardwareSPI)
        , pixelBuffer(nullptr)
    {
        // Allocate pixel buffer (3 bytes per pixel for GRB)
        pixelBuffer = new uint8_t[numPixels * 3];
        if (pixelBuffer) {
            memset(pixelBuffer, 0x80, numPixels * 3); // LPD8806 off = 0x80
        }
    }

    ~LedDriverLPD8806() {
        if (pixelBuffer) {
            delete[] pixelBuffer;
        }
    }

    void begin() override {
        if (useHardwareSPI) {
            SPI.begin();
            SPI.setBitOrder(MSBFIRST);
            SPI.setDataMode(SPI_MODE0);
            SPI.setClockDivider(SPI_CLOCK_DIV8); // 2 MHz on 16 MHz Arduino
        } else {
            pinMode(dataPin, OUTPUT);
            pinMode(clockPin, OUTPUT);
            digitalWrite(dataPin, LOW);
            digitalWrite(clockPin, LOW);
        }

        // Send latch to initialize
        writeLatch();
    }

    void show() override {
        if (!pixelBuffer) return;

        if (useHardwareSPI) {
            // Hardware SPI transfer
            for (uint16_t i = 0; i < numPixels * 3; i++) {
                SPI.transfer(pixelBuffer[i]);
            }
        } else {
            // Software SPI (bit-bang)
            for (uint16_t i = 0; i < numPixels * 3; i++) {
                writeByte(pixelBuffer[i]);
            }
        }

        writeLatch();
    }

    uint8_t* getPixelBuffer() override {
        return pixelBuffer;
    }

    void setPixel(uint16_t index, uint8_t r, uint8_t g, uint8_t b) override {
        if (index >= numPixels || !pixelBuffer) return;

        // Apply brightness and convert to 7-bit with high bit set
        // LPD8806 native order is GRB
        uint16_t offset = index * 3;
        pixelBuffer[offset + 0] = 0x80 | (scale8(g) >> 1); // G
        pixelBuffer[offset + 1] = 0x80 | (scale8(r) >> 1); // R
        pixelBuffer[offset + 2] = 0x80 | (scale8(b) >> 1); // B
    }

    void clear() override {
        if (!pixelBuffer) return;
        // LPD8806 "off" is 0x80 (high bit set, value 0)
        memset(pixelBuffer, 0x80, numPixels * 3);
    }

    uint8_t getLedType() const override {
        return LED_TYPE_LPD8806;
    }

private:
    uint8_t dataPin;
    uint8_t clockPin;
    bool useHardwareSPI;
    uint8_t* pixelBuffer;

    void writeByte(uint8_t b) {
        // Bit-bang SPI, MSB first
        for (uint8_t bit = 0x80; bit; bit >>= 1) {
            digitalWrite(dataPin, (b & bit) ? HIGH : LOW);
            digitalWrite(clockPin, HIGH);
            digitalWrite(clockPin, LOW);
        }
    }

    void writeLatch() {
        // LPD8806 needs (numPixels + 31) / 32 zero bytes as latch
        uint8_t latchBytes = (numPixels + 31) / 32;
        if (useHardwareSPI) {
            for (uint8_t i = 0; i < latchBytes; i++) {
                SPI.transfer(0);
            }
        } else {
            for (uint8_t i = 0; i < latchBytes; i++) {
                writeByte(0);
            }
        }
    }
};

#endif // LTP_LED_DRIVER_LPD8806_H
