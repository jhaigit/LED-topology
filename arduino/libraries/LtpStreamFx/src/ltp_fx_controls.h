/**
 * LtpStreamFx - configuration and controls shared by the ltp_stream_* sketches
 *
 * One table describes every control: its protocol id, type, range, name and
 * where its value lives in FxConfig. The serial sketches answer INFO_CONTROLS,
 * GET_CONTROL and SET_CONTROL from it; the ESP32 sketch builds its JSON
 * capability list from the same table. Adding a control is one table row.
 *
 * Header-only on purpose: include it from exactly one file of a sketch, after
 * defining the board's limits:
 *
 *     #define FXC_MAX_PIXELS     1000         // largest num_pixels accepted
 *     #define FXC_DEFAULT_NAME   "LTP-Stream" // factory device name
 *     #define FXC_HAS_APA102     1            // board can drive APA102 / SK9822
 *     #define FXC_HAS_LPD8806    0            // board can drive LPD8806
 *     #define FXC_HAS_LOAD       0            // board measures its pixel-time load
 *     #include <ltp_fx_controls.h>
 */

#ifndef LTP_FX_CONTROLS_H
#define LTP_FX_CONTROLS_H

#include <Arduino.h>
#include <stddef.h>
#include "ltp_fx.h"

#ifndef FXC_MAX_PIXELS
#error "Define FXC_MAX_PIXELS before including ltp_fx_controls.h"
#endif
#ifndef FXC_DEFAULT_NAME
#define FXC_DEFAULT_NAME "LTP-Stream"
#endif
#ifndef FXC_HAS_APA102
#define FXC_HAS_APA102 1
#endif
#ifndef FXC_HAS_LPD8806
#define FXC_HAS_LPD8806 0
#endif
#ifndef FXC_HAS_LOAD
#define FXC_HAS_LOAD 0
#endif

// ============================================================================
// CONTROL IDS AND TYPES
// ============================================================================

// Ids 0-11 mean what they mean in ltp_328p_dual; the rest are new here.
#define FXC_ID_BRIGHTNESS     0
#define FXC_ID_AUTO_SHOW      3    // not listed: accepted and ignored, see fxcSet()
#define FXC_ID_FRAME_ACK      4    // not listed: accepted and ignored
#define FXC_ID_LOCAL_MODE     6
#define FXC_ID_CYCLE_TIME     7
#define FXC_ID_NUM_PIXELS     8
#define FXC_ID_LED_TYPE       9
#define FXC_ID_MATRIX_WIDTH   10
#define FXC_ID_MATRIX_LAYOUT  11
#define FXC_ID_COLOR_ORDER    12
#define FXC_ID_SPEED          13
#define FXC_ID_SCALE          14
#define FXC_ID_PALETTE        15
#define FXC_ID_HUE            16
#define FXC_ID_SATURATION     17
#define FXC_ID_INTENSITY      18
#define FXC_ID_COUNT          19
#define FXC_ID_REVERSE        20
#define FXC_ID_MAX_FPS        21
#define FXC_ID_FPS            22   // read-only: frames per second achieved
#define FXC_ID_LOAD           23   // read-only, with FXC_HAS_LOAD: see FxRuntime::load
#define FXC_ID_SAVE           0xF0
#define FXC_ID_REBOOT         0xF1

// Same numbers as CTRL_TYPE_* and CTRL_FLAG_* in ltp_protocol.h, repeated so
// the ESP32 sketch does not need the serial protocol library.
#define FXC_TYPE_BOOL    0x01
#define FXC_TYPE_UINT8   0x02
#define FXC_TYPE_UINT16  0x03
#define FXC_TYPE_ACTION  0x06
#define FXC_TYPE_ENUM    0x10

#define FXC_FLAG_HARDWARE  0x01
#define FXC_FLAG_READONLY  0x02
#define FXC_FLAG_VOLATILE  0x04
#define FXC_FLAG_ACTION    0x08

// Results of fxcSet().
#define FXC_OK            0
#define FXC_ERR_UNKNOWN   1   // no such control
#define FXC_ERR_VALUE     2   // out of range, or not one of the enum options
#define FXC_ERR_READONLY  3
#define FXC_DO_SAVE       4   // action: caller saves the configuration
#define FXC_DO_REBOOT     5   // action: caller restarts the board

// LED chips (values of the led_type control).
#define FXLED_WS2812   0   // also SK6812 RGB: one wire, 800 kHz
#define FXLED_APA102   1   // also SK9822: data + clock
#define FXLED_LPD8806  2   // data + clock, 7 bits per colour

// Colour orders (values of the color_order control). 0 picks the usual order
// for the LED chip; the others name the order of the bytes on the wire.
#define FXORDER_DEFAULT 0
#define FXORDER_RGB     1
#define FXORDER_RBG     2
#define FXORDER_GRB     3
#define FXORDER_GBR     4
#define FXORDER_BRG     5
#define FXORDER_BGR     6
#define FXORDER_COUNT   7

// ============================================================================
// CONFIGURATION (persisted as one block)
// ============================================================================

#define FXC_MAGIC       0x4C53   // "LS": LTP stream firmware
#define FXC_VERSION     1
#define FXC_NAME_MAXLEN 15       // wire cap of INFO_ALL / SET_NAME: 15 chars + NUL

// Factory defaults. A strip of 60 at a quarter brightness draws well under
// 1 A in the default rainbow, so a first power-up from USB is safe.
#define FXC_DEFAULT_BRIGHTNESS  64
#define FXC_DEFAULT_MODE        FX_RAINBOW
#define FXC_DEFAULT_CYCLE_TIME  10    // seconds per effect in cycle mode
#define FXC_DEFAULT_NUM_PIXELS  60
#define FXC_DEFAULT_SPEED       FX_SPEED_NOMINAL
#define FXC_DEFAULT_SCALE       32    // 64 px per colour cycle
#define FXC_DEFAULT_SATURATION  255
#define FXC_DEFAULT_INTENSITY   128
#define FXC_DEFAULT_COUNT       2
#define FXC_DEFAULT_MAX_FPS     60
#define FXC_MAX_COUNT           8
#define FXC_MAX_FPS_LIMIT       200
#define FXC_MAX_CYCLE_TIME      3600
#define FXC_MAX_MATRIX_WIDTH    255   // the control is one byte

struct FxConfig {
    uint16_t magic;
    uint8_t version;
    uint8_t brightness;
    uint8_t mode;          // FxEffect or FX_CYCLE
    uint16_t cycleTime;    // seconds
    uint16_t numPixels;
    uint8_t ledType;       // FXLED_*
    uint8_t matrixWidth;   // 1 = strip
    uint8_t matrixLayout;  // FX_LAYOUT_* bits
    uint8_t colorOrder;    // FXORDER_*
    uint8_t speed;
    uint8_t scale;
    uint8_t palette;       // FxPalette
    uint8_t hue;
    uint8_t saturation;
    uint8_t intensity;
    uint8_t count;
    uint8_t reverse;
    uint8_t maxFps;
    char name[FXC_NAME_MAXLEN + 1];
};

// Everything that changes while running and is not saved.
struct FxRuntime {
    uint32_t fxTime;       // animation clock, see ltp_fx.h
    uint32_t cycleMs;      // time spent in the current effect of cycle mode
    uint8_t cycleEffect;   // effect showing when mode is FX_CYCLE
    uint16_t fps;          // measured, for the fps control
    uint16_t blankTo;      // LEDs beyond numPixels still to be turned off once
    uint8_t blankLedType;  // chip those LEDs are on
#if FXC_HAS_LOAD
    // Share of the time one pixel takes on the wire that the last frame spent
    // calculating it, in percent. The pattern keeps up while this is under 100.
    uint8_t load;
#endif
};

static FxConfig fxConfig;
static FxRuntime fxRuntime;

// ============================================================================
// CONTROL TABLE
// ============================================================================

struct FxEnumOption {
    uint8_t value;
    const char* label;  // PROGMEM
};

struct FxControlDef {
    uint8_t id;
    uint8_t type;
    uint8_t flags;
    int16_t minVal;
    int16_t maxVal;
    const char* name;             // PROGMEM
    const char* description;      // PROGMEM
    uint8_t numOptions;           // 0 unless FXC_TYPE_ENUM
    const FxEnumOption* options;  // PROGMEM
    uint8_t offset;               // of the value in FxConfig; FXC_NO_STORAGE if none
};

#define FXC_NO_STORAGE 0xFF
#define FXC_AT(field) ((uint8_t)offsetof(FxConfig, field))

#define FXC_STR(sym, text) static const char sym[] PROGMEM = text

FXC_STR(fxcN_brightness, "brightness");     FXC_STR(fxcD_brightness, "0-255");
FXC_STR(fxcN_local_mode, "local_mode");     FXC_STR(fxcD_local_mode, "pattern shown");
FXC_STR(fxcN_cycle_time, "cycle_time");     FXC_STR(fxcD_cycle_time, "mode cycle, secs");
FXC_STR(fxcN_num_pixels, "num_pixels");     FXC_STR(fxcD_num_pixels, "pixel count");
FXC_STR(fxcN_led_type, "led_type");         FXC_STR(fxcD_led_type, "LED chip");
FXC_STR(fxcN_matrix_width, "matrix_width"); FXC_STR(fxcD_matrix_width, "1=strip, >1=matrix");
FXC_STR(fxcN_matrix_layout, "matrix_layout"); FXC_STR(fxcD_matrix_layout, "pixel mapping");
FXC_STR(fxcN_color_order, "color_order");   FXC_STR(fxcD_color_order, "byte order on wire");
FXC_STR(fxcN_speed, "speed");               FXC_STR(fxcD_speed, "128=normal, 0=frozen");
FXC_STR(fxcN_scale, "scale");               FXC_STR(fxcD_scale, "detail, 32=64px/cycle");
FXC_STR(fxcN_palette, "palette");           FXC_STR(fxcD_palette, "colours used");
FXC_STR(fxcN_hue, "hue");                   FXC_STR(fxcD_hue, "colour 0-255");
FXC_STR(fxcN_saturation, "saturation");     FXC_STR(fxcD_saturation, "0=white, 255=vivid");
FXC_STR(fxcN_intensity, "intensity");       FXC_STR(fxcD_intensity, "tail, density, flame");
FXC_STR(fxcN_count, "count");               FXC_STR(fxcD_count, "how many objects");
FXC_STR(fxcN_reverse, "reverse");           FXC_STR(fxcD_reverse, "flip direction");
FXC_STR(fxcN_max_fps, "max_fps");           FXC_STR(fxcD_max_fps, "frame rate limit");
FXC_STR(fxcN_fps, "fps");                   FXC_STR(fxcD_fps, "frame rate achieved");
#if FXC_HAS_LOAD
FXC_STR(fxcN_load, "load");                 FXC_STR(fxcD_load, "% of pixel time used");
#endif
FXC_STR(fxcN_save, "save");                 FXC_STR(fxcD_save, "save settings");
FXC_STR(fxcN_reboot, "reboot");             FXC_STR(fxcD_reboot, "restart");

FXC_STR(fxcL_off, "Off");         FXC_STR(fxcL_scanner, "Scanner");
FXC_STR(fxcL_rainbow, "Rainbow"); FXC_STR(fxcL_fire, "Fire");
FXC_STR(fxcL_sparkle, "Sparkle"); FXC_STR(fxcL_chase, "Chase");
FXC_STR(fxcL_solid, "Solid");     FXC_STR(fxcL_comet, "Comet");
FXC_STR(fxcL_waves, "Waves");     FXC_STR(fxcL_twinkle, "Twinkle");
FXC_STR(fxcL_noise, "Noise");     FXC_STR(fxcL_plasma, "Plasma");
FXC_STR(fxcL_ripple, "Ripple");   FXC_STR(fxcL_cycle, "Cycle");
#if FX_EXTENDED
FXC_STR(fxcL_fireworks, "Fireworks"); FXC_STR(fxcL_bounce, "Bounce");
FXC_STR(fxcL_meteors, "Meteors");     FXC_STR(fxcL_lava_lamp, "Lava Lamp");
FXC_STR(fxcL_aurora, "Aurora");       FXC_STR(fxcL_raindrops, "Raindrops");
#endif

static const FxEnumOption fxcModeOptions[] PROGMEM = {
    { FX_OFF, fxcL_off },         { FX_SCANNER, fxcL_scanner }, { FX_RAINBOW, fxcL_rainbow },
    { FX_FIRE, fxcL_fire },       { FX_SPARKLE, fxcL_sparkle }, { FX_CHASE, fxcL_chase },
    { FX_SOLID, fxcL_solid },     { FX_COMET, fxcL_comet },     { FX_WAVES, fxcL_waves },
    { FX_TWINKLE, fxcL_twinkle }, { FX_NOISE, fxcL_noise },     { FX_PLASMA, fxcL_plasma },
    { FX_RIPPLE, fxcL_ripple },
#if FX_EXTENDED
    { FX_FIREWORKS, fxcL_fireworks }, { FX_BOUNCE, fxcL_bounce },   { FX_METEORS, fxcL_meteors },
    { FX_LAVA_LAMP, fxcL_lava_lamp }, { FX_AURORA, fxcL_aurora },   { FX_RAINDROPS, fxcL_raindrops },
#endif
    { FX_CYCLE, fxcL_cycle },
};

FXC_STR(fxcL_ws2812, "WS2812");
FXC_STR(fxcL_apa102, "APA102");
FXC_STR(fxcL_lpd8806, "LPD8806");

static const FxEnumOption fxcLedOptions[] PROGMEM = {
    { FXLED_WS2812, fxcL_ws2812 },
#if FXC_HAS_APA102
    { FXLED_APA102, fxcL_apa102 },
#endif
#if FXC_HAS_LPD8806
    { FXLED_LPD8806, fxcL_lpd8806 },
#endif
};

FXC_STR(fxcL_normal, "Normal");     FXC_STR(fxcL_serp, "Serpentine");
FXC_STR(fxcL_vert, "Vertical");     FXC_STR(fxcL_vs, "Vert+Serp");
FXC_STR(fxcL_bottom, "Bottom");     FXC_STR(fxcL_bs, "Bottom+Serp");
FXC_STR(fxcL_bv, "Bottom+Vert");    FXC_STR(fxcL_bvs, "Bottom+V+S");

static const FxEnumOption fxcLayoutOptions[] PROGMEM = {
    { 0, fxcL_normal }, { 1, fxcL_serp }, { 2, fxcL_vert }, { 3, fxcL_vs },
    { 4, fxcL_bottom }, { 5, fxcL_bs },   { 6, fxcL_bv },   { 7, fxcL_bvs },
};

FXC_STR(fxcL_default, "Default");
FXC_STR(fxcL_rgb, "RGB"); FXC_STR(fxcL_rbg, "RBG"); FXC_STR(fxcL_grb, "GRB");
FXC_STR(fxcL_gbr, "GBR"); FXC_STR(fxcL_brg, "BRG"); FXC_STR(fxcL_bgr, "BGR");

static const FxEnumOption fxcOrderOptions[] PROGMEM = {
    { FXORDER_DEFAULT, fxcL_default }, { FXORDER_RGB, fxcL_rgb }, { FXORDER_RBG, fxcL_rbg },
    { FXORDER_GRB, fxcL_grb },         { FXORDER_GBR, fxcL_gbr }, { FXORDER_BRG, fxcL_brg },
    { FXORDER_BGR, fxcL_bgr },
};

FXC_STR(fxcL_color, "Color");   FXC_STR(fxcL_heat, "Heat");
FXC_STR(fxcL_ocean, "Ocean");   FXC_STR(fxcL_forest, "Forest");
FXC_STR(fxcL_lava, "Lava");     FXC_STR(fxcL_party, "Party");
FXC_STR(fxcL_ice, "Ice");

static const FxEnumOption fxcPaletteOptions[] PROGMEM = {
    { FXPAL_DEFAULT, fxcL_default }, { FXPAL_COLOR, fxcL_color },   { FXPAL_RAINBOW, fxcL_rainbow },
    { FXPAL_HEAT, fxcL_heat },       { FXPAL_OCEAN, fxcL_ocean },   { FXPAL_FOREST, fxcL_forest },
    { FXPAL_LAVA, fxcL_lava },       { FXPAL_PARTY, fxcL_party },   { FXPAL_ICE, fxcL_ice },
#if FX_EXTENDED
    { FXPAL_AURORA, fxcL_aurora },
#endif
};

#define FXC_OPTIONS(array) (uint8_t)(sizeof(array) / sizeof(array[0])), array
#define FXC_NO_OPTIONS 0, NULL

static const FxControlDef fxControlDefs[] PROGMEM = {
    { FXC_ID_BRIGHTNESS,    FXC_TYPE_UINT8,  FXC_FLAG_HARDWARE, 0, 255,
      fxcN_brightness, fxcD_brightness, FXC_NO_OPTIONS, FXC_AT(brightness) },
    { FXC_ID_LOCAL_MODE,    FXC_TYPE_ENUM,   FXC_FLAG_HARDWARE, 0, 255,
      fxcN_local_mode, fxcD_local_mode, FXC_OPTIONS(fxcModeOptions), FXC_AT(mode) },
    { FXC_ID_CYCLE_TIME,    FXC_TYPE_UINT16, FXC_FLAG_HARDWARE, 1, FXC_MAX_CYCLE_TIME,
      fxcN_cycle_time, fxcD_cycle_time, FXC_NO_OPTIONS, FXC_AT(cycleTime) },
    { FXC_ID_NUM_PIXELS,    FXC_TYPE_UINT16, FXC_FLAG_HARDWARE, 1, FXC_MAX_PIXELS,
      fxcN_num_pixels, fxcD_num_pixels, FXC_NO_OPTIONS, FXC_AT(numPixels) },
    { FXC_ID_LED_TYPE,      FXC_TYPE_ENUM,   FXC_FLAG_HARDWARE, 0, FXLED_LPD8806,
      fxcN_led_type, fxcD_led_type, FXC_OPTIONS(fxcLedOptions), FXC_AT(ledType) },
    { FXC_ID_MATRIX_WIDTH,  FXC_TYPE_UINT8,  FXC_FLAG_HARDWARE, 1, FXC_MAX_MATRIX_WIDTH,
      fxcN_matrix_width, fxcD_matrix_width, FXC_NO_OPTIONS, FXC_AT(matrixWidth) },
    { FXC_ID_MATRIX_LAYOUT, FXC_TYPE_ENUM,   FXC_FLAG_HARDWARE, 0, FX_LAYOUT_MASK,
      fxcN_matrix_layout, fxcD_matrix_layout, FXC_OPTIONS(fxcLayoutOptions), FXC_AT(matrixLayout) },
    { FXC_ID_COLOR_ORDER,   FXC_TYPE_ENUM,   FXC_FLAG_HARDWARE, 0, FXORDER_COUNT - 1,
      fxcN_color_order, fxcD_color_order, FXC_OPTIONS(fxcOrderOptions), FXC_AT(colorOrder) },
    { FXC_ID_SPEED,         FXC_TYPE_UINT8,  0, 0, 255,
      fxcN_speed, fxcD_speed, FXC_NO_OPTIONS, FXC_AT(speed) },
    { FXC_ID_SCALE,         FXC_TYPE_UINT8,  0, 1, 255,
      fxcN_scale, fxcD_scale, FXC_NO_OPTIONS, FXC_AT(scale) },
    { FXC_ID_PALETTE,       FXC_TYPE_ENUM,   0, 0, FXPAL_COUNT - 1,
      fxcN_palette, fxcD_palette, FXC_OPTIONS(fxcPaletteOptions), FXC_AT(palette) },
    { FXC_ID_HUE,           FXC_TYPE_UINT8,  0, 0, 255,
      fxcN_hue, fxcD_hue, FXC_NO_OPTIONS, FXC_AT(hue) },
    { FXC_ID_SATURATION,    FXC_TYPE_UINT8,  0, 0, 255,
      fxcN_saturation, fxcD_saturation, FXC_NO_OPTIONS, FXC_AT(saturation) },
    { FXC_ID_INTENSITY,     FXC_TYPE_UINT8,  0, 0, 255,
      fxcN_intensity, fxcD_intensity, FXC_NO_OPTIONS, FXC_AT(intensity) },
    { FXC_ID_COUNT,         FXC_TYPE_UINT8,  0, 1, FXC_MAX_COUNT,
      fxcN_count, fxcD_count, FXC_NO_OPTIONS, FXC_AT(count) },
    { FXC_ID_REVERSE,       FXC_TYPE_BOOL,   0, 0, 1,
      fxcN_reverse, fxcD_reverse, FXC_NO_OPTIONS, FXC_AT(reverse) },
    { FXC_ID_MAX_FPS,       FXC_TYPE_UINT8,  FXC_FLAG_HARDWARE, 1, FXC_MAX_FPS_LIMIT,
      fxcN_max_fps, fxcD_max_fps, FXC_NO_OPTIONS, FXC_AT(maxFps) },
    { FXC_ID_FPS,           FXC_TYPE_UINT16, FXC_FLAG_READONLY | FXC_FLAG_VOLATILE, 0, 32767,
      fxcN_fps, fxcD_fps, FXC_NO_OPTIONS, FXC_NO_STORAGE },
#if FXC_HAS_LOAD
    { FXC_ID_LOAD,          FXC_TYPE_UINT8,  FXC_FLAG_READONLY | FXC_FLAG_VOLATILE, 0, 255,
      fxcN_load, fxcD_load, FXC_NO_OPTIONS, FXC_NO_STORAGE },
#endif
    { FXC_ID_SAVE,          FXC_TYPE_ACTION, FXC_FLAG_HARDWARE | FXC_FLAG_ACTION, 0, 0,
      fxcN_save, fxcD_save, FXC_NO_OPTIONS, FXC_NO_STORAGE },
    { FXC_ID_REBOOT,        FXC_TYPE_ACTION, FXC_FLAG_HARDWARE | FXC_FLAG_ACTION, 0, 0,
      fxcN_reboot, fxcD_reboot, FXC_NO_OPTIONS, FXC_NO_STORAGE },
};

#define FXC_NUM_CONTROLS ((uint8_t)(sizeof(fxControlDefs) / sizeof(fxControlDefs[0])))

// ============================================================================
// TABLE ACCESS
// ============================================================================

// Copy one table row out of flash.
static void fxcReadDef(uint8_t index, FxControlDef& def) {
    memcpy_P(&def, &fxControlDefs[index], sizeof(FxControlDef));
}

// Row index of a control id, or -1.
static int8_t fxcFind(uint8_t id) {
    for (uint8_t i = 0; i < FXC_NUM_CONTROLS; i++) {
        if (pgm_read_byte(&fxControlDefs[i].id) == id) return (int8_t)i;
    }
    return -1;
}

static void fxcReadOption(const FxControlDef& def, uint8_t index, FxEnumOption& option) {
    memcpy_P(&option, &def.options[index], sizeof(FxEnumOption));
}

static bool fxcIsOption(const FxControlDef& def, uint16_t value) {
    for (uint8_t i = 0; i < def.numOptions; i++) {
        FxEnumOption option;
        fxcReadOption(def, i, option);
        if (option.value == value) return true;
    }
    return false;
}

// Bytes a control's value takes on the serial wire.
static uint8_t fxcValueSize(uint8_t type) {
    return type == FXC_TYPE_UINT16 ? 2 : 1;
}

// ============================================================================
// CONFIGURATION
// ============================================================================

static void fxcDefaultsInto(FxConfig& c) {
    memset(&c, 0, sizeof(c));
    c.magic = FXC_MAGIC;
    c.version = FXC_VERSION;
    c.brightness = FXC_DEFAULT_BRIGHTNESS;
    c.mode = FXC_DEFAULT_MODE;
    c.cycleTime = FXC_DEFAULT_CYCLE_TIME;
    c.numPixels = FXC_DEFAULT_NUM_PIXELS;
    c.ledType = FXLED_WS2812;
    c.matrixWidth = 1;
    c.matrixLayout = 0;
    c.colorOrder = FXORDER_DEFAULT;
    c.speed = FXC_DEFAULT_SPEED;
    c.scale = FXC_DEFAULT_SCALE;
    c.palette = FXPAL_DEFAULT;
    c.hue = 0;
    c.saturation = FXC_DEFAULT_SATURATION;
    c.intensity = FXC_DEFAULT_INTENSITY;
    c.count = FXC_DEFAULT_COUNT;
    c.reverse = 0;
    c.maxFps = FXC_DEFAULT_MAX_FPS;
    strncpy(c.name, FXC_DEFAULT_NAME, FXC_NAME_MAXLEN);
    c.name[FXC_NAME_MAXLEN] = '\0';
}

static void fxcDefaults() {
    fxcDefaultsInto(fxConfig);
}

// True if a block read from storage is one of ours.
static bool fxcStoredValid(const FxConfig& stored) {
    return stored.magic == FXC_MAGIC && stored.version == FXC_VERSION;
}

static void fxcSetName(const uint8_t* text, uint16_t length) {
    uint8_t n = 0;
    while (n < length && n < FXC_NAME_MAXLEN) {
        uint8_t c = text[n];
        if (c == 0) break;
        if (c < 0x20) c = '_';  // no control characters in a name
        fxConfig.name[n++] = (char)c;
    }
    fxConfig.name[n] = '\0';
    if (n == 0) {  // an empty name restores the factory default
        strncpy(fxConfig.name, FXC_DEFAULT_NAME, FXC_NAME_MAXLEN);
        fxConfig.name[FXC_NAME_MAXLEN] = '\0';
    }
}

// Hand the geometry to the engine. A width that does not divide the pixel
// count cannot describe a matrix, so it falls back to a strip.
static void fxcApplyGeometry() {
    if (fxConfig.matrixWidth < 1 || fxConfig.numPixels % fxConfig.matrixWidth != 0) {
        fxConfig.matrixWidth = 1;
    }
    fxSetGeometry(fxConfig.numPixels, fxConfig.matrixWidth, fxConfig.matrixLayout);
}

// Force every stored value into its allowed range. Run after loading, so a
// corrupt or foreign block can never put the firmware in a state the controls
// cannot reach.
static void fxcSanitize() {
    FxConfig factory;
    fxcDefaultsInto(factory);
    for (uint8_t i = 0; i < FXC_NUM_CONTROLS; i++) {
        FxControlDef def;
        fxcReadDef(i, def);
        if (def.offset == FXC_NO_STORAGE) continue;
        uint8_t size = fxcValueSize(def.type);
        uint8_t* at = (uint8_t*)&fxConfig + def.offset;
        uint16_t value = 0;
        memcpy(&value, at, size);  // little-endian on every board we build for
        bool ok = def.type == FXC_TYPE_ENUM
            ? fxcIsOption(def, value)
            : (value <= 32767 && (int16_t)value >= def.minVal && (int16_t)value <= def.maxVal);
        if (!ok) memcpy(at, (const uint8_t*)&factory + def.offset, size);
    }
    fxConfig.name[FXC_NAME_MAXLEN] = '\0';
    fxcApplyGeometry();
}

// ============================================================================
// GET AND SET
// ============================================================================

static uint16_t fxcGetDef(const FxControlDef& def) {
    if (def.id == FXC_ID_FPS) return fxRuntime.fps;
#if FXC_HAS_LOAD
    if (def.id == FXC_ID_LOAD) return fxRuntime.load;
#endif
    if (def.offset == FXC_NO_STORAGE) return 0;
    const uint8_t* at = (const uint8_t*)&fxConfig + def.offset;
    if (def.type == FXC_TYPE_UINT16) {
        uint16_t value;
        memcpy(&value, at, 2);
        return value;
    }
    return at[0];
}

// Ask for LEDs up to `count` on chip `ledType` to be turned off with the next
// frame. Used when the strip gets shorter or the output changes.
static void fxcRequestBlank(uint16_t count, uint8_t ledType) {
    if (count > fxRuntime.blankTo) fxRuntime.blankTo = count;
    fxRuntime.blankLedType = ledType;
}

static void fxcRestartCycle() {
    fxRuntime.cycleMs = 0;
    fxRuntime.cycleEffect = FX_OFF + 1;
}

// Set a control. Returns FXC_OK, an FXC_ERR_* code, or FXC_DO_SAVE /
// FXC_DO_REBOOT when the caller has to carry out an action.
static uint8_t fxcSet(uint8_t id, uint16_t value) {
    // The host's serial sink switches these two on when it connects and
    // treats a refusal as a failed connection. They configure pixel
    // streaming, which this firmware does not do, so accept and ignore them.
    if (id == FXC_ID_AUTO_SHOW || id == FXC_ID_FRAME_ACK) return FXC_OK;

    int8_t index = fxcFind(id);
    if (index < 0) return FXC_ERR_UNKNOWN;
    FxControlDef def;
    fxcReadDef((uint8_t)index, def);

    if (def.type == FXC_TYPE_ACTION) {
        return id == FXC_ID_SAVE ? FXC_DO_SAVE : FXC_DO_REBOOT;
    }
    if (def.flags & FXC_FLAG_READONLY) return FXC_ERR_READONLY;

    if (def.type == FXC_TYPE_ENUM) {
        if (!fxcIsOption(def, value)) return FXC_ERR_VALUE;
    } else if (def.type == FXC_TYPE_BOOL) {
        value = value ? 1 : 0;
    } else if (value > 32767 || (int16_t)value < def.minVal || (int16_t)value > def.maxVal) {
        return FXC_ERR_VALUE;
    }

    switch (id) {
        case FXC_ID_MATRIX_WIDTH:
            if (fxConfig.numPixels % value != 0) return FXC_ERR_VALUE;
            break;
        case FXC_ID_NUM_PIXELS:
            if (value < fxConfig.numPixels) fxcRequestBlank(fxConfig.numPixels, fxConfig.ledType);
            break;
        case FXC_ID_LED_TYPE:
            if (value != fxConfig.ledType) fxcRequestBlank(fxConfig.numPixels, fxConfig.ledType);
            break;
        case FXC_ID_LOCAL_MODE:
            fxcRestartCycle();
            break;
        default:
            break;
    }

    uint8_t* at = (uint8_t*)&fxConfig + def.offset;
    if (def.type == FXC_TYPE_UINT16) memcpy(at, &value, 2);
    else at[0] = (uint8_t)value;

    if (id == FXC_ID_NUM_PIXELS || id == FXC_ID_MATRIX_WIDTH || id == FXC_ID_MATRIX_LAYOUT) {
        fxcApplyGeometry();
    }
    return FXC_OK;
}

// ============================================================================
// FRAME HELPERS
// ============================================================================

// The effect on show right now: the mode, or the current step of the cycle.
static uint8_t fxcCurrentEffect() {
    return fxConfig.mode == FX_CYCLE ? fxRuntime.cycleEffect : fxConfig.mode;
}

// Advance the animation clock and the mode cycle by elapsed real time.
static void fxcTick(uint16_t elapsedMs) {
    fxRuntime.fxTime += (uint32_t)elapsedMs * fxRate(fxConfig.speed);
    if (fxConfig.mode != FX_CYCLE) return;
    if (fxRuntime.cycleEffect == FX_OFF || fxRuntime.cycleEffect >= FX_COUNT) fxcRestartCycle();
    fxRuntime.cycleMs += elapsedMs;
    if (fxRuntime.cycleMs >= (uint32_t)fxConfig.cycleTime * 1000) {
        fxRuntime.cycleMs = 0;
        if (++fxRuntime.cycleEffect >= FX_COUNT) fxRuntime.cycleEffect = FX_OFF + 1;
    }
}

// Start a frame of the current effect with the current settings.
static void fxcBeginFrame() {
    FxParams p;
    p.effect = fxcCurrentEffect();
    p.brightness = fxConfig.brightness;
    p.scale = fxConfig.scale;
    p.palette = fxConfig.palette;
    p.hue = fxConfig.hue;
    p.saturation = fxConfig.saturation;
    p.intensity = fxConfig.intensity;
    p.count = fxConfig.count;
    p.reverse = fxConfig.reverse;
    fxBeginFrame(p, fxRuntime.fxTime);
}

// Which of r, g, b goes out first, second and third for the configured
// colour order: order[k] indexes an {r, g, b} triplet.
static void fxcWireOrder(uint8_t ledType, uint8_t order[3]) {
    uint8_t choice = fxConfig.colorOrder;
    if (choice == FXORDER_DEFAULT || choice >= FXORDER_COUNT) {
        // WS2812 and LPD8806 take green first; APA102 takes blue first.
        choice = ledType == FXLED_APA102 ? FXORDER_BGR : FXORDER_GRB;
    }
    static const uint8_t table[FXORDER_COUNT][3] PROGMEM = {
        { 1, 0, 2 },  // placeholder for FXORDER_DEFAULT, never used
        { 0, 1, 2 }, { 0, 2, 1 }, { 1, 0, 2 }, { 1, 2, 0 }, { 2, 0, 1 }, { 2, 1, 0 },
    };
    for (uint8_t k = 0; k < 3; k++) order[k] = pgm_read_byte(&table[choice][k]);
}

#endif // LTP_FX_CONTROLS_H
