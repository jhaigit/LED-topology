/**
 * ESP32-C3 OLED Sink - Display Driver
 *
 * Wraps u8g2 library for the 72x40 SSD1306 OLED.
 * Provides RGB-to-monochrome conversion for pixel streaming.
 */

#ifndef OLED_DRIVER_H
#define OLED_DRIVER_H

#include <Arduino.h>
#include <U8g2lib.h>
#include <Wire.h>
#include "config.h"

class OledDriver {
public:
    OledDriver()
        : display(U8G2_R0, U8X8_PIN_NONE, OLED_SCL, OLED_SDA)
    {}

    void begin() {
        display.begin();
        display.setContrast(255);
        display.clearBuffer();
        display.sendBuffer();
    }

    void clear() {
        display.clearBuffer();
        display.sendBuffer();
    }

    void clearBuffer() {
        display.clearBuffer();
    }

    void setContrast(uint8_t contrast) {
        display.setContrast(contrast);
    }

    // Convert RGB pixel buffer to monochrome and render to OLED
    // rgbBuffer: array of RGB triplets (3 bytes per pixel)
    // count: number of pixels
    void drawPixels(const uint8_t* rgbBuffer, uint16_t count) {
        display.clearBuffer();

        uint16_t maxPixels = (count < OLED_TOTAL_PIXELS) ? count : OLED_TOTAL_PIXELS;
        for (uint16_t i = 0; i < maxPixels; i++) {
            uint8_t r = rgbBuffer[i * 3];
            uint8_t g = rgbBuffer[i * 3 + 1];
            uint8_t b = rgbBuffer[i * 3 + 2];

            // Fast integer luminance: (r*77 + g*150 + b*29) >> 8
            uint8_t luma = (uint16_t(r) * 77 + uint16_t(g) * 150 + uint16_t(b) * 29) >> 8;

            uint8_t x = i % OLED_WIDTH;
            uint8_t y = i / OLED_WIDTH;

            if (luma >= 128) {
                display.setDrawColor(1);
            } else {
                display.setDrawColor(0);
            }
            display.drawPixel(x, y);
        }

        display.sendBuffer();
    }

    // Convert grayscale pixel buffer to monochrome and render to OLED
    // grayBuffer: array of single bytes (1 byte per pixel, 0-255 luminance)
    // count: number of pixels
    void drawGrayscalePixels(const uint8_t* grayBuffer, uint16_t count) {
        display.clearBuffer();

        uint16_t maxPixels = (count < OLED_TOTAL_PIXELS) ? count : OLED_TOTAL_PIXELS;
        for (uint16_t i = 0; i < maxPixels; i++) {
            uint8_t x = i % OLED_WIDTH;
            uint8_t y = i / OLED_WIDTH;

            if (grayBuffer[i] >= 128) {
                display.setDrawColor(1);
            } else {
                display.setDrawColor(0);
            }
            display.drawPixel(x, y);
        }

        display.sendBuffer();
    }

    // Text drawing helpers for local modes
    void setFont(const uint8_t* font) {
        display.setFont(font);
    }

    void drawText(int16_t x, int16_t y, const char* text) {
        display.setDrawColor(1);
        display.drawStr(x, y, text);
    }

    // Draw a string using printf-style formatting
    void drawTextF(int16_t x, int16_t y, const char* fmt, ...) {
        char buf[64];
        va_list args;
        va_start(args, fmt);
        vsnprintf(buf, sizeof(buf), fmt, args);
        va_end(args);
        display.setDrawColor(1);
        display.drawStr(x, y, buf);
    }

    void drawBox(int16_t x, int16_t y, int16_t w, int16_t h) {
        display.setDrawColor(1);
        display.drawBox(x, y, w, h);
    }

    void drawFrame(int16_t x, int16_t y, int16_t w, int16_t h) {
        display.setDrawColor(1);
        display.drawFrame(x, y, w, h);
    }

    void drawLine(int16_t x0, int16_t y0, int16_t x1, int16_t y1) {
        display.setDrawColor(1);
        display.drawLine(x0, y0, x1, y1);
    }

    void setDrawColor(uint8_t color) {
        display.setDrawColor(color);
    }

    void drawPixel(int16_t x, int16_t y) {
        display.drawPixel(x, y);
    }

    void sendBuffer() {
        display.sendBuffer();
    }

    uint16_t getWidth() const { return OLED_WIDTH; }
    uint16_t getHeight() const { return OLED_HEIGHT; }

    // Access the underlying u8g2 for advanced drawing
    U8G2_SSD1306_72X40_ER_F_HW_I2C& getDisplay() { return display; }

private:
    U8G2_SSD1306_72X40_ER_F_HW_I2C display;
};

#endif // OLED_DRIVER_H
