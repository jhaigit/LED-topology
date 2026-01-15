"""Command-line interface for LTP Serial Sink (v2 protocol)."""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

import yaml

from libltp import ColorFormat, DeviceType
from ltp_serial_sink.sink import SerialSink, SerialSinkConfig
from ltp_serial_sink.v2_renderer import V2Renderer, V2RendererConfig


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="ltp-serial-sink",
        description="LTP Serial Sink - LED data sink using v2 binary protocol",
    )

    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        help="Path to YAML configuration file",
    )
    parser.add_argument(
        "--name",
        "-n",
        type=str,
        default="Serial LED Strip",
        help="Device name (default: Serial LED Strip)",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=str,
        help="Serial port path (required unless using config file)",
    )
    parser.add_argument(
        "--baudrate",
        "-b",
        type=int,
        default=115200,
        help="Baud rate (default: 115200)",
    )
    parser.add_argument(
        "--pixels",
        type=int,
        default=0,
        help="Number of pixels (default: auto-detect from device)",
    )
    parser.add_argument(
        "--dimensions",
        "-d",
        type=str,
        help="Dimensions (e.g., '160' or '16x10')",
    )
    parser.add_argument(
        "--color-format",
        type=str,
        choices=["rgb", "rgbw"],
        default="rgb",
        help="Color format (default: rgb)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="Serial timeout in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["debug", "info", "warning", "error"],
        default="info",
        help="Logging level (default: info)",
    )
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List available serial ports and exit",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test serial connection and exit",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging (same as --log-level debug)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show serial protocol packets sent/received",
    )

    return parser.parse_args()


def load_config(path: Path) -> SerialSinkConfig:
    """Load configuration from YAML file."""
    with open(path) as f:
        data = yaml.safe_load(f)

    config_dict = {}

    if "device" in data:
        device = data["device"]
        if "id" in device and device["id"] != "auto":
            from uuid import UUID
            config_dict["device_id"] = UUID(device["id"])
        if "name" in device:
            config_dict["name"] = device["name"]
        if "description" in device:
            config_dict["description"] = device["description"]

    if "display" in data:
        display = data["display"]
        if "type" in display:
            config_dict["device_type"] = DeviceType(display["type"])
        if "pixels" in display:
            config_dict["pixels"] = display["pixels"]
        if "dimensions" in display:
            config_dict["dimensions"] = display["dimensions"]
        if "color_format" in display:
            config_dict["color_format"] = ColorFormat[display["color_format"].upper()]
        if "max_refresh_hz" in display:
            config_dict["max_refresh_hz"] = display["max_refresh_hz"]

    if "serial" in data:
        serial = data["serial"]
        if "port" in serial:
            config_dict["port"] = serial["port"]
        if "baudrate" in serial:
            config_dict["baudrate"] = serial["baudrate"]
        if "baud" in serial:  # Legacy support
            config_dict["baudrate"] = serial["baud"]
        if "timeout" in serial:
            config_dict["timeout"] = serial["timeout"]
        if "debug" in serial:
            config_dict["debug"] = serial["debug"]

    return SerialSinkConfig(**config_dict)


def config_from_args(args: argparse.Namespace) -> SerialSinkConfig:
    """Create configuration from command line arguments."""
    # Parse dimensions
    pixels = args.pixels
    dimensions = []

    if args.dimensions:
        parts = args.dimensions.lower().split("x")
        dimensions = [int(p) for p in parts]
        if len(dimensions) > 1:
            pixels = dimensions[0] * dimensions[1]
        else:
            pixels = dimensions[0]
    elif pixels > 0:
        dimensions = [pixels]

    # Map color format
    color_map = {
        "rgb": ColorFormat.RGB,
        "rgbw": ColorFormat.RGBW,
    }

    return SerialSinkConfig(
        name=args.name,
        port=args.port or "",
        baudrate=args.baudrate,
        timeout=args.timeout,
        pixels=pixels,
        dimensions=dimensions,
        color_format=color_map.get(args.color_format, ColorFormat.RGB),
        debug=args.debug,
    )


def list_ports() -> None:
    """Print available serial ports."""
    ports = V2Renderer.list_ports()

    if not ports:
        print("No serial ports found.")
        return

    print("Available serial ports:")
    print()
    for port in ports:
        print(f"  {port['device']}")
        if port["description"] and port["description"] != port["device"]:
            print(f"    Description: {port['description']}")
        if port["hwid"] and port["hwid"] != "n/a":
            print(f"    Hardware ID: {port['hwid']}")
        print()


def test_connection(config: SerialSinkConfig) -> bool:
    """Test serial connection with v2 protocol."""
    print(f"Testing serial connection to {config.port} at {config.baudrate} baud...")
    print(f"Using LTP Serial Protocol v2")
    print()

    renderer_config = V2RendererConfig(
        port=config.port,
        baudrate=config.baudrate,
        timeout=config.timeout,
        debug=config.debug,
        debug_file=sys.stderr,
    )

    renderer = V2Renderer(renderer_config)

    try:
        renderer.open()
        print("  Port opened successfully")
    except Exception as e:
        print(f"  ERROR: Could not open port: {e}")
        return False

    # Show device info
    device_info = renderer.device_info
    if device_info:
        print(f"  Device: {device_info.device_name or 'Unknown'}")
        print(f"  Firmware: v{device_info.firmware_version}")
        print(f"  Pixels: {device_info.total_pixels}")
        print(f"  Strips: {device_info.strip_count}")
        print()

        # Show capabilities
        print("  Capabilities:")
        print(f"    Brightness: {device_info.has_brightness}")
        print(f"    Gamma: {device_info.has_gamma}")
        print(f"    RLE: {device_info.has_rle}")
        print()

    # Send test pattern
    print("  Sending test pattern...")
    pixel_count = renderer.pixel_count or 160

    # Red, green, blue sections
    renderer.fill(255, 0, 0)  # All red first
    time.sleep(0.5)
    renderer.fill(0, 255, 0)  # All green
    time.sleep(0.5)
    renderer.fill(0, 0, 255)  # All blue
    time.sleep(0.5)

    print()
    print("  Pattern sent. Check your LED strip for red/green/blue sequence.")
    print()

    # Clear after a moment
    time.sleep(1)
    print("  Clearing strip...")
    renderer.fill(0, 0, 0)

    renderer.close()
    print()
    print("SUCCESS: Serial connection working with v2 protocol")
    return True


async def run_sink(config: SerialSinkConfig) -> None:
    """Run the sink."""
    sink = SerialSink(config)

    try:
        await sink.run()
    except KeyboardInterrupt:
        pass
    finally:
        await sink.stop()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Set up logging
    log_level_str = "debug" if args.verbose else args.log_level
    log_level = getattr(logging, log_level_str.upper())
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Handle special commands
    if args.list_ports:
        list_ports()
        return 0

    # Load configuration
    if args.config:
        if not args.config.exists():
            print(f"Error: Config file not found: {args.config}", file=sys.stderr)
            return 1
        config = load_config(args.config)
        # Override debug from command line
        if args.debug:
            config = SerialSinkConfig(**{**config.model_dump(), "debug": True})
    else:
        config = config_from_args(args)

    # Validate port is specified
    if not config.port:
        print("Error: Serial port required. Use --port or specify in config file.", file=sys.stderr)
        print("Use --list-ports to see available ports.", file=sys.stderr)
        return 1

    # Test mode
    if args.test:
        return 0 if test_connection(config) else 1

    # Print startup info
    print(f"Starting LTP Serial Sink: {config.name}")
    print(f"  Protocol: v2 (binary)")
    print(f"  Pixels: {config.pixels if config.pixels > 0 else 'auto-detect'}")
    if config.dimensions:
        print(f"  Dimensions: {config.dimensions}")
    print(f"  Serial port: {config.port}")
    print(f"  Baud rate: {config.baudrate}")
    print(f"  Debug packets: {config.debug}")
    print()

    # Run
    try:
        asyncio.run(run_sink(config))
    except KeyboardInterrupt:
        print("\nShutdown requested")

    return 0


if __name__ == "__main__":
    sys.exit(main())
