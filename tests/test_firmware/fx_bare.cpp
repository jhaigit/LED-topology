// Bare-metal entry to the LtpStreamFx engine, for running the engine as the
// Teensy compiler builds it inside a CPU emulator (arm_emu.py). No startup
// code and no C library calls of its own: the emulator loads the image,
// puts the job and an output buffer in memory and calls fx_bare_run.

#include "fx_job.h"

extern "C" uint32_t fx_bare_run(const uint32_t* job, uint8_t* out) {
    uint8_t* at = out;
    fxRunJob(job, [&at](const uint8_t rgb[3]) {
        at[0] = rgb[0];
        at[1] = rgb[1];
        at[2] = rgb[2];
        at += 3;
    });
    return (uint32_t)(at - out);
}
