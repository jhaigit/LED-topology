"""Build the LtpStreamFx engine for the host, as the tests of it need."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "arduino" / "libraries" / "LtpStreamFx" / "src"
HARNESS_SRC = Path(__file__).with_name("fx_harness.cpp")


def build_harness(directory, extended):
    """Compile fx_harness.cpp with the engine; `extended` adds the FX_EXTENDED patterns.

    Built with the undefined-behaviour sanitizer set to abort, so integer
    overflow or a bad shift in a pattern fails the test that reaches it.
    """
    exe = Path(directory) / ("fx_harness_extended" if extended else "fx_harness")
    subprocess.run(
        ["g++", "-std=gnu++11", "-O1", "-Wall", "-Wextra", "-Werror",
         "-fsanitize=undefined", "-fno-sanitize-recover=undefined",
         f"-DFX_EXTENDED={1 if extended else 0}",
         f"-I{LIB}", str(HARNESS_SRC), str(LIB / "ltp_fx.cpp"), "-o", str(exe)],
        check=True,
    )
    return exe


def run_job(exe, job):
    """Raw output of one job (see fx_job.h for what a job is)."""
    return subprocess.run([str(exe), *map(str, job)], check=True, capture_output=True).stdout
