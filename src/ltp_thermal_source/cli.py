"""Command-line interface for LTP Thermal Source."""

import argparse
import asyncio
import logging
import sys

from ltp_thermal_source.source import create_thermal_source

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="ltp-thermal-source",
        description="LTP Thermal Source - AMG8833 infrared thermal camera",
    )

    parser.add_argument(
        "--name",
        "-n",
        type=str,
        default="AMG8833 Thermal",
        help="Device name (default: AMG8833 Thermal)",
    )
    parser.add_argument(
        "--bus",
        type=int,
        default=1,
        help="I2C bus number (default: 1)",
    )
    parser.add_argument(
        "--address",
        type=lambda x: int(x, 0),
        default=0x69,
        help="I2C address in hex (default: 0x69)",
    )
    parser.add_argument(
        "--rate",
        "-r",
        type=int,
        default=10,
        help="Frame rate in Hz (default: 10, AMG8833 max)",
    )
    parser.add_argument(
        "--palette",
        "-p",
        type=str,
        default="ironbow",
        choices=["ironbow", "heat", "grayscale", "arctic"],
        help="Initial color palette (default: ironbow)",
    )
    parser.add_argument(
        "--range-min",
        type=float,
        help="Fixed range minimum temperature in °C (enables fixed range mode)",
    )
    parser.add_argument(
        "--range-max",
        type=float,
        help="Fixed range maximum temperature in °C (enables fixed range mode)",
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

    return parser.parse_args()


async def run_source(args: argparse.Namespace) -> None:
    """Run the thermal source."""
    # Determine range mode
    range_min = args.range_min
    range_max = args.range_max
    if (range_min is not None) != (range_max is not None):
        print("Error: --range-min and --range-max must both be specified", file=sys.stderr)
        sys.exit(1)

    source = create_thermal_source(
        name=args.name,
        bus=args.bus,
        address=args.address,
        rate=args.rate,
        palette=args.palette,
        range_min=range_min,
        range_max=range_max,
        control_port=args.control_port,
    )

    await source.start()

    # Connect to sink if specified
    if args.sink:
        try:
            host, port = args.sink.rsplit(":", 1)
            await source.connect_to_sink(host, int(port))
        except ValueError:
            print(f"Invalid sink address: {args.sink}", file=sys.stderr)
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

    # Print startup info
    print(f"Starting LTP Thermal Source: {args.name}")
    print(f"  I2C bus: {args.bus}, address: 0x{args.address:02X}")
    print(f"  Rate: {args.rate} Hz")
    print(f"  Palette: {args.palette}")
    if args.range_min is not None:
        print(f"  Range: {args.range_min}°C - {args.range_max}°C (fixed)")
    else:
        print("  Range: auto")
    print()

    try:
        asyncio.run(run_source(args))
    except KeyboardInterrupt:
        print("\nShutdown requested")

    return 0


if __name__ == "__main__":
    sys.exit(main())
