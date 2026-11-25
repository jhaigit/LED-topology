// One rendering job for the LtpStreamFx engine, shared by the host harness
// (fx_harness.cpp) and the bare-metal one run in a CPU emulator (fx_bare.cpp).
//
// A job is fifteen numbers:
//   numPixels width layout effect brightness scale palette hue saturation
//   intensity count reverse fxTime0 fxTimeStep frames
// For each frame it emits numPixels RGB triplets in wire order (fxNext), then
// numPixels triplets in logical order (fxAt).

#ifndef FX_JOB_H
#define FX_JOB_H

#include <stdint.h>

#include "ltp_fx.h"

#define FX_JOB_FIELDS 15

template <class Emit>
void fxRunJob(const uint32_t a[FX_JOB_FIELDS], Emit emit) {
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

    uint32_t fxTime = a[12];
    for (uint32_t f = 0; f < a[14]; f++, fxTime += a[13]) {
        uint8_t rgb[3];
        fxBeginFrame(p, fxTime);
        fxStart();
        for (uint16_t i = 0; i < numPixels; i++) {
            fxNext(rgb);
            emit(rgb);
        }
        for (uint16_t i = 0; i < numPixels; i++) {
            fxAt(i, rgb);
            emit(rgb);
        }
    }
}

#endif // FX_JOB_H
