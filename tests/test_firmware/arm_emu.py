"""Run the LtpStreamFx engine, as the Teensy compiler builds it, in a CPU emulator.

There is no simulator for the Teensy boards in this repository, but the
pattern engine has no hardware dependency: it can be compiled with the
board's own compiler and flags and executed instruction by instruction in
Unicorn (a CPU emulator, `pip install unicorn`). That shows two things the
host build cannot:

- the frames an ARM build produces, to compare with the host build's;
- how many instructions each pixel takes, the nearest thing to a timing
  measurement that is possible without the board.

Unicorn executes instructions; it does not model how long they take. Counts
here are instructions, not CPU cycles.
"""

import glob
import os
import struct
import subprocess
from bisect import bisect_left
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "arduino" / "libraries" / "LtpStreamFx" / "src"
BARE_SRC = Path(__file__).with_name("fx_bare.cpp")

# Compiler flags of the two Teensy builds, from the core's boards.txt
# (optimisation "Faster", the default).
BOARDS = {
    "teensy40": ["-mthumb", "-mcpu=cortex-m7", "-mfloat-abi=hard", "-mfpu=fpv5-d16",
                 "-std=gnu++17", "-D__IMXRT1062__"],
    "teensy32": ["-mthumb", "-mcpu=cortex-m4", "-fsingle-precision-constant",
                 "-std=gnu++14", "-D__MK20DX256__"],
}
COMMON_FLAGS = ["-O2", "-fno-exceptions", "-fno-rtti", "-ffunction-sections", "-fdata-sections",
                "-Wall", "-Wextra", "-Werror"]

# Where the emulator puts what the image does not bring itself. A Cortex-M
# may execute only below 0x40000000, so the return address is down there too.
STACK_BASE, STACK_SIZE = 0x20000000, 0x10000
IO_BASE = 0x30000000          # the job, then the output buffer
OUT_OFFSET = 0x100
RETURN_ADDRESS = 0x10000000   # fx_bare_run returns here, which ends the run
PAGE = 0x1000

JOB_FIELDS = 15


def find_compiler():
    """The Teensy core's arm-none-eabi-g++, or None."""
    data_dir = os.environ.get("ARDUINO_DIRECTORIES_DATA", str(Path.home() / ".arduino15"))
    found = sorted(glob.glob(f"{data_dir}/packages/teensy/tools/teensy-compile/*/arm/bin/arm-none-eabi-g++"))
    return found[-1] if found else None


def build(board, out_dir, extra_flags=()):
    """Compile the engine and fx_bare.cpp for `board`; returns the ELF path."""
    gxx = find_compiler()
    elf = Path(out_dir) / f"fx_{board}.elf"
    subprocess.run(
        [gxx, *COMMON_FLAGS, *BOARDS[board], *extra_flags, f"-I{LIB}",
         "-nostartfiles", "-Wl,-e,fx_bare_run", "-Wl,--gc-sections",
         str(BARE_SRC), str(LIB / "ltp_fx.cpp"), "-o", str(elf)],
        check=True,
    )
    return elf


def _tool(name):
    return str(Path(find_compiler()).with_name(f"arm-none-eabi-{name}"))


class Image:
    """A loaded ELF image and the addresses of its functions."""

    def __init__(self, elf):
        self.elf = Path(elf)
        self.segments = []  # (address, file bytes, size in memory)
        data = self.elf.read_bytes()
        assert data[:4] == b"\x7fELF" and data[4] == 1, "not a 32-bit ELF file"
        phoff, = struct.unpack_from("<I", data, 28)
        phentsize, phnum = struct.unpack_from("<HH", data, 42)
        for i in range(phnum):
            p_type, offset, vaddr, _, filesz, memsz = struct.unpack_from("<6I", data, phoff + i * phentsize)
            if p_type == 1:  # PT_LOAD
                self.segments.append((vaddr, data[offset:offset + filesz], memsz))

        self.symbols = {}
        for line in subprocess.run([_tool("nm"), "-C", str(self.elf)], check=True,
                                   capture_output=True, text=True).stdout.splitlines():
            address, kind, name = line.split(maxsplit=2)
            if kind in "Tt":
                self.symbols[name.split("(")[0]] = int(address, 16) & ~1

        # Address and mnemonic of every instruction, for counting by block.
        self.instructions = []
        self.mnemonics = []
        for line in subprocess.run([_tool("objdump"), "-d", "--no-show-raw-insn", str(self.elf)],
                                   check=True, capture_output=True, text=True).stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].strip().endswith(":"):
                try:
                    address = int(parts[0].strip()[:-1], 16)
                except ValueError:
                    continue
                mnemonic = parts[1].split()[0] if parts[1].split() else ""
                if mnemonic.startswith("."):  # literal pool data, not code
                    continue
                self.instructions.append(address)
                self.mnemonics.append(mnemonic)

        self._blocks = {}

    def count(self, start, size):
        """Instructions, and divide instructions among them, in [start, start + size)."""
        key = (start, size)
        found = self._blocks.get(key)
        if found is None:
            lo = bisect_left(self.instructions, start)
            hi = bisect_left(self.instructions, start + size)
            divides = sum(1 for m in self.mnemonics[lo:hi] if m.startswith(("udiv", "sdiv")))
            found = self._blocks[key] = (hi - lo, divides)
        return found


def run(image, cpu, job, *, count=False):
    """Run one job. Returns (output bytes, per-pixel counts or None).

    With count=True the second value is a list with one entry per fxNext()
    call: (instructions, divides) executed from the start of that call to the
    start of the next one - the pixel itself plus the few instructions of the
    loop around it. Calls into the compiler's 64-bit divide helper are counted
    as divides too.
    """
    from unicorn import Uc, UC_ARCH_ARM, UC_HOOK_BLOCK, UC_MODE_MCLASS, UC_MODE_THUMB
    from unicorn import arm_const as arm

    assert len(job) == JOB_FIELDS
    num_pixels, frames = job[0], job[14]
    out_size = frames * 2 * num_pixels * 3

    uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    uc.ctl_set_cpu_model({"cortex-m7": arm.UC_CPU_ARM_CORTEX_M7,
                          "cortex-m4": arm.UC_CPU_ARM_CORTEX_M4}[cpu])

    def map_region(base, size):
        start = base & ~(PAGE - 1)
        end = (base + size + PAGE - 1) & ~(PAGE - 1)
        uc.mem_map(start, end - start)

    spans = sorted((addr & ~(PAGE - 1), (addr + memsz + PAGE - 1) & ~(PAGE - 1))
                   for addr, _, memsz in image.segments)
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    for start, end in merged:
        uc.mem_map(start, end - start)
    for addr, content, _ in image.segments:
        uc.mem_write(addr, content)  # the rest of each segment (.bss) is already zero

    map_region(STACK_BASE, STACK_SIZE)
    map_region(IO_BASE, OUT_OFFSET + out_size)
    map_region(RETURN_ADDRESS, PAGE)
    uc.mem_write(IO_BASE, struct.pack(f"<{JOB_FIELDS}I", *job))

    uc.reg_write(arm.UC_ARM_REG_SP, STACK_BASE + STACK_SIZE - 16)
    uc.reg_write(arm.UC_ARM_REG_LR, RETURN_ADDRESS | 1)
    uc.reg_write(arm.UC_ARM_REG_R0, IO_BASE)
    uc.reg_write(arm.UC_ARM_REG_R1, IO_BASE + OUT_OFFSET)

    pixels = None
    if count:
        fx_next = image.symbols["fxNext"]
        divide_helpers = {a for n, a in image.symbols.items() if n in ("__aeabi_uldivmod", "__aeabi_ldivmod")}
        pixels = []
        state = {"n": 0, "d": 0, "open": False}

        def on_block(_uc, address, size, _user):
            if address == fx_next:
                if state["open"]:
                    pixels.append((state["n"], state["d"]))
                state["n"] = state["d"] = 0
                state["open"] = True
            n, d = image.count(address, size)
            state["n"] += n
            state["d"] += d + (1 if address in divide_helpers else 0)

        uc.hook_add(UC_HOOK_BLOCK, on_block)

    uc.emu_start(image.symbols["fx_bare_run"] | 1, RETURN_ADDRESS)

    written = uc.reg_read(arm.UC_ARM_REG_R0)
    assert written == out_size, f"engine wrote {written} bytes, expected {out_size}"
    out = bytes(uc.mem_read(IO_BASE + OUT_OFFSET, out_size))
    if count:
        # Keep the fxNext passes of each frame; the last entry of a pass also
        # holds the fxAt pass that follows it, so drop it.
        per_frame = []
        for f in range(frames):
            per_frame.extend(pixels[f * num_pixels: f * num_pixels + num_pixels - 1])
        pixels = per_frame
    return out, pixels


# ============================================================================
# How heavy is each pattern?
# ============================================================================

EFFECT_NAMES = {
    1: "Scanner", 2: "Rainbow", 3: "Fire", 4: "Sparkle", 5: "Chase", 6: "Solid", 7: "Comet",
    8: "Waves", 9: "Twinkle", 10: "Noise", 11: "Plasma", 12: "Ripple", 13: "Fireworks",
    14: "Bounce", 15: "Meteors", 16: "Lava Lamp", 17: "Aurora", 18: "Raindrops",
}

# A WS2812 pixel is 30 us on the wire: 18 000 CPU cycles at the Teensy 4.0's
# 600 MHz. That is what a pattern may use per pixel, on average.
TEENSY40_CYCLES_PER_PIXEL = 18000

# Instructions to cycles. The Cortex-M7 runs typical code at about one cycle
# per instruction; two is a deliberately pessimistic figure that leaves room
# for mispredicted branches and for the transmit interrupts. A hardware
# divide takes up to 12 cycles.
PESSIMISTIC_CYCLES_PER_INSTRUCTION = 2
CYCLES_PER_DIVIDE = 12

# The settings that make the patterns work hardest: the most objects, the
# longest tails and largest sizes (small scale), and each default.
HEAVY_SETTINGS = [
    {"count": 8, "intensity": 255, "scale": 8},
    {"count": 8, "intensity": 255, "scale": 32},
    {"count": 8, "intensity": 128, "scale": 128},
    {"count": 2, "intensity": 128, "scale": 32},
]
MEASURE_GEOMETRIES = {"strip": (150, 1), "matrix": (256, 16)}
MEASURE_FRAMES = 8
MEASURE_STEP_MS = 211   # not a divisor of any pattern's period, so frames catch different moments
MEASURE_START_MS = 20000
TIME_PER_MS = 256


def measure(image, cpu, effect, geometry, settings_list=None, frames=None):
    """The heaviest frame of `effect` over HEAVY_SETTINGS (or `settings_list`).

    Returns a dict: instructions and divides per pixel averaged over that
    frame, the costliest single pixel seen, and the settings that did it.
    """
    n, width = MEASURE_GEOMETRIES[geometry]
    worst = None
    worst_pixel = 0
    frames = frames or MEASURE_FRAMES
    for settings in settings_list or HEAVY_SETTINGS:
        job = [n, width, 1, effect, 255, settings["scale"], 0, 0, 255, settings["intensity"],
               settings["count"], 0, MEASURE_START_MS * TIME_PER_MS, MEASURE_STEP_MS * TIME_PER_MS,
               frames]
        _, pixels = run(image, cpu, job, count=True)
        per_frame = n - 1
        for f in range(frames):
            frame = pixels[f * per_frame:(f + 1) * per_frame]
            worst_pixel = max(worst_pixel, max(i for i, _ in frame))
            instructions = sum(i for i, _ in frame) / len(frame)
            if worst is None or instructions > worst["instructions"]:
                worst = {"instructions": instructions,
                         "divides": sum(d for _, d in frame) / len(frame),
                         "settings": settings}
    worst["worst_pixel"] = worst_pixel
    return worst


def pessimistic_cycles(result):
    return (result["instructions"] * PESSIMISTIC_CYCLES_PER_INSTRUCTION
            + result["divides"] * CYCLES_PER_DIVIDE)


def main():
    """Print the table: python tests/test_firmware/arm_emu.py"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        image = Image(build("teensy40", tmp))
        print("LtpStreamFx built for the Teensy 4.0, run in a CPU emulator.")
        print("Heaviest frame of each pattern; instructions per pixel, not cycles.")
        print(f"Budget: {TEENSY40_CYCLES_PER_PIXEL} cycles per WS2812 pixel.\n")
        print(f"{'pattern':10} {'on':7} {'instr/px':>9} {'div/px':>7} {'worst px':>9} "
              f"{'cycles, pessimistic':>20} {'of budget':>10}   heaviest at")
        for effect, name in EFFECT_NAMES.items():
            for geometry in MEASURE_GEOMETRIES:
                r = measure(image, "cortex-m7", effect, geometry)
                cycles = pessimistic_cycles(r)
                s = r["settings"]
                print(f"{name:10} {geometry:7} {r['instructions']:9.0f} {r['divides']:7.1f} "
                      f"{r['worst_pixel']:9d} {cycles:20.0f} "
                      f"{100 * cycles / TEENSY40_CYCLES_PER_PIXEL:9.1f}%   "
                      f"count={s['count']} intensity={s['intensity']} scale={s['scale']}",
                      flush=True)


if __name__ == "__main__":
    main()
