"""
LTP Serial Protocol v2 - Command Line Interface

Usage:
    python -m ltp_sink_serial /dev/ttyUSB0 info
    python -m ltp_sink_serial /dev/ttyUSB0 fill 255 0 0
    python -m ltp_sink_serial /dev/ttyUSB0 clear
    python -m ltp_sink_serial /dev/ttyUSB0 brightness 128
"""

import argparse
import sys
import time

from .device import LtpDevice
from .exceptions import LtpError


def cmd_info(device: LtpDevice, args: argparse.Namespace):
    """Show device information."""
    info = device.info
    if not info:
        print("No device info available")
        return

    print(f"Device: {info.device_name or 'Unknown'}")
    print(f"Protocol: v{info.protocol_version}")
    print(f"Firmware: v{info.firmware_version}")
    print(f"Strips: {info.strip_count}")
    print(f"Total Pixels: {info.total_pixels}")
    print(f"Controls: {info.control_count}")
    print(f"Inputs: {info.input_count}")
    print(f"Capabilities:")
    print(f"  Brightness: {info.has_brightness}")
    print(f"  Gamma: {info.has_gamma}")
    print(f"  RLE: {info.has_rle}")
    print(f"  USB High-Speed: {info.is_usb_highspeed}")

    if info.strips:
        print(f"\nStrips:")
        for strip in info.strips:
            print(f"  [{strip.strip_id}] {strip.pixel_count} pixels, {strip.led_type_name}, {strip.color_format_name}")
            print(f"      Pins: data={strip.data_pin}, clock={strip.clock_pin}")


def cmd_status(device: LtpDevice, args: argparse.Namespace):
    """Show device status."""
    status = device.get_status()
    print(f"State: {status.state_name}")
    print(f"Brightness: {status.brightness}")
    if status.temperature is not None:
        print(f"Temperature: {status.temperature:.1f}°C")
    if status.voltage is not None:
        print(f"Voltage: {status.voltage:.2f}V")
    if status.error_code:
        print(f"Error: 0x{status.error_code:02X}")


def cmd_stats(device: LtpDevice, args: argparse.Namespace):
    """Show device statistics."""
    stats = device.get_stats()
    print(f"Frames Received: {stats.frames_received}")
    print(f"Frames Displayed: {stats.frames_displayed}")
    print(f"Bytes Received: {stats.bytes_received}")
    print(f"Checksum Errors: {stats.checksum_errors}")
    print(f"Buffer Overflows: {stats.buffer_overflows}")

    uptime = stats.uptime_seconds
    hours = uptime // 3600
    minutes = (uptime % 3600) // 60
    seconds = uptime % 60
    print(f"Uptime: {hours}h {minutes}m {seconds}s")


def cmd_fill(device: LtpDevice, args: argparse.Namespace):
    """Fill all pixels with a color."""
    device.fill(args.r, args.g, args.b)
    device.show()
    print(f"Filled with RGB({args.r}, {args.g}, {args.b})")


def cmd_clear(device: LtpDevice, args: argparse.Namespace):
    """Clear all pixels."""
    device.clear()
    device.show()
    print("Cleared")


def cmd_brightness(device: LtpDevice, args: argparse.Namespace):
    """Set brightness."""
    device.set_brightness(args.value)
    print(f"Brightness set to {args.value}")


def cmd_rainbow(device: LtpDevice, args: argparse.Namespace):
    """Display a rainbow pattern."""
    num_pixels = device.pixel_count or 160
    pixels = bytearray(num_pixels * 3)

    for i in range(num_pixels):
        hue = (i * 256) // num_pixels
        # Simple HSV to RGB (hue only, full sat/val)
        if hue < 43:
            r, g, b = 255, hue * 6, 0
        elif hue < 85:
            r, g, b = 255 - (hue - 43) * 6, 255, 0
        elif hue < 128:
            r, g, b = 0, 255, (hue - 85) * 6
        elif hue < 170:
            r, g, b = 0, 255 - (hue - 128) * 6, 255
        elif hue < 213:
            r, g, b = (hue - 170) * 6, 0, 255
        else:
            r, g, b = 255, 0, 255 - (hue - 213) * 6

        pixels[i * 3] = min(255, max(0, int(r)))
        pixels[i * 3 + 1] = min(255, max(0, int(g)))
        pixels[i * 3 + 2] = min(255, max(0, int(b)))

    device.set_pixels(bytes(pixels))
    device.show()
    print(f"Rainbow pattern on {num_pixels} pixels")


def cmd_chase(device: LtpDevice, args: argparse.Namespace):
    """Run a chase animation."""
    num_pixels = device.pixel_count or 160
    chase_len = min(10, num_pixels // 4)

    print(f"Running chase animation on {num_pixels} pixels (Ctrl+C to stop)...")

    try:
        pos = 0
        while True:
            device.clear()
            for i in range(chase_len):
                idx = (pos + i) % num_pixels
                brightness = 255 - (i * 255 // chase_len)
                device.fill_range(idx, idx + 1, args.r * brightness // 255,
                                  args.g * brightness // 255,
                                  args.b * brightness // 255)
            device.show()
            pos = (pos + 1) % num_pixels
            time.sleep(0.03)
    except KeyboardInterrupt:
        device.clear()
        device.show()
        print("\nStopped")


def cmd_ping(device: LtpDevice, args: argparse.Namespace):
    """Ping the device."""
    start = time.time()
    if device.ping():
        elapsed = (time.time() - start) * 1000
        print(f"Pong! ({elapsed:.1f}ms)")
    else:
        print("No response")


def cmd_read(device: LtpDevice, args: argparse.Namespace):
    """Read pixel values."""
    data = device.get_pixels(args.start, args.count)
    num_pixels = len(data) // 3

    print(f"Read {num_pixels} pixels starting at {args.start}:")
    for i in range(min(num_pixels, 20)):  # Limit output
        r, g, b = data[i * 3], data[i * 3 + 1], data[i * 3 + 2]
        print(f"  [{args.start + i:3d}] RGB({r:3d}, {g:3d}, {b:3d})")

    if num_pixels > 20:
        print(f"  ... ({num_pixels - 20} more)")


def main():
    parser = argparse.ArgumentParser(
        description="LTP Serial Protocol v2 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("port", help="Serial port (e.g., /dev/ttyUSB0, COM3)")
    parser.add_argument("-b", "--baudrate", type=int, default=115200, help="Baud rate")
    parser.add_argument("-t", "--timeout", type=float, default=2.0, help="Timeout (seconds)")

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # info
    subparsers.add_parser("info", help="Show device information")

    # status
    subparsers.add_parser("status", help="Show device status")

    # stats
    subparsers.add_parser("stats", help="Show device statistics")

    # fill
    p = subparsers.add_parser("fill", help="Fill all pixels with a color")
    p.add_argument("r", type=int, help="Red (0-255)")
    p.add_argument("g", type=int, help="Green (0-255)")
    p.add_argument("b", type=int, help="Blue (0-255)")

    # clear
    subparsers.add_parser("clear", help="Clear all pixels")

    # brightness
    p = subparsers.add_parser("brightness", help="Set brightness")
    p.add_argument("value", type=int, help="Brightness (0-255)")

    # rainbow
    subparsers.add_parser("rainbow", help="Display rainbow pattern")

    # chase
    p = subparsers.add_parser("chase", help="Run chase animation")
    p.add_argument("-r", type=int, default=255, help="Red (0-255)")
    p.add_argument("-g", type=int, default=0, help="Green (0-255)")
    p.add_argument("-b", type=int, default=0, help="Blue (0-255)")

    # ping
    subparsers.add_parser("ping", help="Ping the device")

    # read
    p = subparsers.add_parser("read", help="Read pixel values")
    p.add_argument("-s", "--start", type=int, default=0, help="Start index")
    p.add_argument("-c", "--count", type=int, default=10, help="Number of pixels")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Map commands to handlers
    handlers = {
        "info": cmd_info,
        "status": cmd_status,
        "stats": cmd_stats,
        "fill": cmd_fill,
        "clear": cmd_clear,
        "brightness": cmd_brightness,
        "rainbow": cmd_rainbow,
        "chase": cmd_chase,
        "ping": cmd_ping,
        "read": cmd_read,
    }

    try:
        with LtpDevice(args.port, args.baudrate, args.timeout) as device:
            handlers[args.command](device, args)
    except LtpError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
