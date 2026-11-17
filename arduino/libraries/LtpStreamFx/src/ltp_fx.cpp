/**
 * LtpStreamFx - pattern engine implementation. See ltp_fx.h.
 *
 * Rules kept throughout, because the same code runs on a 16 MHz AVR between
 * the bits of a WS2812 frame:
 *   - integer maths only; no divide in any per-pixel path (divides live in
 *     fxBeginFrame, once per frame);
 *   - a pixel depends only on (x, y, logical index) and frame constants, so
 *     fxNext() and fxAt() agree and no pixel needs the previous frame;
 *   - each effect is its own small function taking and returning values in
 *     registers. On AVR a function pays for every call-saved register it
 *     touches, so one big function for all effects would cost every effect
 *     the registers of the greediest.
 */

#include "ltp_fx.h"

#if defined(__AVR__)
#include <avr/pgmspace.h>
#define FX_PROGMEM PROGMEM
#define FX_RD8(addr) pgm_read_byte(addr)
#else
#define FX_PROGMEM
#define FX_RD8(addr) (*(const uint8_t*)(addr))
#endif

#include "ltp_fx_tables.h"

// ============================================================================
// TIMING
// ============================================================================

// Each effect shifts fxTime down to a 16-bit phase. One phase cycle is 65536
// units and fxTime gains 256 units per ms at nominal speed, so a shift of s
// gives a cycle of 256 * 2^s ms.
#define FX_SHIFT_RAINBOW       4   // 4.1 s per palette cycle
#define FX_SHIFT_CHASE         1   // 0.5 s per dash period
#define FX_SHIFT_SCANNER       3   // 2.0 s out and back
#define FX_SHIFT_COMET         4   // 4.1 s per lap
#define FX_SHIFT_SPARKLE       8   // phase high byte ticks every 256 ms: one sparkle
#define FX_SHIFT_TWINKLE       10  // one twinkle per 1.0 s
#define FX_SHIFT_WAVES_A       3   // 2.0 s
#define FX_SHIFT_WAVES_B       4   // 4.1 s, so the two waves drift apart
#define FX_SHIFT_PLASMA_A      3
#define FX_SHIFT_PLASMA_B      4
#define FX_SHIFT_PLASMA_C      5
#define FX_SHIFT_NOISE_EVOLVE  10  // one lattice cell per 1.0 s
#define FX_SHIFT_NOISE_DRIFT   12  // sideways, four times slower
#define FX_SHIFT_FIRE_RISE     7   // 7.8 lattice cells per second upward
#define FX_SHIFT_FIRE_FLICKER  8
#define FX_SHIFT_RIPPLE        3   // 2.0 s per ring
#define FX_SHIFT_COLOUR        14  // slow colour drift of one-colour effects: 16.8 s

// ============================================================================
// SPATIAL SCALE
// ============================================================================

// Phase units per pixel for each unit of the scale setting. With 32, one
// phase cycle spans 2048 / scale pixels: 64 pixels at the default scale of 32.
#define FX_FIELD_STEP_PER_SCALE  32
// Chase dashes are shorter: 512 / scale pixels per period (16 at scale 32).
#define FX_CHASE_STEP_PER_SCALE  128
// Noise coordinates are 8.8 fixed point in lattice cells. scale / 2 per pixel
// makes a cell 512 / scale pixels wide (16 at scale 32).
#define FX_NOISE_SCALE_SHIFT     1
// Fire wants smaller features: a cell is 256 / scale pixels (8 at scale 32).
#define FX_FIRE_MIN_STEP         2

// ============================================================================
// EFFECT SHAPES
// ============================================================================

// Scanner positions are in 1/8 pixel, which keeps a 16-bit position good for
// 8191 pixels and makes slow movement smooth.
#define FX_SCANNER_SUBPIXEL_BITS 3
#define FX_SCANNER_HEAD          12  // 1.5 px glow ahead of the eye
#define FX_SCANNER_TAIL_MIN      8   // tail at full speed: 1 px + intensity / 8 px

// Sparkle lights intensity / 4 of 256 pixels per tick, twinkle intensity / 2.
#define FX_SPARKLE_DENSITY_SHIFT 2
#define FX_TWINKLE_DENSITY_SHIFT 1

// Fire: cooling per unit of height is coolMul / 64. Intensity 255 gives 1.0
// (flames reach the far end), intensity 0 gives 4.0 (flames a quarter high).
#define FX_FIRE_COOL_MAX         255
#define FX_FIRE_COOL_RANGE       191
#define FX_FIRE_COOL_SHIFT       6
#define FX_FIRE_COOL_FULL        (256u << FX_FIRE_COOL_SHIFT)  // product at which cooling is total
// The base glows on its own, so the root of the flame never goes dark.
#define FX_FIRE_BASE_GLOW_SHIFT  2

// Noise is bell-shaped around 128. Stretching 64..192 to 0..255 uses the
// whole palette.
#define FX_NOISE_FLOOR           64
#define FX_NOISE_CEILING         192

// Ripple squares coordinates in 16 bits: |dx|, |dy| must stay under 181.
#define FX_RIPPLE_MAX_COORD      180

// Bytes per table palette: 256 RGB triplets.
#define FX_PALETTE_BYTES         768

// ============================================================================
// STATE
// ============================================================================

namespace {

struct FxState {
    // Geometry
    uint16_t n, w, h;
    uint8_t layout;

    // Frame-wide
    uint8_t effect;
    uint8_t is2d;
    uint8_t colorPalette;      // 1: one colour, 0: table palette
    uint8_t monoField;         // one colour on a field effect: palette index becomes brightness
    uint8_t solid[3];
    const uint8_t* palBase;
    uint8_t brightness;
    uint8_t hue;
    uint8_t reverse;

    // Per-effect frame constants
    union {
        struct { uint16_t step, t; } rainbow;
        struct { uint16_t step, t, gain; uint8_t duty, colour; } chase;
        struct { uint16_t pos, headLim, tailLim, headGain, tailGain; uint8_t movingUp; } scanner;
        struct { uint16_t step, head, gain; uint8_t tail; } comet;
        struct { uint16_t t; uint8_t density, triangle; } twinkle;
        struct { uint16_t stepA, stepB, ta, tb; } waves;
        struct { uint16_t step, stepHalf, ta, tb, tc; } plasma;
        struct { uint16_t step, drift, t; } noise;
        struct { uint16_t step, flicker, rise, invH, top; uint8_t coolMul; } fire;
        struct { uint16_t step, t; int16_t cx2, cy2; uint8_t shift; } ripple;
    } e;

    // Walker: position of the next wired LED. Along a wired line only one of
    // x and y moves, by `step`.
    uint16_t x, y, lineLeft, major;
    int16_t step;
    uint8_t alongY;
};

FxState S;

// ============================================================================
// SMALL MATHS
// ============================================================================

inline uint8_t hi8(uint16_t v) { return (uint8_t)(v >> 8); }
inline uint8_t lo8(uint16_t v) { return (uint8_t)v; }

// Low 16 bits of a product. `unsigned` is 16 bits on AVR (one cheap multiply)
// and 32 bits elsewhere (no signed-overflow trap from integer promotion).
inline uint16_t mulLo(uint16_t a, uint16_t b) {
    return (uint16_t)((unsigned)a * (unsigned)b);
}

// value * (scale + 1) / 256: scale 255 returns value unchanged.
#if defined(__AVR__)
// As value * scale + value, which is one 8 x 8 MUL. Written in assembler
// because the compiler turns the C form into a 16-bit multiply of three MULs,
// and this runs four times per pixel.
inline uint8_t scale8(uint8_t value, uint8_t scale) __attribute__((always_inline));
inline uint8_t scale8(uint8_t value, uint8_t scale) {
    uint8_t result;
    __asm__(
        "mul %[value], %[scale]"  "\n\t"  // r1:r0 = value * scale
        "add r0, %[value]"        "\n\t"  // carry out of the low byte
        "mov %[result], r1"       "\n\t"
        "clr r1"                  "\n\t"  // the compiler's zero register; carry survives
        "adc %[result], r1"       "\n\t"
        : [result] "=&r" (result)
        : [value] "r" (value), [scale] "r" (scale)
        : "r0"
    );
    return result;
}
#else
inline uint8_t scale8(uint8_t value, uint8_t scale) {
    return (uint8_t)(((uint16_t)value * scale + value) >> 8);
}
#endif

inline uint8_t qadd8(uint8_t a, uint8_t b) {
    uint16_t s = (uint16_t)a + b;
    return s > 255 ? 255 : (uint8_t)s;
}

inline uint8_t qsub8(uint8_t a, uint8_t b) {
    return a > b ? (uint8_t)(a - b) : 0;
}

// Triangle wave: 0 at 0 and 255, peak 254 in the middle.
inline uint8_t tri8(uint8_t v) __attribute__((always_inline));
inline uint8_t tri8(uint8_t v) {
    return (v & 0x80) ? (uint8_t)((uint8_t)(255 - v) << 1) : (uint8_t)(v << 1);
}

// Perceptual brightness to linear PWM, gamma 2: v * (v + 1) / 256.
inline uint8_t gamma2(uint8_t v) __attribute__((always_inline));
inline uint8_t gamma2(uint8_t v) {
    return scale8(v, v);
}

inline uint8_t sin8(uint8_t a)  { return FX_RD8(fxSinTable + a); }
inline uint8_t ease8(uint8_t a) { return FX_RD8(fxEaseTable + a); }
inline uint8_t perm8(uint8_t a) { return FX_RD8(fxPermTable + a); }

inline uint8_t lerp8(uint8_t a, uint8_t b, uint8_t frac) __attribute__((always_inline));
inline uint8_t lerp8(uint8_t a, uint8_t b, uint8_t frac) {
    if (b >= a) return (uint8_t)(a + (((uint16_t)(uint8_t)(b - a) * frac) >> 8));
    return (uint8_t)(a - (((uint16_t)(uint8_t)(a - b) * frac) >> 8));
}

// Two-dimensional value noise. a and b are 8.8 fixed-point lattice
// coordinates; the lattice repeats every 256 cells.
uint8_t noise2(uint16_t a, uint16_t b) {
    uint8_t xi = hi8(a), yi = hi8(b);
    uint8_t px0 = perm8(xi);
    uint8_t px1 = perm8((uint8_t)(xi + 1));
    uint8_t yi1 = (uint8_t)(yi + 1);
    uint8_t fx = ease8(lo8(a));
    uint8_t top = lerp8(perm8((uint8_t)(px0 + yi)),  perm8((uint8_t)(px1 + yi)),  fx);
    uint8_t bot = lerp8(perm8((uint8_t)(px0 + yi1)), perm8((uint8_t)(px1 + yi1)), fx);
    return lerp8(top, bot, ease8(lo8(b)));
}

// Integer square root of a 16-bit value, one result bit per pass.
uint8_t isqrt16(uint16_t v) {
    uint8_t root = 0;
    for (uint8_t bit = 0x80; bit; bit >>= 1) {
        uint8_t trial = root | bit;
        if ((uint16_t)trial * trial <= v) root = trial;
    }
    return root;
}

// Offset sine with 16-bit result, interpolated between table entries so that
// a slow scanner moves smoothly on a long strip.
uint16_t sin16(uint16_t phase) {
    uint8_t i = hi8(phase);
    uint8_t a = sin8(i);
    uint8_t b = sin8((uint8_t)(i + 1));
    int16_t diff = (int16_t)b - (int16_t)a;
    return (uint16_t)(((uint16_t)a << 8) + diff * (int16_t)lo8(phase));
}

// Hue and saturation at full value, then gamma-corrected like the tables.
void solidColour(uint8_t hue, uint8_t sat, uint8_t* rgb) {
    const uint8_t sextant = 43;  // 256 / 6
    uint8_t region = hue / sextant;
    uint8_t rem = (uint8_t)((hue - region * sextant) * 6);
    uint8_t p = (uint8_t)(255 - sat);
    uint8_t q = (uint8_t)(255 - (((uint16_t)sat * rem) >> 8));
    uint8_t t = (uint8_t)(255 - (((uint16_t)sat * (uint8_t)(255 - rem)) >> 8));
    uint8_t r, g, b;
    switch (region) {
        case 0:  r = 255; g = t;   b = p;   break;
        case 1:  r = q;   g = 255; b = p;   break;
        case 2:  r = p;   g = 255; b = t;   break;
        case 3:  r = p;   g = q;   b = 255; break;
        case 4:  r = t;   g = p;   b = 255; break;
        default: r = 255; g = p;   b = q;   break;
    }
    rgb[0] = gamma2(r);
    rgb[1] = gamma2(g);
    rgb[2] = gamma2(b);
}

// Field effects set the palette index for every pixel and leave brightness
// full. With the one-colour palette their index is shown as brightness.
bool isFieldEffect(uint8_t effect) {
    switch (effect) {
        case FX_RAINBOW: case FX_FIRE: case FX_WAVES:
        case FX_NOISE: case FX_PLASMA: case FX_RIPPLE:
            return true;
        default:
            return false;
    }
}

// ============================================================================
// PER-PIXEL EVALUATION
// ============================================================================

// What an effect says about one pixel: palette index in the low byte,
// brightness in the high byte. One 16-bit value comes back in registers.
typedef uint16_t FxPixel;
typedef FxPixel (*FxEval)(uint16_t x, uint16_t y);
#define FX_PIXEL(idx, val) ((FxPixel)(((uint16_t)(uint8_t)(val) << 8) | (uint8_t)(idx)))
#define FX_NOINLINE __attribute__((noinline))

// Each effect is a function of the pixel's position (x, y). fxBeginFrame
// picks the function once; fxNext and fxAt call it for every pixel.

// Row-major logical index, for effects in which a pixel has an identity.
inline uint16_t logicalIndex(uint16_t x, uint16_t y) {
    return (uint16_t)(mulLo(y, S.w) + x);
}

FX_NOINLINE FxPixel fxOff(uint16_t, uint16_t) {
    return FX_PIXEL(0, 0);
}

FX_NOINLINE FxPixel fxSolid(uint16_t, uint16_t) {
    return FX_PIXEL(S.hue, 255);
}

FX_NOINLINE FxPixel fxRainbow(uint16_t x, uint16_t y) {
    return FX_PIXEL(hi8(mulLo(x + y, S.e.rainbow.step) - S.e.rainbow.t), 255);
}

FX_NOINLINE FxPixel fxChase(uint16_t x, uint16_t y) {
    // Sawtooth along x + y: diagonal stripes on a matrix.
    uint8_t a = hi8(mulLo(x + y, S.e.chase.step) - S.e.chase.t);
    // Bright at the leading edge, fading to the trailing edge.
    uint8_t val = a < S.e.chase.duty ? hi8(mulLo((uint16_t)a + 1, S.e.chase.gain)) : 0;
    return FX_PIXEL(S.hue + S.e.chase.colour, val);
}

FX_NOINLINE FxPixel fxScanner(uint16_t x, uint16_t) {
    int16_t d = (int16_t)((x << FX_SCANNER_SUBPIXEL_BITS) - S.e.scanner.pos);
    bool behind = (d < 0) == (S.e.scanner.movingUp != 0);
    uint16_t dist = d < 0 ? (uint16_t)(-d) : (uint16_t)d;
    uint16_t lim  = behind ? S.e.scanner.tailLim  : S.e.scanner.headLim;
    uint16_t gain = behind ? S.e.scanner.tailGain : S.e.scanner.headGain;
    return FX_PIXEL(S.hue, dist < lim ? 255 - hi8(mulLo(dist, gain)) : 0);
}

FX_NOINLINE FxPixel fxComet(uint16_t x, uint16_t y) {
    // Fraction of the comet spacing by which this pixel trails a head.
    uint8_t a = hi8(S.e.comet.head - mulLo(logicalIndex(x, y), S.e.comet.step));
    uint8_t val = a < S.e.comet.tail ? (uint8_t)(255 - hi8(mulLo(a, S.e.comet.gain))) : 0;
    return FX_PIXEL(S.hue + (a >> 1), val);
}

// Sparkle and twinkle.
FX_NOINLINE FxPixel fxTwinkle(uint16_t x, uint16_t y) {
    uint16_t n = logicalIndex(x, y);
    // Each pixel runs its own clock: a constant hash offsets the phase, so
    // pixels neither start nor end together.
    uint8_t own = perm8((uint8_t)(perm8(lo8(n)) + hi8(n)));
    uint16_t phase = S.e.twinkle.t + mulLo(own, 257);
    uint8_t tick = hi8(phase), frac = lo8(phase);
    // A fresh draw per pixel per tick decides whether it lights.
    uint8_t draw = perm8((uint8_t)(perm8((uint8_t)(tick + lo8(n))) + own));
    if (draw >= S.e.twinkle.density) return FX_PIXEL(0, 0);
    uint8_t val = S.e.twinkle.triangle ? tri8(frac) : (uint8_t)(255 - frac);
    return FX_PIXEL(S.hue + perm8((uint8_t)(draw + tick)), val);
}

FX_NOINLINE FxPixel fxWaves(uint16_t x, uint16_t y) {
    uint16_t a = mulLo(x, S.e.waves.stepA) - S.e.waves.ta;
    uint16_t b = mulLo(x, S.e.waves.stepB) + mulLo(y, S.e.waves.stepA) + S.e.waves.tb;
    return FX_PIXEL(((uint16_t)sin8(hi8(a)) + sin8(hi8(b))) >> 1, 255);
}

FX_NOINLINE FxPixel fxPlasma(uint16_t x, uint16_t y) {
    uint16_t u = mulLo(x, S.e.plasma.step);
    uint16_t v = mulLo(y, S.e.plasma.step);
    uint16_t sum = sin8(hi8(u + S.e.plasma.ta));
    sum += sin8(hi8(v + mulLo(x, S.e.plasma.stepHalf) - S.e.plasma.tb));
    sum += sin8(hi8(u + v + S.e.plasma.tc));
    return FX_PIXEL(hi8(mulLo(sum, 85)), 255);  // divide by three: 85 / 256
}

FX_NOINLINE FxPixel fxNoise(uint16_t x, uint16_t y) {
    uint8_t nz = noise2(mulLo(x, S.e.noise.step) + S.e.noise.drift,
                        mulLo(y, S.e.noise.step) + S.e.noise.t);
    uint8_t idx;
    if (nz <= FX_NOISE_FLOOR) idx = 0;
    else if (nz >= FX_NOISE_CEILING) idx = 255;
    else idx = (uint8_t)((nz - FX_NOISE_FLOOR) << 1);
    return FX_PIXEL(idx, 255);
}

FX_NOINLINE FxPixel fxFire(uint16_t x, uint16_t y) {
    // height: distance from the base of the flame, 0 .. top.
    uint16_t height;
    uint16_t a;
    if (S.is2d) {
        height = S.reverse ? y : (uint16_t)(S.e.fire.top - y);
        a = mulLo(x, S.e.fire.step) + (S.e.fire.flicker >> 2);
    } else {
        height = S.reverse ? (uint16_t)(S.e.fire.top - x) : x;
        a = S.e.fire.flicker;
    }
    uint8_t frac = hi8(mulLo(height, S.e.fire.invH));  // 0 at base, 255 at top
    // cooling = frac * coolMul / 64, capped at 255. Tested against the cap
    // before shifting, so the shift is two places up, not six down.
    uint16_t product = (uint16_t)frac * S.e.fire.coolMul;
    uint8_t cool = product >= FX_FIRE_COOL_FULL ? 255
        : hi8((uint16_t)(product << (8 - FX_FIRE_COOL_SHIFT)));
    uint8_t nz = noise2(a, mulLo(height, S.e.fire.step) - S.e.fire.rise);
    uint8_t glow = (uint8_t)((uint8_t)(255 - cool) >> FX_FIRE_BASE_GLOW_SHIFT);
    return FX_PIXEL(qadd8(qsub8(nz, cool), glow), 255);
}

FX_NOINLINE FxPixel fxRipple(uint16_t x, uint16_t y) {
    // Distance from the centre in half pixels.
    int16_t dx = (int16_t)(x << 1) - S.e.ripple.cx2;
    uint16_t dist;
    if (S.is2d) {
        int16_t dy = (int16_t)(y << 1) - S.e.ripple.cy2;
        uint8_t ax = (uint8_t)((dx < 0 ? -dx : dx) >> S.e.ripple.shift);
        uint8_t ay = (uint8_t)((dy < 0 ? -dy : dy) >> S.e.ripple.shift);
        dist = (uint16_t)isqrt16((uint16_t)ax * ax + (uint16_t)ay * ay) << S.e.ripple.shift;
    } else {
        dist = dx < 0 ? (uint16_t)(-dx) : (uint16_t)dx;
    }
    return FX_PIXEL(hi8(mulLo(dist, S.e.ripple.step) - S.e.ripple.t), 255);
}

FxEval evalFor(uint8_t effect) {
    switch (effect) {
        case FX_SOLID:   return fxSolid;
        case FX_RAINBOW: return fxRainbow;
        case FX_CHASE:   return fxChase;
        case FX_SCANNER: return fxScanner;
        case FX_COMET:   return fxComet;
        case FX_SPARKLE:
        case FX_TWINKLE: return fxTwinkle;
        case FX_WAVES:   return fxWaves;
        case FX_PLASMA:  return fxPlasma;
        case FX_NOISE:   return fxNoise;
        case FX_FIRE:    return fxFire;
        case FX_RIPPLE:  return fxRipple;
        default:         return fxOff;
    }
}

FxEval evalPixel = fxOff;  // the current frame's effect

// Palette index and brightness to the RGB that goes on the wire.
FX_NOINLINE void colourPixel(FxPixel pixel, uint8_t rgb[3]) {
    uint8_t idx = lo8(pixel), val = hi8(pixel);
    uint8_t r, g, b;
    if (S.colorPalette) {
        r = S.solid[0];
        g = S.solid[1];
        b = S.solid[2];
        if (S.monoField) val = scale8(val, tri8(idx));
    } else {
        const uint8_t* entry = S.palBase + (uint16_t)idx * 3;
        r = FX_RD8(entry);
        g = FX_RD8(entry + 1);
        b = FX_RD8(entry + 2);
    }
    // Palettes are already gamma-corrected; do the same to the brightness,
    // then apply the global brightness linearly.
    uint8_t k = scale8(gamma2(val), S.brightness);
    rgb[0] = scale8(r, k);
    rgb[1] = scale8(g, k);
    rgb[2] = scale8(b, k);
}

// Set the walker to the first LED of wired line S.major.
void lineStart() {
    bool odd = (S.major & 1) != 0;
    bool serp = (S.layout & FX_LAYOUT_SERPENTINE) != 0;
    bool bottom = (S.layout & FX_LAYOUT_ORIGIN_BOTTOM) != 0;
    if (S.layout & FX_LAYOUT_VERTICAL_FIRST) {
        bool flip = (serp && odd) != bottom;
        S.alongY = 1;
        S.x = S.major;
        S.y = flip ? (uint16_t)(S.h - 1) : 0;
        S.step = flip ? -1 : 1;
        S.lineLeft = S.h;
    } else {
        bool flip = serp && odd;
        S.alongY = 0;
        S.y = bottom ? (uint16_t)(S.h - 1 - S.major) : S.major;
        S.x = flip ? (uint16_t)(S.w - 1) : 0;
        S.step = flip ? -1 : 1;
        S.lineLeft = S.w;
    }
}

}  // namespace

// ============================================================================
// PUBLIC API
// ============================================================================

void fxSetGeometry(uint16_t numPixels, uint16_t width, uint8_t layout) {
    if (numPixels == 0) numPixels = 1;
    S.n = numPixels;
    if (width > 1 && width <= numPixels && numPixels % width == 0) {
        S.w = width;
        S.h = numPixels / width;
        S.layout = layout & FX_LAYOUT_MASK;
    } else {
        S.w = numPixels;
        S.h = 1;
        S.layout = 0;
    }
}

uint16_t fxWidth()  { return S.w; }
uint16_t fxHeight() { return S.h; }

uint16_t fxRate(uint8_t speed) {
    // speed^2 / 64: 128 gives FX_TIME_PER_MS_NOMINAL.
    return (uint16_t)(((uint16_t)speed * speed) >> 6);
}

uint8_t fxDefaultPalette(uint8_t effect) {
    switch (effect) {
        case FX_SCANNER:
        case FX_SOLID:   return FXPAL_COLOR;
        case FX_FIRE:    return FXPAL_HEAT;
        case FX_WAVES:
        case FX_RIPPLE:  return FXPAL_OCEAN;
        case FX_NOISE:   return FXPAL_LAVA;
        case FX_TWINKLE: return FXPAL_PARTY;
        default:         return FXPAL_RAINBOW;
    }
}

void fxBeginFrame(const FxParams& p, uint32_t fxTime) {
    if (S.n == 0) fxSetGeometry(1, 1, 0);

    S.effect = p.effect < FX_COUNT ? p.effect : (uint8_t)FX_OFF;
    evalPixel = evalFor(S.effect);
    S.is2d = S.h > 1;
    S.hue = p.hue;
    S.reverse = p.reverse;
    S.brightness = p.brightness;

    uint8_t pal = p.palette;
    if (pal == FXPAL_DEFAULT || pal >= FXPAL_COUNT) pal = fxDefaultPalette(S.effect);
    S.colorPalette = pal == FXPAL_COLOR;
    S.monoField = S.colorPalette && isFieldEffect(S.effect);
    if (S.colorPalette) {
        solidColour(p.hue, p.saturation, S.solid);
        S.palBase = fxPaletteTable;
    } else {
        S.palBase = fxPaletteTable + (uint16_t)(pal - FXPAL_RAINBOW) * FX_PALETTE_BYTES;
    }

    // Phase for an effect: fxTime shifted down, negated when reversed.
    #define FX_PHASE(shift) ((uint16_t)(p.reverse ? (uint16_t)(0 - (uint16_t)(fxTime >> (shift))) \
                                                  : (uint16_t)(fxTime >> (shift))))
    uint8_t scale = p.scale ? p.scale : 1;
    uint16_t fieldStep = (uint16_t)scale * FX_FIELD_STEP_PER_SCALE;

    switch (S.effect) {
        case FX_RAINBOW:
            S.e.rainbow.step = fieldStep;
            S.e.rainbow.t = FX_PHASE(FX_SHIFT_RAINBOW);
            break;

        case FX_CHASE: {
            uint8_t duty = p.intensity ? p.intensity : 1;
            S.e.chase.step = (uint16_t)scale * FX_CHASE_STEP_PER_SCALE;
            S.e.chase.t = FX_PHASE(FX_SHIFT_CHASE);
            S.e.chase.duty = duty;
            S.e.chase.gain = (uint16_t)(0xFF00u / duty);
            S.e.chase.colour = (uint8_t)(fxTime >> FX_SHIFT_COLOUR);
            break;
        }

        case FX_SCANNER: {
            uint16_t phase = (uint16_t)(fxTime >> FX_SHIFT_SCANNER);
            uint16_t span = (uint16_t)((S.w - 1) << FX_SCANNER_SUBPIXEL_BITS);
            S.e.scanner.pos = (uint16_t)(((uint32_t)sin16(phase) * span) >> 16);
            // Velocity is the cosine. The tail grows with speed and shrinks
            // to nothing at each turn, so it never has to jump sides.
            uint8_t cosine = sin8((uint8_t)(hi8(phase) + 64));
            uint8_t speed = cosine >= 128 ? (uint8_t)(cosine - 128) : (uint8_t)(128 - cosine);
            uint16_t tailMax = FX_SCANNER_TAIL_MIN + p.intensity;
            S.e.scanner.movingUp = cosine >= 128;
            S.e.scanner.headLim = FX_SCANNER_HEAD;
            S.e.scanner.tailLim = FX_SCANNER_HEAD + (uint16_t)((tailMax * speed) >> 7);
            S.e.scanner.headGain = (uint16_t)(0xFF00u / S.e.scanner.headLim);
            S.e.scanner.tailGain = (uint16_t)(0xFF00u / S.e.scanner.tailLim);
            break;
        }

        case FX_COMET: {
            uint8_t count = p.count ? p.count : 1;
            uint8_t tail = p.intensity ? p.intensity : 1;
            uint32_t step = ((uint32_t)count << 16) / S.n;
            S.e.comet.step = step > 0xFFFFu ? 0xFFFFu : (uint16_t)step;
            S.e.comet.head = mulLo(FX_PHASE(FX_SHIFT_COMET), count);
            S.e.comet.tail = tail;
            S.e.comet.gain = (uint16_t)(0xFF00u / tail);
            break;
        }

        case FX_SPARKLE:
            S.e.twinkle.t = (uint16_t)(fxTime >> FX_SHIFT_SPARKLE);
            S.e.twinkle.density = p.intensity >> FX_SPARKLE_DENSITY_SHIFT;
            S.e.twinkle.triangle = 0;
            break;

        case FX_TWINKLE:
            S.e.twinkle.t = (uint16_t)(fxTime >> FX_SHIFT_TWINKLE);
            S.e.twinkle.density = p.intensity >> FX_TWINKLE_DENSITY_SHIFT;
            S.e.twinkle.triangle = 1;
            break;

        case FX_WAVES:
            S.e.waves.stepA = fieldStep;
            S.e.waves.stepB = fieldStep + (fieldStep >> 1);
            S.e.waves.ta = FX_PHASE(FX_SHIFT_WAVES_A);
            S.e.waves.tb = FX_PHASE(FX_SHIFT_WAVES_B);
            break;

        case FX_PLASMA:
            S.e.plasma.step = fieldStep;
            S.e.plasma.stepHalf = fieldStep >> 1;
            S.e.plasma.ta = FX_PHASE(FX_SHIFT_PLASMA_A);
            S.e.plasma.tb = FX_PHASE(FX_SHIFT_PLASMA_B);
            S.e.plasma.tc = FX_PHASE(FX_SHIFT_PLASMA_C);
            break;

        case FX_NOISE: {
            uint8_t step = scale >> FX_NOISE_SCALE_SHIFT;
            S.e.noise.step = step ? step : 1;
            S.e.noise.drift = FX_PHASE(FX_SHIFT_NOISE_DRIFT);
            S.e.noise.t = (uint16_t)(fxTime >> FX_SHIFT_NOISE_EVOLVE);
            break;
        }

        case FX_FIRE: {
            uint16_t extent = S.is2d ? S.h : S.w;
            S.e.fire.step = scale < FX_FIRE_MIN_STEP ? FX_FIRE_MIN_STEP : scale;
            S.e.fire.flicker = (uint16_t)(fxTime >> FX_SHIFT_FIRE_FLICKER);
            S.e.fire.rise = (uint16_t)(fxTime >> FX_SHIFT_FIRE_RISE);
            S.e.fire.top = extent - 1;
            S.e.fire.invH = (uint16_t)(0xFFFFu / extent);
            S.e.fire.coolMul = (uint8_t)(FX_FIRE_COOL_MAX
                - (((uint16_t)p.intensity * FX_FIRE_COOL_RANGE) >> 8));
            break;
        }

        case FX_RIPPLE: {
            // Half-pixel distances, so half the field step keeps the same
            // ring spacing as the other field effects.
            S.e.ripple.step = fieldStep >> 1;
            S.e.ripple.t = FX_PHASE(FX_SHIFT_RIPPLE);
            S.e.ripple.cx2 = (int16_t)(S.w - 1);
            S.e.ripple.cy2 = (int16_t)(S.h - 1);
            uint16_t reach = S.w > S.h ? S.w : S.h;  // largest |2x - (w - 1)| is below this
            uint8_t shift = 0;
            while ((reach >> shift) > FX_RIPPLE_MAX_COORD) shift++;
            S.e.ripple.shift = shift;
            break;
        }

        default:
            break;
    }
    #undef FX_PHASE
}

void fxStart() {
    S.major = 0;
    lineStart();
}

void fxNext(uint8_t rgb[3]) {
    colourPixel(evalPixel(S.x, S.y), rgb);

    if (S.alongY) S.y = (uint16_t)(S.y + S.step);
    else S.x = (uint16_t)(S.x + S.step);
    if (--S.lineLeft == 0) {
        S.major++;
        lineStart();
    }
}

void fxAt(uint16_t logicalIndex, uint8_t rgb[3]) {
    uint16_t x, y;
    if (S.layout & FX_LAYOUT_VERTICAL_FIRST) {
        // Same reading of a logical index as mapPixel() in ltp_328p_dual:
        // column-major when the matrix is wired along its columns.
        x = logicalIndex / S.h;
        y = logicalIndex % S.h;
    } else {
        x = logicalIndex % S.w;
        y = logicalIndex / S.w;
    }
    colourPixel(evalPixel(x, y), rgb);
}
