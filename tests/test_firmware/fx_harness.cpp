// Host-side harness for the LtpStreamFx engine (arduino/libraries/LtpStreamFx).
//
// Renders frames with the same source the firmware compiles and writes them
// to stdout as raw bytes; fx_job.h says what a job is and what comes out.
//
// Usage: fx_harness numPixels width layout effect brightness scale palette
//                   hue saturation intensity count reverse fxTime0 fxTimeStep frames

#include <cstdio>
#include <cstdlib>
#include <cstdint>

#include "fx_job.h"

int main(int argc, char** argv) {
    if (argc != FX_JOB_FIELDS + 1) {
        std::fprintf(stderr, "expected %d arguments, got %d\n", FX_JOB_FIELDS, argc - 1);
        return 2;
    }
    uint32_t job[FX_JOB_FIELDS];
    for (int i = 0; i < FX_JOB_FIELDS; i++) job[i] = (uint32_t)std::strtoul(argv[i + 1], nullptr, 0);
    fxRunJob(job, [](const uint8_t rgb[3]) { std::fwrite(rgb, 1, 3, stdout); });
    return 0;
}
