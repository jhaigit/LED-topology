"""Command-line interface for LTP Serial Sink."""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

import yaml

from libltp import ColorFormat, DeviceType
from ltp_serial_sink.sink import SerialSink, SerialSinkConfig
from ltp_serial_sink.serial_renderer import SerialRenderer, SerialConfig


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="ltp-serial-sink",
        description="LTP Serial Sink - LED data sink with serial output backend",
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
        "--baud",
        "-b",
        type=int,
        default=38400,
        help="Baud rate (default: 38400)",
    )
    parser.add_argument(
        "--pixels",
        type=int,
        default=160,
        help="Number of pixels (default: 160)",
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
        "--hex-format",
        type=str,
        choices=["0x", "#"],
        default="0x",
        help="Output hex format (default: 0x)",
    )
    parser.add_argument(
        "--no-change-detection",
        action="store_true",
        help="Disable change detection (send all pixels every frame)",
    )
    parser.add_argument(
        "--no-run-length",
        action="store_true",
        help="Disable run-length optimization",
    )
    parser.add_argument(
        "--command-delay",
        type=float,
        default=0.001,
        help="Delay between serial commands in seconds (default: 0.001)",
    )
    parser.add_argument(
        "--frame-delay",
        type=float,
        default=0.0,
        help="Minimum delay between frames in seconds (default: 0.0)",
    )
    parser.add_argument(
        "--max-commands",
        type=int,
        default=100,
        help="Maximum commands per frame (default: 100)",
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
        "--show-commands",
        action="store_true",
        help="Log each serial command sent to the LED controller",
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
        if "baud" in serial:
            config_dict["baud"] = serial["baud"]
        if "timeout" in serial:
            config_dict["timeout"] = serial["timeout"]
        if "write_timeout" in serial:
            config_dict["write_timeout"] = serial["write_timeout"]

    if "protocol" in data:
        protocol = data["protocol"]
        if "hex_format" in protocol:
            config_dict["hex_format"] = protocol["hex_format"]
        if "line_ending" in protocol:
            config_dict["line_ending"] = protocol["line_ending"]
        if "command_delay" in protocol:
            config_dict["command_delay"] = protocol["command_delay"]
        if "frame_delay" in protocol:
            config_dict["frame_delay"] = protocol["frame_delay"]

    if "optimization" in data:
        opt = data["optimization"]
        if "change_detection" in opt:
            config_dict["change_detection"] = opt["change_detection"]
        if "run_length" in opt:
            config_dict["run_length"] = opt["run_length"]
        if "max_commands_per_frame" in opt:
            config_dict["max_commands_per_frame"] = opt["max_commands_per_frame"]

    return SerialSinkConfig(**config_dict)


def config_from_args(args: argparse.Namespace) -> SerialSinkConfig:
    """Create configuration from command line arguments."""
    # Parse dimensions
    dimensions = [args.pixels]
    if args.dimensions:
        parts = args.dimensions.lower().split("x")
        dimensions = [int(p) for p in parts]
        if len(dimensions) > 1:
            args.pixels = dimensions[0] * dimensions[1]
        else:
            args.pixels = dimensions[0]

    # Map color format
    color_map = {
        "rgb": ColorFormat.RGB,
        "rgbw": ColorFormat.RGBW,
    }

    return SerialSinkConfig(
        name=args.name,
        port=args.port or "",
        baud=args.baud,
        pixels=args.pixels,
        dimensions=dimensions,
        color_format=color_map.get(args.color_format, ColorFormat.RGB),
        hex_format=args.hex_format,
        change_detection=not args.no_change_detection,
        run_length=not args.no_run_length,
        command_delay=args.command_delay,
        frame_delay=args.frame_delay,
        max_commands_per_frame=args.max_commands,
        trace_commands=args.show_commands,
    )


def list_ports() -> None:
    """Print available serial ports."""
    ports = SerialRenderer.list_ports()

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
    """Test serial connection with a pattern."""
    print(f"Testing serial connection to {config.port} at {config.baud} baud...")
    print()

    serial_config = SerialConfig(
        port=config.port,
        baud=config.baud,
        hex_format=config.hex_format,
    )

    renderer = SerialRenderer(serial_config)

    try:
        renderer.open()
        print("  Port opened successfully")
    except Exception as e:
        print(f"  ERROR: Could not open port: {e}")
        return False

    # Send test pattern
    print("  Sending test pattern...")
    print()

    # Red, green, blue sections
    test_patterns = [
        (f"0,{config.pixels // 3 - 1}", "0xFF0000", "Red"),
        (f"{config.pixels // 3},{2 * config.pixels // 3 - 1}", "0x00FF00", "Green"),
        (f"{2 * config.pixels // 3},{config.pixels - 1}", "0x0000FF", "Blue"),
    ]

    for range_str, color, name in test_patterns:
        cmd = f"{range_str}={color}"
        print(f"    {name}: {cmd}")
        renderer.send_raw(cmd)
        time.sleep(0.1)

    print()
    print("  Pattern sent. Check your LED strip for red/green/blue sections.")
    print()

    # Clear after a moment
    time.sleep(2)
    print("  Clearing strip...")
    renderer.send_raw(f"0,{config.pixels - 1}=0x000000")

    renderer.close()
    print()
    print("SUCCESS: Serial connection working")
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
    print(f"  Pixels: {config.pixels}")
    print(f"  Dimensions: {config.dimensions}")
    print(f"  Serial port: {config.port}")
    print(f"  Baud rate: {config.baud}")
    print(f"  Change detection: {config.change_detection}")
    print(f"  Run-length optimization: {config.run_length}")
    print()

    # Run
    try:
        asyncio.run(run_sink(config))
    except KeyboardInterrupt:
        print("\nShutdown requested")

    return 0


if __name__ == "__main__":
    sys.exit(main())
