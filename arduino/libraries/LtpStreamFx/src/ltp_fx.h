/**
 * LtpStreamFx - LED patterns with no frame buffer
 *
 * Every pixel is calculated from its position, the frame time and a few
 * parameters, in the order the LEDs are wired, and handed straight to the
 * output driver. Nothing is stored per LED, so RAM use does not grow with the
 * pixel count. See docs/led-patterns-without-per-led-memory.md.
 *
 * Use, once per frame:
 *     fxBeginFrame(params, fxTime);    // frame-wide constants
 *     fxStart();                       // rewind to the first wired LED
 *     for each LED: fxNext(rgb);       // RGB of the next wired LED
 *
 * fxAt() gives the same frame by logical index, for pixel read-back.
 *
 * The engine keeps one global state: it drives one output at a time. It uses
 * integer maths only, so fxNext() may be called from an interrupt handler.
 */

#ifndef LTP_FX_H
#define LTP_FX_H

#include <stdint.h>

// The extended patterns (13 and up, ltp_fx_extended.h) are for processors
// with thousands of cycles to spend on each pixel. They are built for the
// Teensy 4.x. To override, define FX_EXTENDED as 0 or 1 on the compiler
// command line: a #define in a sketch does not reach this library.
#ifndef FX_EXTENDED
#if defined(__IMXRT1062__)
#define FX_EXTENDED 1
#else
#define FX_EXTENDED 0
#endif
#endif

// Effects. 0-5 keep the local-mode numbers of ltp_328p_dual, so a controller
// scene that sets local_mode means the same on both firmwares.
enum FxEffect : uint8_t {
    FX_OFF      = 0,
    FX_SCANNER  = 1,   // "Cylon": one eye sweeping end to end
    FX_RAINBOW  = 2,   // palette scrolling along the strip
    FX_FIRE     = 3,
    FX_SPARKLE  = 4,   // random flashes that fade out
    FX_CHASE    = 5,   // moving dashes
    FX_SOLID    = 6,
    FX_COMET    = 7,   // heads with fading tails, wrapping around
    FX_WAVES    = 8,   // two interfering sine waves
    FX_TWINKLE  = 9,   // random pixels fading in and out
    FX_NOISE    = 10,  // smooth noise field: lava, clouds
    FX_PLASMA   = 11,
    FX_RIPPLE   = 12,  // rings from the centre
    FX_COUNT_BASE = 13,

    // Extended effects: numbered on every board, present where FX_EXTENDED is set.
    FX_FIREWORKS = 13,  // shells that rise and burst into sparks
    FX_BOUNCE    = 14,  // balls bouncing under gravity
    FX_METEORS   = 15,  // streaks with tails that crumble away
    FX_LAVA_LAMP = 16,  // blobs that drift and merge
    FX_AURORA    = 17,  // swaying curtains of light
    FX_RAINDROPS = 18,  // rings spreading from random drops, interfering
    FX_COUNT_EXTENDED = 19,

    FX_COUNT    = FX_EXTENDED ? FX_COUNT_EXTENDED : FX_COUNT_BASE,
    FX_CYCLE    = 255  // not an effect: the caller steps through 1..FX_COUNT-1
};

enum FxPalette : uint8_t {
    FXPAL_DEFAULT = 0,  // each effect's own choice, see fxDefaultPalette()
    FXPAL_COLOR   = 1,  // one colour, from hue and saturation
    FXPAL_RAINBOW = 2,  // first table palette; order matches tools/gen_tables.py
    FXPAL_HEAT    = 3,
    FXPAL_OCEAN   = 4,
    FXPAL_FOREST  = 5,
    FXPAL_LAVA    = 6,
    FXPAL_PARTY   = 7,
    FXPAL_ICE     = 8,
    FXPAL_COUNT_BASE = 9,
    FXPAL_AURORA  = 9,  // with FX_EXTENDED only
    FXPAL_COUNT   = FX_EXTENDED ? 10 : FXPAL_COUNT_BASE
};

// Matrix wiring, same bits as SUBMATRIX_* in ltp_protocol.h.
#define FX_LAYOUT_SERPENTINE     0x01  // every other line runs backwards
#define FX_LAYOUT_VERTICAL_FIRST 0x02  // wired along columns, not rows
#define FX_LAYOUT_ORIGIN_BOTTOM  0x04  // first LED is in the bottom row
#define FX_LAYOUT_MASK           0x07

// fxTime units per millisecond at the nominal speed setting.
#define FX_SPEED_NOMINAL        128
#define FX_TIME_PER_MS_NOMINAL  256

struct FxParams {
    uint8_t effect;      // FxEffect, not FX_CYCLE
    uint8_t brightness;  // 0-255, linear, applied to every pixel
    uint8_t scale;       // spatial frequency: larger means finer detail
    uint8_t palette;     // FxPalette
    uint8_t hue;         // colour for FXPAL_COLOR; palette position for one-colour effects
    uint8_t saturation;  // for FXPAL_COLOR
    uint8_t intensity;   // tail length, density, dash width or flame height
    uint8_t count;       // number of comets, balls, shells, meteors, blobs or drops
    uint8_t reverse;     // 0/1: direction of travel; flips the fire base
};

// numPixels LEDs; width > 1 that divides numPixels makes a matrix of
// width x (numPixels / width), wired as `layout` says. Anything else is a strip.
void fxSetGeometry(uint16_t numPixels, uint16_t width, uint8_t layout);
uint16_t fxWidth();
uint16_t fxHeight();

// fxTime units to add per millisecond for a speed setting. Quadratic, so the
// 8-bit control spans frozen (below 8) to 4x nominal (255).
uint16_t fxRate(uint8_t speed);

void fxBeginFrame(const FxParams& params, uint32_t fxTime);
void fxStart();
void fxNext(uint8_t rgb[3]);
void fxAt(uint16_t logicalIndex, uint8_t rgb[3]);

// The palette an effect uses when the palette setting is FXPAL_DEFAULT.
uint8_t fxDefaultPalette(uint8_t effect);

#endif // LTP_FX_H
