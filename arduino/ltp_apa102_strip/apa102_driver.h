/**
 * Simple APA102 LED Driver
 *
 * Software SPI implementation - no FastLED dependency.
 * APA102 protocol:
 *   - Start frame: 32 bits of 0
 *   - LED frame: 111 + 5-bit global brightness + BGR (8 bits each)
 *   - End frame: (numLeds/2 + 1) bits of 1
 */

#ifndef APA102_DRIVER_H
#define APA102_DRIVER_H

#include <Arduino.h>

class APA102Driver {
public:
    APA102Driver(uint16_t numLeds, uint8_t dataPin, uint8_t clockPin)
        : _numLeds(numLeds), _dataPin(dataPin), _clockPin(clockPin),
          _brightness(31), _pixels(nullptr) {
    }

    ~APA102Driver() {
        if (_pixels) delete[] _pixels;
    }

    bool begin() {
        pinMode(_dataPin, OUTPUT);
        pinMode(_clockPin, OUTPUT);
        digitalWrite(_dataPin, LOW);
        digitalWrite(_clockPin, LOW);

        _pixels = new uint8_t[_numLeds * 3];
        if (!_pixels) return false;

        memset(_pixels, 0, _numLeds * 3);
        return true;
    }

    void setBrightness(uint8_t brightness) {
        // APA102 has 5-bit brightness (0-31)
        _brightness = (brightness * 31) / 255;
        if (_brightness == 0 && brightness > 0) _brightness = 1;
    }

    void setPixel(uint16_t idx, uint8_t r, uint8_t g, uint8_t b) {
        if (idx >= _numLeds || !_pixels) return;
        uint16_t offset = idx * 3;
        _pixels[offset + 0] = r;
        _pixels[offset + 1] = g;
        _pixels[offset + 2] = b;
    }

    void getPixel(uint16_t idx, uint8_t& r, uint8_t& g, uint8_t& b) {
        if (idx >= _numLeds || !_pixels) {
            r = g = b = 0;
            return;
        }
        uint16_t offset = idx * 3;
        r = _pixels[offset + 0];
        g = _pixels[offset + 1];
        b = _pixels[offset + 2];
    }

    uint32_t getPixelColor(uint16_t idx) {
        if (idx >= _numLeds || !_pixels) return 0;
        uint16_t offset = idx * 3;
        return ((uint32_t)_pixels[offset] << 16) |
               ((uint32_t)_pixels[offset + 1] << 8) |
               _pixels[offset + 2];
    }

    void clear() {
        if (_pixels) {
            memset(_pixels, 0, _numLeds * 3);
        }
    }

    void fill(uint8_t r, uint8_t g, uint8_t b) {
        for (uint16_t i = 0; i < _numLeds; i++) {
            setPixel(i, r, g, b);
        }
    }

    void show() {
        if (!_pixels) return;

        // Start frame - 32 bits of 0
        for (uint8_t i = 0; i < 4; i++) {
            sendByte(0x00);
        }

        // LED frames
        uint8_t brightnessByte = 0xE0 | _brightness;  // 111 + 5-bit brightness
        for (uint16_t i = 0; i < _numLeds; i++) {
            uint16_t offset = i * 3;
            sendByte(brightnessByte);
            sendByte(_pixels[offset + 2]);  // Blue
            sendByte(_pixels[offset + 1]);  // Green
            sendByte(_pixels[offset + 0]);  // Red
        }

        // End frame - at least (numLeds/2) bits of 1
        // Send full bytes of 0xFF
        uint16_t endBytes = (_numLeds + 15) / 16 + 1;
        for (uint16_t i = 0; i < endBytes; i++) {
            sendByte(0xFF);
        }
    }

    uint16_t numPixels() const { return _numLeds; }

private:
    void sendByte(uint8_t b) {
        for (uint8_t bit = 0; bit < 8; bit++) {
            // MSB first
            digitalWrite(_dataPin, (b & 0x80) ? HIGH : LOW);
            digitalWrite(_clockPin, HIGH);
            b <<= 1;
            digitalWrite(_clockPin, LOW);
        }
    }

    uint16_t _numLeds;
    uint8_t _dataPin;
    uint8_t _clockPin;
    uint8_t _brightness;
    uint8_t* _pixels;
};

#endif // APA102_DRIVER_H
