// Host-side harness for the LtpStreamFx engine (arduino/libraries/LtpStreamFx).
//
// Renders frames with the same source the firmware compiles and writes them
// to stdout as raw bytes. For each frame: numPixels RGB triplets in wire
// order (fxNext), then numPixels triplets in logical order (fxAt).
//
// Usage: fx_harness numPixels width layout effect brightness scale palette
//                   hue saturation intensity count reverse fxTime0 fxTimeStep frames

#include <cstdio>
#include <cstdlib>
#include <cstdint>

#include "ltp_fx.h"

int main(int argc, char** argv) {
    if (argc != 16) {
        std::fprintf(stderr, "expected 15 arguments, got %d\n", argc - 1);
        return 2;
    }
    unsigned long a[15];
    for (int i = 0; i < 15; i++) a[i] = std::strtoul(argv[i + 1], nullptr, 0);

    uint16_t numPixels = (uint16_t)a[0];
    fxSetGeometry(numPixels, (uint16_t)a[1], (uint8_t)a[2]);

    FxParams p;
    p.effect = (uint8_t)a[3];
    p.brightness = (uint8_t)a[4];
    p.scale = (uint8_t)a[5];
    p.palette = (uint8_t)a[6];
    p.hue = (uint8_t)a[7];
    p.saturation = (uint8_t)a[8];
    p.intensity = (uint8_t)a[9];
    p.count = (uint8_t)a[10];
    p.reverse = (uint8_t)a[11];

    uint32_t fxTime = (uint32_t)a[12];
    uint32_t step = (uint32_t)a[13];
    for (unsigned long f = 0; f < a[14]; f++, fxTime += step) {
        uint8_t rgb[3];
        fxBeginFrame(p, fxTime);
        fxStart();
        for (uint16_t i = 0; i < numPixels; i++) {
            fxNext(rgb);
            std::fwrite(rgb, 1, 3, stdout);
        }
        for (uint16_t i = 0; i < numPixels; i++) {
            fxAt(i, rgb);
            std::fwrite(rgb, 1, 3, stdout);
        }
    }
    return 0;
}
