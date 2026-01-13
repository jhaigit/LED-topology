"""Command-line interface for LTP Source."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import yaml

from ltp_source.source import Source, SourceConfig
from ltp_source.patterns import PatternRegistry
from libltp import ColorFormat, SourceMode


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="ltp-source",
        description="LTP Source - LED pattern generator",
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
        default="LTP Source",
        help="Device name (default: LTP Source)",
    )
    parser.add_argument(
        "--description",
        "-d",
        type=str,
        default="",
        help="Device description",
    )
    parser.add_argument(
        "--pattern",
        "-p",
        type=str,
        default="rainbow",
        help="Pattern to generate (default: rainbow)",
    )
    parser.add_argument(
        "--dimensions",
        type=str,
        default="60",
        help="Output dimensions (e.g., 60 or 16x16)",
    )
    parser.add_argument(
        "--rate",
        "-r",
        type=int,
        default=30,
        help="Frame rate in Hz (default: 30)",
    )
    parser.add_argument(
        "--color",
        type=str,
        choices=["rgb", "rgbw", "hsv"],
        default="rgb",
        help="Color format (default: rgb)",
    )
    parser.add_argument(
        "--sink",
        "-s",
        type=str,
        help="Connect to sink (host:port)",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=0,
        help="Control channel port (default: auto)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--list-patterns",
        action="store_true",
        help="List available patterns and exit",
    )

    # Pattern-specific arguments
    parser.add_argument(
        "--speed",
        type=float,
        help="Pattern animation speed",
    )
    parser.add_argument(
        "--brightness",
        type=float,
        help="Pattern brightness (0-1)",
    )

    return parser.parse_args()


def load_config(path: Path) -> SourceConfig:
    """Load configuration from YAML file."""
    with open(path) as f:
        data = yaml.safe_load(f)

    config_dict = {}

    if "device" in data:
        device = data["device"]
        if "id" in device and device["id"] != "auto":
            config_dict["device_id"] = device["id"]
        if "name" in device:
            config_dict["name"] = device["name"]
        if "description" in device:
            config_dict["description"] = device["description"]

    if "output" in data or "outputs" in data:
        output = data.get("output") or data.get("outputs", [{}])[0]
        if "dimensions" in output:
            config_dict["dimensions"] = output["dimensions"]
        if "color_format" in output:
            config_dict["color_format"] = ColorFormat[output["color_format"].upper()]
        if "rate" in output:
            config_dict["rate"] = output["rate"]

    if "pattern" in data:
        pattern = data["pattern"]
        if isinstance(pattern, str):
            config_dict["pattern"] = pattern
        elif isinstance(pattern, dict):
            config_dict["pattern"] = pattern.get("type", "rainbow")
            config_dict["pattern_params"] = pattern.get("params", {})

    if "network" in data:
        network = data["network"]
        if "control_port" in network:
            config_dict["control_port"] = network["control_port"]

    return SourceConfig(**config_dict)


def config_from_args(args: argparse.Namespace) -> SourceConfig:
    """Create configuration from command line arguments."""
    # Parse dimensions
    dim_parts = args.dimensions.lower().split("x")
    dimensions = [int(p) for p in dim_parts]

    # Map color format
    color_map = {
        "rgb": ColorFormat.RGB,
        "rgbw": ColorFormat.RGBW,
        "hsv": ColorFormat.HSV,
    }

    # Build pattern params from args
    pattern_params = {}
    if args.speed is not None:
        pattern_params["speed"] = args.speed
    if args.brightness is not None:
        pattern_params["brightness"] = args.brightness

    return SourceConfig(
        name=args.name,
        description=args.description,
        pattern=args.pattern,
        pattern_params=pattern_params,
        dimensions=dimensions,
        rate=args.rate,
        color_format=color_map.get(args.color, ColorFormat.RGB),
        control_port=args.control_port,
    )


def list_patterns() -> None:
    """Print available patterns."""
    print("Available patterns:")
    print()
    for pattern in PatternRegistry.list_patterns():
        print(f"  {pattern['name']:12} - {pattern['description']}")


async def run_source(config: SourceConfig, sink_addr: str | None = None) -> None:
    """Run the source."""
    source = Source(config)
    await source.start()

    # Connect to sink if specified
    if sink_addr:
        try:
            host, port = sink_addr.rsplit(":", 1)
            await source.connect_to_sink(host, int(port))
        except ValueError:
            print(f"Invalid sink address: {sink_addr}", file=sys.stderr)
        except Exception as e:
            print(f"Failed to connect to sink: {e}", file=sys.stderr)

    try:
        while source.is_running:
            await asyncio.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        await source.stop()


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
    if args.list_patterns:
        list_patterns()
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
    print(f"Starting LTP Source: {config.name}")
    print(f"  Pattern: {config.pattern}")
    print(f"  Dimensions: {config.dimensions}")
    print(f"  Rate: {config.rate} Hz")
    print(f"  Color format: {config.color_format.name}")
    print()

    # Run
    try:
        asyncio.run(run_source(config, args.sink))
    except KeyboardInterrupt:
        print("\nShutdown requested")

    return 0


if __name__ == "__main__":
    sys.exit(main())
