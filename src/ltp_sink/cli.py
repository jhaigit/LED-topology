"""Command-line interface for LTP Sink."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import yaml

from ltp_sink.sink import Sink, SinkConfig
from libltp import ColorFormat, DeviceType


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="ltp-sink",
        description="LTP Sink - Virtual LED display receiver",
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
        default="LTP Sink",
        help="Device name (default: LTP Sink)",
    )
    parser.add_argument(
        "--description",
        "-d",
        type=str,
        default="",
        help="Device description",
    )
    parser.add_argument(
        "--type",
        "-t",
        type=str,
        choices=["single", "string", "matrix", "array"],
        default="string",
        help="Device type (default: string)",
    )
    parser.add_argument(
        "--pixels",
        "-p",
        type=int,
        default=60,
        help="Number of pixels (default: 60)",
    )
    parser.add_argument(
        "--dimensions",
        type=str,
        help="Dimensions as WxH for matrix (e.g., 16x16)",
    )
    parser.add_argument(
        "--color",
        type=str,
        choices=["rgb", "rgbw", "hsv"],
        default="rgb",
        help="Color format (default: rgb)",
    )
    parser.add_argument(
        "--rate",
        type=int,
        default=60,
        help="Maximum refresh rate in Hz (default: 60)",
    )
    parser.add_argument(
        "--renderer",
        "-r",
        type=str,
        choices=["terminal", "gui", "headless"],
        default="terminal",
        help="Renderer type (default: terminal)",
    )
    parser.add_argument(
        "--style",
        "-s",
        type=str,
        choices=["block", "braille", "ascii", "bar"],
        default="block",
        help="Terminal renderer style (default: block)",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=0,
        help="Control channel port (default: auto)",
    )
    parser.add_argument(
        "--data-port",
        type=int,
        default=0,
        help="Data channel port (default: auto)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--list-renderers",
        action="store_true",
        help="List available renderers and exit",
    )

    return parser.parse_args()


def load_config(path: Path) -> SinkConfig:
    """Load configuration from YAML file."""
    with open(path) as f:
        data = yaml.safe_load(f)

    # Map nested config to flat
    config_dict = {}

    if "device" in data:
        device = data["device"]
        if "id" in device and device["id"] != "auto":
            config_dict["device_id"] = device["id"]
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

    if "network" in data:
        network = data["network"]
        if "control_port" in network:
            config_dict["control_port"] = network["control_port"]
        if "data_port" in network:
            config_dict["data_port"] = network["data_port"]

    if "renderer" in data:
        renderer = data["renderer"]
        if "type" in renderer:
            config_dict["renderer_type"] = renderer["type"]
        # Pass renderer-specific config
        renderer_config = {k: v for k, v in renderer.items() if k != "type"}
        if renderer_config:
            config_dict["renderer_config"] = renderer_config

    return SinkConfig(**config_dict)


def config_from_args(args: argparse.Namespace) -> SinkConfig:
    """Create configuration from command line arguments."""
    # Parse dimensions
    dimensions = [args.pixels]
    if args.dimensions:
        parts = args.dimensions.lower().split("x")
        dimensions = [int(p) for p in parts]
        args.pixels = dimensions[0] * dimensions[1] if len(dimensions) > 1 else dimensions[0]

    # Map color format
    color_map = {
        "rgb": ColorFormat.RGB,
        "rgbw": ColorFormat.RGBW,
        "hsv": ColorFormat.HSV,
    }

    # Map device type
    type_map = {
        "single": DeviceType.SINGLE,
        "string": DeviceType.STRING,
        "matrix": DeviceType.MATRIX,
        "array": DeviceType.ARRAY,
    }

    renderer_config = {}
    if args.renderer == "terminal":
        renderer_config["style"] = args.style

    return SinkConfig(
        name=args.name,
        description=args.description,
        device_type=type_map.get(args.type, DeviceType.STRING),
        pixels=args.pixels,
        dimensions=dimensions,
        color_format=color_map.get(args.color, ColorFormat.RGB),
        max_refresh_hz=args.rate,
        control_port=args.control_port,
        data_port=args.data_port,
        renderer_type=args.renderer,
        renderer_config=renderer_config,
    )


def list_renderers() -> None:
    """Print available renderers."""
    print("Available renderers:")
    print()
    print("  terminal    - Terminal-based renderer using Unicode and colors")
    print("                Styles: block, braille, ascii, bar")
    print()
    print("  gui         - Graphical window renderer using pygame (requires pygame)")
    print()
    print("  headless    - No visualization, for testing/logging")


async def run_sink(config: SinkConfig) -> None:
    """Run the sink."""
    sink = Sink(config)

    # Handle shutdown signals
    loop = asyncio.get_event_loop()

    def shutdown() -> None:
        asyncio.create_task(sink.stop())

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
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Handle special commands
    if args.list_renderers:
        list_renderers()
        return 0

    # Load configuration
    if args.config:
        if not args.config.exists():
            print(f"Error: Config file not found: {args.config}", file=sys.stderr)
            return 1
        config = load_config(args.config)
    else:
        config = config_from_args(args)

    # Print startup info
    print(f"Starting LTP Sink: {config.name}")
    print(f"  Pixels: {config.pixels}")
    print(f"  Dimensions: {config.dimensions}")
    print(f"  Color format: {config.color_format.name}")
    print(f"  Renderer: {config.renderer_type}")
    print()

    # Run
    try:
        asyncio.run(run_sink(config))
    except KeyboardInterrupt:
        print("\nShutdown requested")

    return 0


if __name__ == "__main__":
    sys.exit(main())
