/**
 * LtpStreamFx - extended patterns.
 *
 * Part of ltp_fx.cpp, which includes this file inside its own namespace when
 * FX_EXTENDED is set. It is not a header for anything else.
 *
 * These patterns are for processors with thousands of cycles to spend on each
 * pixel: a Teensy 4.0 has about 18 000 for a WS2812 pixel, where the
 * ATmega328P has a few hundred. The rules of the base patterns are relaxed
 * where that buys something:
 *   - 32-bit maths with 64-bit products, and a divide or a square root where
 *     a pixel needs one;
 *   - a pixel may be the sum of many moving objects (sparks, balls, drops).
 *     The objects are worked out once per frame from the frame time alone, so
 *     memory grows with the number of objects and never with the number of
 *     LEDs.
 * What still holds: integer maths only, and a frame depends on nothing but
 * the settings and fxTime, so fxNext() and fxAt() agree and no frame needs
 * the one before it.
 */

#ifndef LTP_FX_EXTENDED_H
#define LTP_FX_EXTENDED_H

#if defined(__AVR__)
#error "The extended patterns read tables directly and need a 32-bit processor"
#endif

// ============================================================================
// LIMITS AND SHAPES
// ============================================================================

// Positions are in 1/256 pixel ("q8"). An object between two LEDs lights both
// in proportion, so slow movement is smooth.
#define FX_PIXEL_Q8              256

// Fireworks. One shell: 0.6 s rising, 1.7 s burning, dark until its period
// is over. Each further shell runs 0.37 s slower, so they drift apart.
#define FX_FW_MAX_SHELLS         8      // range of the count control
#define FX_FW_MAX_SPARKS         40
#define FX_FW_PERIOD_MS          2600
#define FX_FW_PERIOD_STEP_MS     370
#define FX_FW_RISE_MS            600
#define FX_FW_BURN_MS            1700
#define FX_FW_FLASH_MS           100    // white flash at the moment of the burst
#define FX_FW_MIN_SPARKS         8      // sparks = this + intensity / 8
#define FX_FW_SPARKS_SHIFT       3
#define FX_FW_RADIUS_PER_SCALE   (512L << 8)  // burst radius, q8: 16 px at scale 32
#define FX_FW_ROCKET_LEVEL       70     // the rising shell is a dim dot
#define FX_FW_FLASH_REACH        (2 * FX_PIXEL_Q8)
#define FX_FW_FLICKER_SHIFT      6      // dying sparks flicker in 64 ms steps
#define FX_FW_FLICKER_FROM       128    // of 255: flicker starts half-way through the burn
#define FX_FW_DROOP_SHIFT        10     // sparks sink by a quarter of the radius while they burn
#define FX_FW_BURST_LOWEST16     29500  // of 65536: a shell bursts from 45 % of the way up...
#define FX_FW_BURST_SPAN_MASK    0x7FFF // ...to 95 %,
#define FX_FW_ACROSS_LOWEST16    9830   // and on a matrix from 15 % of the way across...
#define FX_FW_ACROSS_SPAN16      45875  // ...to 85 %
#define FX_FW_SIZE_LEAST         160    // of 256: a shell is 62 % of the radius setting...
#define FX_FW_SIZE_SPAN_MASK     0x7F   // ...to 112 %
#define FX_FW_SPEED_LEAST        128    // of 256: on a matrix a spark flies at half speed...
#define FX_FW_SPEED_SPAN_MASK    0x7F   // ...to full speed, which fills the burst instead of ringing it
#define FX_FW_HUE_SPREAD_MASK    0x1F   // sparks of one shell differ by up to an eighth of the palette

// Bounce. Period goes with the square root of height, as under gravity.
#define FX_BOUNCE_MAX_BALLS      8
#define FX_BOUNCE_PERIOD_MS      1500   // one bounce of a ball that reaches the far end
#define FX_BOUNCE_MIN_PERIOD_MS  250
#define FX_BOUNCE_RADIUS_PER_SCALE (96L << 8)  // q8: 3 px at scale 32
#define FX_BOUNCE_MIN_RADIUS     192    // q8: 0.75 px
#define FX_BOUNCE_EDGE           128    // q8: the rim fades over one pixel, half each side
#define FX_BOUNCE_MIN_ENERGY     96     // of 255, at intensity 0
#define FX_BOUNCE_DRIFT_MS       3000   // matrix: slowest ball's time across and back...
#define FX_BOUNCE_DRIFT_SPAN_MASK 0x7FF // ...the others up to 2 s less
#define FX_BOUNCE_HEIGHT_LEAST16 36000  // of 65536: a ball reaches 55 %...
#define FX_BOUNCE_HEIGHT_SPAN_MASK 0x6FFF  // ...to 99 % of the height intensity allows

// Meteors.
#define FX_METEOR_PER_COUNT      2
#define FX_METEOR_MAX            (8 * FX_METEOR_PER_COUNT)
#define FX_METEOR_MIN_SPEED      15     // q8 per ms: 59 px/s
#define FX_METEOR_SPEED_SPAN     24     // fastest is 38 q8 per ms: 148 px/s
#define FX_METEOR_TAIL_FACTOR    1024L  // tail, q8 = (8 + intensity) * this / scale: 17 px at the defaults
#define FX_METEOR_TAIL_BASE      8
#define FX_METEOR_MIN_TAIL       (2 * FX_PIXEL_Q8)
#define FX_METEOR_FLICKER_SHIFT  5      // tail flickers in 32 ms steps
#define FX_METEOR_MIN_LEVEL      150    // of 255: the dimmest meteor
#define FX_METEOR_TAIL_LEAST     160    // of 256: a meteor's tail is 62 %...
#define FX_METEOR_TAIL_SPAN_MASK 0x5F   // ...to 99 % of the tail setting
#define FX_METEOR_FLICKER_MASK   7      // a tail LED is dimmed in one flicker step out of eight

// Lava lamp.
#define FX_LAVA_EXTRA_BLOBS      2      // count 1-8 gives 3-10 blobs
#define FX_LAVA_MAX_BLOBS        (8 + FX_LAVA_EXTRA_BLOBS)
#define FX_LAVA_PERIOD_MS        28000  // fastest blob, end to end and back; slowest 33 s more
#define FX_LAVA_RADIUS_PER_SCALE (256L << 8)  // reach of the largest blob, q8: 8 px at scale 32
#define FX_LAVA_MIN_RADIUS       (2L << 8)
#define FX_LAVA_MAX_RADIUS       (256L << 8)  // keeps squared distances in 32 bits
#define FX_LAVA_CORE_LEVEL       150    // palette position at the middle of one blob; merged blobs go higher
#define FX_LAVA_PERIOD_SPAN_MASK 0x7FFF // spread of the blobs' periods: 33 s
#define FX_LAVA_WOBBLE_FACTOR    3      // a second, slower sine keeps a blob from repeating itself:
#define FX_LAVA_WOBBLE_EXTRA_MS  1700   // three times the period and a bit, so never in step
#define FX_LAVA_MAIN_WEIGHT      179    // of 256: 70 % of a blob's travel is the first sine,
#define FX_LAVA_WOBBLE_WEIGHT    77     // 30 % the second
#define FX_LAVA_ACROSS_EXTRA_MS  900    // sideways it runs at a period of its own
#define FX_LAVA_SIZE_LEAST       150    // of 256: a blob is 59 %...
#define FX_LAVA_SIZE_SPAN_MASK   0x7F   // ...to 108 % of the largest

// Aurora. Noise coordinates are 16.16 fixed-point lattice cells.
#define FX_AURORA_STEP_PER_SCALE 64     // cells per pixel: a cell is 1024 / scale px (32 at scale 32)
#define FX_AURORA_TIME_SHIFT     3      // one cell per 8.2 s
#define FX_AURORA_SWAY           3      // curtains sway sideways by up to 1.5 cells
#define FX_AURORA_FLOOR          25900L // noise below this is dark sky at intensity 128: about a third of it
#define FX_AURORA_FLOOR_PER_STEP 70     // each step of intensity moves the cut-off
#define FX_AURORA_GAIN           52     // noise above the cut-off * 52 / 1024: full brightness after 5000
#define FX_AURORA_GAIN_SHIFT     10
#define FX_AURORA_FOOT           48     // of 255: height of the sharp lower edge on a matrix
#define FX_AURORA_SWAY_MIDDLE    24480L // middle of two octaves of noise
#define FX_AURORA_RAYS_SHIFT     3      // rays are eight times finer than curtains
#define FX_AURORA_RAYS_FLOOR     128    // of 255: rays dim a curtain by up to half
#define FX_AURORA_HEIGHT_TINT    96     // of 256: palette positions between foot and top of a matrix,
#define FX_AURORA_HEIGHT_BIAS    40     // less this, so the wandering colour sits a third of the way up
// Offsets in lattice cells that keep the four noise fields apart; without
// them sway, rays and colour would all follow the curtains.
#define FX_AURORA_SWAY_OFFSET    0x310000UL
#define FX_AURORA_RAYS_ROW       0x5A0000UL
#define FX_AURORA_TINT_OFFSET_X  0x3B0000UL
#define FX_AURORA_TINT_OFFSET_Z  0x770000UL
#define FX_AURORA_TINT_MIDDLE    135    // palette position the colour wanders around: just past the bright green
#define FX_AURORA_TINT_GAIN      5      // noise is bunched around its middle; * 5 / 2 spreads it over the palette
#define FX_AURORA_TINT_SHIFT     1
#define FX_AURORA_TINT_LOWEST    100    // below this the Aurora palette darkens towards its night sky

// Raindrops.
#define FX_RAIN_DROPS_PER_COUNT  2
#define FX_RAIN_MAX_DROPS        (8 * FX_RAIN_DROPS_PER_COUNT)
#define FX_RAIN_PERIOD_MS        1800   // shortest time between drops in one place, at intensity 128
#define FX_RAIN_SPEED            10     // q8 per ms: rings spread at 39 px/s
#define FX_RAIN_TRAIN_SHIFT      10     // a ring is followed by four wavelengths of ripple
#define FX_RAIN_CALM             72     // palette position of still water
#define FX_RAIN_PERIOD_SPAN_MASK 0x7FF  // drops differ in period by up to 2 s
#define FX_RAIN_RATE_BASE        384    // period * (384 - intensity) / 256: 1.5 times at 0, half at 255
#define FX_RAIN_GAIN_SHIFT       7      // a ring at full strength moves the palette position by 254

#define FX_MAX_OBJECTS           (FX_FW_MAX_SHELLS * (FX_FW_MAX_SPARKS + 1))

// Arbitrary: they only keep one pattern's random choices apart from another's.
#define FX_SALT_FIREWORKS        0xF15E0000UL
#define FX_SALT_BOUNCE           0xB0C40000UL
#define FX_SALT_METEORS          0x3E7E0000UL
#define FX_SALT_LAVA             0x1A7A0000UL
#define FX_SALT_RAIN             0xD5090000UL

// ============================================================================
// STATE
// ============================================================================

struct FxObject {
    int32_t along;    // position along the strip, or down the matrix, q8
    int32_t across;   // matrix column, q8
    int32_t reach;    // size, q8; what it measures is the pattern's business
    uint32_t aux;     // the pattern's own: a seed or a reciprocal
    uint8_t rgb[3];   // colour at full strength, or a level in rgb[0]
};

// Objects that belong together and the stretch they can light, so a pixel
// outside it skips them all.
struct FxObjectGroup {
    uint16_t first, count;
    int32_t lo, hi;
};

struct FxExtState {
    int32_t topQ8;        // position of the last pixel along
    int32_t acrossTopQ8;  // and across; 0 on a strip
    uint8_t baseAtEnd;    // "down" is towards the high end of `along`
    uint8_t scale;        // the scale setting, never 0
    uint16_t objects;     // entries of `object` in use
    uint8_t groups;
    uint32_t tick;        // flicker clock
    struct { uint32_t step, z; int32_t floor; uint32_t invHeight; } aurora;
    FxObjectGroup group[FX_FW_MAX_SHELLS];
    FxObject object[FX_MAX_OBJECTS];
};

FxExtState E;

// ============================================================================
// MATHS
// ============================================================================

// Integer hash (the "lowbias32" constants of Chris Wellons, public domain).
// Every random choice is the hash of an object's number and of how many
// times it has come round, so it is the same whenever the frame is redrawn.
inline uint32_t hash32(uint32_t v) {
    v ^= v >> 16;
    v *= 0x7FEB352DUL;
    v ^= v >> 15;
    v *= 0x846CA68BUL;
    v ^= v >> 16;
    return v;
}

inline uint32_t hash2(uint32_t a, uint32_t b) {
    return hash32(a ^ hash32(b));
}

// a * frac / 65536
inline uint32_t mulFrac16(uint32_t a, uint32_t frac16) {
    return (uint32_t)(((uint64_t)a * frac16) >> 16);
}

inline uint32_t absDiff(int32_t a, int32_t b) {
    return a > b ? (uint32_t)(a - b) : (uint32_t)(b - a);
}

inline uint8_t clamp8(int32_t v) {
    return v < 0 ? 0 : (v > 255 ? 255 : (uint8_t)v);
}

inline uint8_t limitCount(uint8_t count, uint8_t most) {
    return count < 1 ? 1 : (count > most ? most : count);
}

// Fast start, slow finish: 1 - (1 - f)^2, on 0..255.
inline uint8_t easeOut8(uint8_t f) {
    uint8_t rest = (uint8_t)(255 - f);
    return (uint8_t)(255 - scale8(rest, rest));
}

// Fraction 0..65535 of the way through `period` ms. Once per object per
// frame, so the 64-bit divide is affordable.
inline uint32_t phase16(uint32_t ms, uint32_t period) {
    return (uint32_t)(((uint64_t)(ms % period) << 16) / period);
}

uint32_t isqrt32(uint32_t v) {
    uint32_t root = 0;
    uint32_t bit = 1UL << 30;
    while (bit > v) bit >>= 2;
    while (bit) {
        if (v >= root + bit) {
            v -= root + bit;
            root = (root >> 1) + bit;
        } else {
            root >>= 1;
        }
        bit >>= 2;
    }
    return root;
}

inline int32_t lerpWide(int32_t a, int32_t b, uint8_t frac) {
    return a + (((b - a) * (int32_t)frac) >> 8);
}

// Three-dimensional value noise. Coordinates are 16.16 fixed-point lattice
// cells and the lattice repeats every 256; the result is 0..65280.
uint32_t noise3(uint32_t x, uint32_t y, uint32_t z) {
    uint8_t xi = (uint8_t)(x >> 16), yi = (uint8_t)(y >> 16), zi = (uint8_t)(z >> 16);
    uint8_t yi1 = (uint8_t)(yi + 1), zi1 = (uint8_t)(zi + 1);
    uint8_t fx = ease8((uint8_t)(x >> 8));
    uint8_t fy = ease8((uint8_t)(y >> 8));
    uint8_t fz = ease8((uint8_t)(z >> 8));

    uint8_t a0 = perm8(xi), a1 = perm8((uint8_t)(xi + 1));
    uint8_t b00 = perm8((uint8_t)(a0 + yi)), b01 = perm8((uint8_t)(a0 + yi1));
    uint8_t b10 = perm8((uint8_t)(a1 + yi)), b11 = perm8((uint8_t)(a1 + yi1));

    // Lattice values of the eight corners, interpolated along x, then y, then z.
    #define FX_CORNER(b, zc) ((int32_t)perm8((uint8_t)((b) + (zc))) << 8)
    int32_t near = lerpWide(lerpWide(FX_CORNER(b00, zi), FX_CORNER(b10, zi), fx),
                            lerpWide(FX_CORNER(b01, zi), FX_CORNER(b11, zi), fx), fy);
    int32_t far = lerpWide(lerpWide(FX_CORNER(b00, zi1), FX_CORNER(b10, zi1), fx),
                           lerpWide(FX_CORNER(b01, zi1), FX_CORNER(b11, zi1), fx), fy);
    #undef FX_CORNER
    return (uint32_t)lerpWide(near, far, fz);
}

// Octaves of noise3, each half the size and half the strength of the one
// before: 0 .. 65280 * (1 - 2^-octaves), bunched around the middle.
uint32_t fbm3(uint32_t x, uint32_t y, uint32_t z, uint8_t octaves) {
    uint32_t sum = 0;
    for (uint8_t o = 0; o < octaves; o++) {
        sum += noise3(x << o, y << o, z << o) >> (o + 1);
    }
    return sum;
}

// ============================================================================
// GEOMETRY AND LIGHT
// ============================================================================

// Objects move along a strip, and down the columns of a matrix.
inline int32_t pixelAlong(uint16_t x, uint16_t y) {
    return (int32_t)(S.is2d ? y : x) * FX_PIXEL_Q8;
}

inline int32_t pixelAcross(uint16_t x) {
    return (int32_t)x * FX_PIXEL_Q8;
}

// Height above the base to a position along. The base is where the fire
// burns: LED 0 of a strip, the bottom row of a matrix, the other end when
// reversed.
inline int32_t alongOfHeight(int32_t heightQ8) {
    return E.baseAtEnd ? E.topQ8 - heightQ8 : heightQ8;
}

// Palette position idx, or the one colour, at a linear strength of 0-255.
// The brightness setting is applied later, once per pixel.
void objectColour(uint8_t idx, uint8_t strength, uint8_t rgb[3]) {
    const uint8_t* colour = S.colorPalette ? S.solid : S.palBase + (uint16_t)idx * 3;
    rgb[0] = scale8(colour[0], strength);
    rgb[1] = scale8(colour[1], strength);
    rgb[2] = scale8(colour[2], strength);
}

// White, unless the palette setting allows one colour only.
void flashColour(uint8_t strength, uint8_t rgb[3]) {
    if (S.colorPalette) {
        objectColour(0, strength, rgb);
    } else {
        rgb[0] = rgb[1] = rgb[2] = strength;
    }
}

// Add an object's light to a pixel; weight 256 is all of it.
inline void addLight(uint32_t sum[3], const uint8_t rgb[3], uint32_t weight) {
    sum[0] += (rgb[0] * weight) >> 8;
    sum[1] += (rgb[1] * weight) >> 8;
    sum[2] += (rgb[2] * weight) >> 8;
}

inline void finishLight(const uint32_t sum[3], uint8_t rgb[3]) {
    for (uint8_t k = 0; k < 3; k++) {
        rgb[k] = scale8(sum[k] > 255 ? 255 : (uint8_t)sum[k], S.brightness);
    }
}

FxObject& newObject() {
    // The limits above add up to no more than the array holds; if they are
    // ever changed carelessly, reuse the last entry rather than write past it.
    if (E.objects < FX_MAX_OBJECTS) E.objects++;
    return E.object[E.objects - 1];
}

// ============================================================================
// FIREWORKS
// ============================================================================

void fireworksBegin(const FxParams& p, uint32_t ms) {
    uint8_t shells = limitCount(p.count, FX_FW_MAX_SHELLS);
    uint8_t sparks = (uint8_t)(FX_FW_MIN_SPARKS + (p.intensity >> FX_FW_SPARKS_SHIFT));
    if (S.is2d) sparks += sparks >> 1;  // a burst has an area to fill, not a line
    if (sparks > FX_FW_MAX_SPARKS) sparks = FX_FW_MAX_SPARKS;
    uint32_t radius = FX_FW_RADIUS_PER_SCALE / E.scale;
    uint32_t tick = ms >> FX_FW_FLICKER_SHIFT;

    E.groups = shells;
    for (uint8_t k = 0; k < shells; k++) {
        FxObjectGroup& group = E.group[k];
        group.first = E.objects;

        uint32_t period = FX_FW_PERIOD_MS + (uint32_t)k * FX_FW_PERIOD_STEP_MS;
        uint32_t clock = ms + hash2(FX_SALT_FIREWORKS, k) % period;  // each shell starts somewhere in its cycle
        uint32_t into = clock % period;
        uint32_t seed = hash2(FX_SALT_FIREWORKS + k, clock / period);

        // It bursts 45-95 % of the way up and, on a matrix, in the middle 70 %.
        int32_t burstHeight = (int32_t)mulFrac16(
            (uint32_t)E.topQ8, FX_FW_BURST_LOWEST16 + (seed & FX_FW_BURST_SPAN_MASK));
        int32_t burstAcross = (int32_t)mulFrac16(
            (uint32_t)E.acrossTopQ8, FX_FW_ACROSS_LOWEST16 + ((seed >> 15) % FX_FW_ACROSS_SPAN16));
        uint8_t hue = (uint8_t)(seed >> 24);
        uint32_t size = (radius * (FX_FW_SIZE_LEAST + ((seed >> 8) & FX_FW_SIZE_SPAN_MASK))) >> 8;

        if (into < FX_FW_RISE_MS) {
            // The shell on its way up, slowing as it climbs.
            uint8_t f = (uint8_t)(into * 255 / FX_FW_RISE_MS);
            FxObject& rocket = newObject();
            rocket.along = alongOfHeight((int32_t)(((uint32_t)burstHeight * easeOut8(f)) >> 8));
            rocket.across = burstAcross;
            rocket.reach = FX_PIXEL_Q8;
            objectColour(hue, FX_FW_ROCKET_LEVEL, rocket.rgb);
        } else if (into < FX_FW_RISE_MS + FX_FW_BURN_MS) {
            uint32_t burning = into - FX_FW_RISE_MS;
            uint8_t f = (uint8_t)(burning * 255 / FX_FW_BURN_MS);
            // Sparks fly out fast and slow down, sink, and fade.
            int32_t spread = (int32_t)((size * easeOut8(f)) >> 8);
            int32_t droop = (int32_t)((size * scale8(f, f)) >> FX_FW_DROOP_SHIFT);
            uint8_t fade = gamma2((uint8_t)(255 - scale8(f, f)));  // bright at first, then quickly out

            for (uint8_t i = 0; i < sparks; i++) {
                uint32_t r = hash2(seed, i);
                // Velocity as a fraction of the spread, -256..256.
                int32_t vAlong, vAcross = 0;
                if (S.is2d) {
                    uint8_t angle = (uint8_t)r;
                    int32_t speed = FX_FW_SPEED_LEAST + (int32_t)((r >> 8) & FX_FW_SPEED_SPAN_MASK);
                    // sin8 is 128 at zero and swings by 127; 64 is a quarter turn, the cosine.
                    vAlong = (((int32_t)sin8(angle) - 128) * speed) >> 7;
                    vAcross = (((int32_t)sin8((uint8_t)(angle + 64)) - 128) * speed) >> 7;
                } else {
                    vAlong = (int32_t)(r & 0x1FF) - 256;
                }
                FxObject& spark = newObject();
                spark.along = alongOfHeight(burstHeight + ((vAlong * spread) >> 8) - droop);
                spark.across = burstAcross + ((vAcross * spread) >> 8);
                spark.reach = FX_PIXEL_Q8;
                uint8_t level = fade;
                // Towards the end sparks gutter: each is dimmed at random, more often as it dies.
                if (f > FX_FW_FLICKER_FROM && (uint8_t)hash2(r, tick) < f) level >>= 2;
                objectColour((uint8_t)(hue + ((r >> 20) & FX_FW_HUE_SPREAD_MASK)), level, spark.rgb);
            }
            if (burning < FX_FW_FLASH_MS) {
                FxObject& flash = newObject();
                flash.along = alongOfHeight(burstHeight);
                flash.across = burstAcross;
                flash.reach = FX_FW_FLASH_REACH;
                flashColour((uint8_t)(255 - burning * 255 / FX_FW_FLASH_MS), flash.rgb);
            }
        }

        group.count = (uint16_t)(E.objects - group.first);
        group.lo = INT32_MAX;
        group.hi = INT32_MIN;
        for (uint16_t i = group.first; i < E.objects; i++) {
            const FxObject& o = E.object[i];
            if (o.along - o.reach < group.lo) group.lo = o.along - o.reach;
            if (o.along + o.reach > group.hi) group.hi = o.along + o.reach;
        }
    }
}

// Each object is a dot `reach` wide either side, brightest at its centre.
FX_NOINLINE void fxFireworks(uint16_t x, uint16_t y, uint8_t rgb[3]) {
    int32_t along = pixelAlong(x, y);
    int32_t across = pixelAcross(x);
    uint32_t sum[3] = { 0, 0, 0 };
    for (uint8_t g = 0; g < E.groups; g++) {
        const FxObjectGroup& group = E.group[g];
        if (along < group.lo || along > group.hi) continue;
        const FxObject* o = &E.object[group.first];
        for (uint16_t i = 0; i < group.count; i++, o++) {
            uint32_t reach = (uint32_t)o->reach;
            uint32_t da = absDiff(along, o->along);
            if (da >= reach) continue;
            uint32_t weight = 256 - (da << 8) / reach;
            if (S.is2d) {
                uint32_t db = absDiff(across, o->across);
                if (db >= reach) continue;
                weight = (weight * (256 - (db << 8) / reach)) >> 8;
            }
            addLight(sum, o->rgb, weight);
        }
    }
    finishLight(sum, rgb);
}

// ============================================================================
// BOUNCE
// ============================================================================

void bounceBegin(const FxParams& p, uint32_t ms) {
    uint8_t balls = limitCount(p.count, FX_BOUNCE_MAX_BALLS);
    int32_t radius = (int32_t)(FX_BOUNCE_RADIUS_PER_SCALE / E.scale);
    if (radius < FX_BOUNCE_MIN_RADIUS) radius = FX_BOUNCE_MIN_RADIUS;
    // How high the liveliest ball gets, as a fraction of the strip: 37 % at
    // intensity 0, all of it at 255.
    uint32_t energy16 = (uint32_t)(FX_BOUNCE_MIN_ENERGY
        + (((uint16_t)p.intensity * (255 - FX_BOUNCE_MIN_ENERGY)) >> 8)) << 8;

    for (uint8_t k = 0; k < balls; k++) {
        uint32_t r = hash2(FX_SALT_BOUNCE, k);
        uint32_t height16 = mulFrac16(
            energy16, FX_BOUNCE_HEIGHT_LEAST16 + (r & FX_BOUNCE_HEIGHT_SPAN_MASK));
        uint32_t period = (FX_BOUNCE_PERIOD_MS * isqrt32(height16)) >> 8;
        if (period < FX_BOUNCE_MIN_PERIOD_MS) period = FX_BOUNCE_MIN_PERIOD_MS;
        uint32_t clock = ms + (r >> 16);

        // A parabola over each bounce: 4u(1 - u), u from 0 to 1.
        uint32_t u = phase16(clock, period);
        uint32_t arc16 = (u * (65535 - u)) >> 14;

        FxObject& ball = newObject();
        ball.along = alongOfHeight(
            (int32_t)mulFrac16(mulFrac16((uint32_t)E.topQ8, height16), arc16));
        // On a matrix each ball also wanders from side to side.
        uint32_t v = phase16(clock, FX_BOUNCE_DRIFT_MS - ((r >> 8) & FX_BOUNCE_DRIFT_SPAN_MASK));
        uint32_t triangle16 = v < 32768 ? v * 2 : (65535 - v) * 2;
        ball.across = (int32_t)mulFrac16((uint32_t)E.acrossTopQ8, triangle16);
        ball.reach = radius;
        objectColour((uint8_t)(S.hue + (uint16_t)k * 256 / balls), 255, ball.rgb);
    }
}

// Each ball is a disc with a soft rim.
FX_NOINLINE void fxBounce(uint16_t x, uint16_t y, uint8_t rgb[3]) {
    int32_t along = pixelAlong(x, y);
    int32_t across = pixelAcross(x);
    uint32_t sum[3] = { 0, 0, 0 };
    const FxObject* o = E.object;
    for (uint16_t i = 0; i < E.objects; i++, o++) {
        uint32_t limit = (uint32_t)o->reach + FX_BOUNCE_EDGE;
        uint32_t d = absDiff(along, o->along);
        if (d >= limit) continue;
        if (S.is2d) {
            uint32_t db = absDiff(across, o->across);
            if (db >= limit) continue;
            d = isqrt32(d * d + db * db);
            if (d >= limit) continue;
        }
        uint32_t weight = limit - d;
        addLight(sum, o->rgb, weight > 256 ? 256 : weight);
    }
    finishLight(sum, rgb);
}

// ============================================================================
// METEORS
// ============================================================================

void meteorsBegin(const FxParams& p, uint32_t ms) {
    uint8_t meteors = (uint8_t)(limitCount(p.count, FX_METEOR_MAX / FX_METEOR_PER_COUNT)
                                * FX_METEOR_PER_COUNT);
    uint32_t tail = (uint32_t)(FX_METEOR_TAIL_BASE + p.intensity) * FX_METEOR_TAIL_FACTOR / E.scale;
    if (tail < FX_METEOR_MIN_TAIL) tail = FX_METEOR_MIN_TAIL;
    uint32_t length = (uint32_t)E.topQ8 + FX_PIXEL_Q8;
    E.tick = ms >> FX_METEOR_FLICKER_SHIFT;

    for (uint8_t k = 0; k < meteors; k++) {
        uint32_t r = hash2(FX_SALT_METEORS, k);
        uint32_t speed = FX_METEOR_MIN_SPEED + r % FX_METEOR_SPEED_SPAN;
        // One lap: the strip, the tail leaving it, then up to a strip's length of dark.
        uint32_t lap = length + tail + mulFrac16(length, (r >> 8) & 0xFFFF);
        uint32_t travelled = ms * speed + (r >> 4);
        uint32_t seed = hash2(FX_SALT_METEORS + k, travelled / lap);

        FxObject& meteor = newObject();
        meteor.along = (int32_t)(travelled % lap);  // of the head, in the direction of travel
        meteor.across = (int32_t)(seed % S.w) * FX_PIXEL_Q8;  // a column of its own on a matrix
        meteor.reach = (int32_t)(
            (tail * (FX_METEOR_TAIL_LEAST + ((seed >> 8) & FX_METEOR_TAIL_SPAN_MASK))) >> 8);
        meteor.aux = seed;
        meteor.rgb[0] = (uint8_t)(FX_METEOR_MIN_LEVEL + ((seed >> 16) % (256 - FX_METEOR_MIN_LEVEL)));
    }
}

// The palette is read as a temperature: its top at the head, its bottom at
// the end of the tail. Every LED lets go of the light at its own rate, which
// makes the tail crumble instead of fading evenly.
FX_NOINLINE void fxMeteors(uint16_t x, uint16_t y, uint8_t rgb[3]) {
    int32_t position = pixelAlong(x, y);
    if (S.reverse) position = E.topQ8 - position;
    int32_t across = pixelAcross(x);
    uint32_t index = logicalIndex(x, y);
    uint32_t sum[3] = { 0, 0, 0 };
    const FxObject* o = E.object;
    for (uint16_t i = 0; i < E.objects; i++, o++) {
        if (S.is2d && o->across != across) continue;
        int32_t behind = o->along - position;
        if (behind <= -FX_PIXEL_Q8 || behind >= o->reach) continue;
        uint32_t level;
        uint8_t idx = 255;
        if (behind < 0) {
            level = (uint32_t)(FX_PIXEL_Q8 + behind);  // the head arriving
            if (level > 255) level = 255;
        } else {
            uint32_t f = (uint32_t)behind * 255 / (uint32_t)o->reach;
            uint32_t own = hash2(o->aux, index) & 0xFF;
            uint32_t gone = f + ((f * own) >> 8);  // this LED fades up to twice as fast
            if (gone >= 255) continue;
            level = 255 - gone;
            idx = (uint8_t)(255 - f);
            if ((hash2(o->aux ^ index, E.tick) & FX_METEOR_FLICKER_MASK) == 0) level >>= 1;
        }
        // No gamma on the level: the palette already darkens towards the
        // end of the tail, and both together would cut the tail short.
        uint8_t colour[3];
        objectColour(idx, (uint8_t)((level * o->rgb[0]) >> 8), colour);
        addLight(sum, colour, 256);
    }
    finishLight(sum, rgb);
}

// ============================================================================
// LAVA LAMP
// ============================================================================

void lavaBegin(const FxParams& p, uint32_t ms) {
    uint8_t blobs = (uint8_t)(limitCount(p.count, FX_LAVA_MAX_BLOBS - FX_LAVA_EXTRA_BLOBS)
                              + FX_LAVA_EXTRA_BLOBS);
    uint32_t largest = FX_LAVA_RADIUS_PER_SCALE / E.scale;

    for (uint8_t k = 0; k < blobs; k++) {
        uint32_t r = hash2(FX_SALT_LAVA, k);
        // Two sines of different period, so a blob never quite repeats itself.
        uint32_t period = FX_LAVA_PERIOD_MS + (r & FX_LAVA_PERIOD_SPAN_MASK);
        uint32_t wobble = period * FX_LAVA_WOBBLE_FACTOR + FX_LAVA_WOBBLE_EXTRA_MS;
        uint16_t phaseA = (uint16_t)(phase16(ms, period) + (r >> 16));
        uint16_t phaseB = (uint16_t)(phase16(ms, wobble) + (r >> 8));
        uint32_t place16 = ((uint32_t)sin16(phaseA) * FX_LAVA_MAIN_WEIGHT
                            + (uint32_t)sin16(phaseB) * FX_LAVA_WOBBLE_WEIGHT) >> 8;

        FxObject& blob = newObject();
        blob.along = (int32_t)mulFrac16((uint32_t)E.topQ8, place16);
        uint32_t sideways = period + FX_LAVA_ACROSS_EXTRA_MS + ((r >> 5) & FX_LAVA_PERIOD_SPAN_MASK);
        uint16_t phaseC = (uint16_t)(phase16(ms, sideways) + (r >> 3));
        blob.across = (int32_t)mulFrac16((uint32_t)E.acrossTopQ8, sin16(phaseC));

        uint32_t radius = (largest * (FX_LAVA_SIZE_LEAST + ((r >> 8) & FX_LAVA_SIZE_SPAN_MASK))) >> 8;
        if (radius < FX_LAVA_MIN_RADIUS) radius = FX_LAVA_MIN_RADIUS;
        if (radius > FX_LAVA_MAX_RADIUS) radius = FX_LAVA_MAX_RADIUS;
        blob.reach = (int32_t)radius;
        // Reciprocal of the squared radius, in the 1/16 pixel units the
        // per-pixel code squares distances in. One divide per blob per frame
        // instead of one per blob per pixel.
        uint32_t r4 = radius >> 4;
        blob.aux = 0xFFFFFFFFUL / (r4 * r4);
    }
}

// Metaballs: each blob adds (1 - d^2 / r^2)^2 inside its reach, and the sum
// is the palette position. Where blobs overlap the sum rises smoothly, which
// is what makes them appear to merge.
FX_NOINLINE FxPixel fxLavaLamp(uint16_t x, uint16_t y) {
    int32_t along = pixelAlong(x, y);
    int32_t across = pixelAcross(x);
    uint32_t field = 0;
    const FxObject* o = E.object;
    for (uint16_t i = 0; i < E.objects; i++, o++) {
        uint32_t reach = (uint32_t)o->reach;
        uint32_t da = absDiff(along, o->along);
        if (da >= reach) continue;
        da >>= 4;
        uint32_t d2 = da * da;
        if (S.is2d) {
            uint32_t db = absDiff(across, o->across);
            if (db >= reach) continue;
            db >>= 4;
            d2 += db * db;
        }
        uint32_t q = (uint32_t)(((uint64_t)d2 * o->aux) >> 16);  // d^2 / r^2 on 0..65535
        if (q >= 65535) continue;
        uint32_t inside = 65535 - q;
        field += (inside * inside) >> 16;
    }
    uint32_t idx = (field * FX_LAVA_CORE_LEVEL) >> 16;
    return FX_PIXEL(idx > 255 ? 255 : idx, 255);
}

// ============================================================================
// AURORA
// ============================================================================

void auroraBegin(const FxParams& p, uint32_t ms) {
    E.aurora.step = (uint32_t)E.scale * FX_AURORA_STEP_PER_SCALE;
    E.aurora.z = ms << FX_AURORA_TIME_SHIFT;
    E.aurora.floor = FX_AURORA_FLOOR - ((int32_t)p.intensity - 128) * FX_AURORA_FLOOR_PER_STEP;
    E.aurora.invHeight = 0xFFFFFFUL / S.h;  // 255 / height, as 16.16
}

FX_NOINLINE FxPixel fxAurora(uint16_t x, uint16_t y) {
    uint32_t z = E.aurora.z;
    uint32_t u = (uint32_t)x * E.aurora.step;
    // Curtains hang: features are four times as long down a matrix as across.
    uint32_t v = S.is2d ? (uint32_t)y * (E.aurora.step >> 2) : 0;

    // Sway: move the sampling point sideways by a slower, larger noise.
    int32_t sway = (int32_t)fbm3(u >> 1, v, (z >> 1) + FX_AURORA_SWAY_OFFSET, 2)
                 - FX_AURORA_SWAY_MIDDLE;
    uint32_t swayed = u + (uint32_t)(sway * FX_AURORA_SWAY);

    int32_t light = (int32_t)fbm3(swayed, v, z, 3) - E.aurora.floor;
    if (light <= 0) return FX_PIXEL(0, 0);
    uint8_t val = clamp8((light * FX_AURORA_GAIN) >> FX_AURORA_GAIN_SHIFT);

    // Rays: fine, fast structure that shimmers within a curtain.
    uint8_t rays = (uint8_t)(noise3(swayed << FX_AURORA_RAYS_SHIFT, FX_AURORA_RAYS_ROW, z) >> 8);
    val = scale8(val, (uint8_t)(FX_AURORA_RAYS_FLOOR + (rays >> 1)));

    // Colour wanders slowly over large distances.
    uint8_t tint = (uint8_t)(noise3((u >> 1) + FX_AURORA_TINT_OFFSET_X, v >> 1,
                                    (z >> 2) + FX_AURORA_TINT_OFFSET_Z) >> 8);
    int32_t idx = FX_AURORA_TINT_MIDDLE
        + ((((int32_t)tint - 128) * FX_AURORA_TINT_GAIN) >> FX_AURORA_TINT_SHIFT);
    if (S.is2d) {
        // Green below, violet above; a sharp foot and a long fade upwards.
        uint8_t height = (uint8_t)(((uint32_t)(S.h - 1 - y) * E.aurora.invHeight) >> 16);
        idx += (((int32_t)height * FX_AURORA_HEIGHT_TINT) >> 8) - FX_AURORA_HEIGHT_BIAS;
        uint8_t profile = height < FX_AURORA_FOOT
            ? (uint8_t)((uint16_t)height * 255 / FX_AURORA_FOOT)
            : (uint8_t)(255 - ((height - FX_AURORA_FOOT) >> 1));
        val = scale8(val, profile);
    }
    if (idx < FX_AURORA_TINT_LOWEST) idx = FX_AURORA_TINT_LOWEST;
    return FX_PIXEL(clamp8(idx), val);
}

// ============================================================================
// RAINDROPS
// ============================================================================

void raindropsBegin(const FxParams& p, uint32_t ms) {
    uint8_t drops = (uint8_t)(limitCount(p.count, FX_RAIN_MAX_DROPS / FX_RAIN_DROPS_PER_COUNT)
                              * FX_RAIN_DROPS_PER_COUNT);
    for (uint8_t k = 0; k < drops; k++) {
        uint32_t r = hash2(FX_SALT_RAIN, k);
        // More intensity, more rain: periods run from 1.5 times to half.
        uint32_t period = ((FX_RAIN_PERIOD_MS + (r & FX_RAIN_PERIOD_SPAN_MASK))
                           * (FX_RAIN_RATE_BASE - (uint32_t)p.intensity)) >> 8;
        uint32_t clock = ms + (r >> 12);
        uint32_t age = clock % period;
        uint32_t seed = hash2(FX_SALT_RAIN + k, clock / period);

        FxObject& drop = newObject();
        drop.along = (int32_t)mulFrac16((uint32_t)E.topQ8 + FX_PIXEL_Q8, seed & 0xFFFF);
        drop.across = (int32_t)mulFrac16((uint32_t)E.acrossTopQ8 + FX_PIXEL_Q8, seed >> 16);
        drop.reach = (int32_t)(age * FX_RAIN_SPEED);  // how far the first ring has got
        uint8_t left = (uint8_t)(255 - age * 255 / period);
        drop.rgb[0] = scale8(left, left);  // the whole pattern dies away with age
    }
}

// Each drop is a train of rings behind an expanding front. Rings from
// different drops add where they cross.
FX_NOINLINE FxPixel fxRaindrops(uint16_t x, uint16_t y) {
    int32_t along = pixelAlong(x, y);
    int32_t across = pixelAcross(x);
    int32_t water = 0;
    const FxObject* o = E.object;
    for (uint16_t i = 0; i < E.objects; i++, o++) {
        uint32_t front = (uint32_t)o->reach;
        uint32_t d = absDiff(along, o->along);
        if (d > front) continue;
        if (S.is2d) {
            uint32_t db = absDiff(across, o->across);
            if (db > front) continue;
            uint32_t da4 = d >> 4, db4 = db >> 4;
            d = isqrt32(da4 * da4 + db4 * db4) << 4;
            if (d > front) continue;
        }
        // One wavelength is 65536 / scale in q8: 8 px at scale 32.
        uint32_t phase = (front - d) * E.scale;
        uint32_t spent = phase >> FX_RAIN_TRAIN_SHIFT;
        if (spent >= 255) continue;
        int32_t wave = (int32_t)sin8((uint8_t)(phase >> 8)) - 128;
        int32_t strength = scale8((uint8_t)(255 - spent), o->rgb[0]);
        water += (wave * strength) >> FX_RAIN_GAIN_SHIFT;
    }
    return FX_PIXEL(clamp8(FX_RAIN_CALM + water), 255);
}

// ============================================================================
// DISPATCH
// ============================================================================

// Effects whose pixel is a sum of coloured lights write RGB themselves; the
// others return a palette position and brightness like the base effects.
typedef void (*FxEvalRgb)(uint16_t x, uint16_t y, uint8_t rgb[3]);
FxEvalRgb evalRgb = nullptr;

void extendedBeginFrame(const FxParams& p, uint32_t fxTime) {
    // fxTime gains 256 units per millisecond at nominal speed, so this is
    // animation time in ms. It wraps after 4.6 hours at nominal speed and
    // these patterns' periods do not divide that: they jump once at the wrap.
    uint32_t ms = fxTime >> 8;

    E.objects = 0;
    E.groups = 0;
    E.scale = p.scale ? p.scale : 1;
    E.topQ8 = (int32_t)((S.is2d ? S.h : S.w) - 1) * FX_PIXEL_Q8;
    E.acrossTopQ8 = S.is2d ? (int32_t)(S.w - 1) * FX_PIXEL_Q8 : 0;
    E.baseAtEnd = (S.is2d != 0) != (S.reverse != 0);

    switch (S.effect) {
        case FX_FIREWORKS: fireworksBegin(p, ms); evalRgb = fxFireworks;   break;
        case FX_BOUNCE:    bounceBegin(p, ms);    evalRgb = fxBounce;      break;
        case FX_METEORS:   meteorsBegin(p, ms);   evalRgb = fxMeteors;     break;
        case FX_LAVA_LAMP: lavaBegin(p, ms);      evalPixel = fxLavaLamp;  break;
        case FX_AURORA:    auroraBegin(p, ms);    evalPixel = fxAurora;    break;
        case FX_RAINDROPS: raindropsBegin(p, ms); evalPixel = fxRaindrops; break;
        default: break;
    }
}

#endif // LTP_FX_EXTENDED_H
