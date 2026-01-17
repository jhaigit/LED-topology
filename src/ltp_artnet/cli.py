"""CLI for LTP Art-Net Sink."""

import argparse
import asyncio
import logging
import signal
import sys

from libltp import ColorFormat, DeviceType

from ltp_artnet.sink import ArtNetSink, ArtNetSinkConfig
from ltp_artnet.protocol import ARTNET_PORT


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="LTP Art-Net Sink - Output LTP data to Art-Net devices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage - 170 pixels to broadcast
  ltp-artnet-sink --pixels 170

  # Send to specific WLED device
  ltp-artnet-sink --pixels 300 --host 192.168.1.100

  # Multi-universe setup (500 pixels = 3 universes)
  ltp-artnet-sink --pixels 500 --start-universe 0

  # With sync packets for multi-universe
  ltp-artnet-sink --pixels 500 --sync

  # Matrix configuration
  ltp-artnet-sink --pixels 256 --dimensions 16x16
""",
    )

    # Basic settings
    parser.add_argument(
        "--name",
        default="Art-Net Output",
        help="Device name for mDNS advertisement (default: Art-Net Output)",
    )
    parser.add_argument(
        "--pixels",
        type=int,
        default=170,
        help="Number of pixels (default: 170, one RGB universe)",
    )
    parser.add_argument(
        "--dimensions",
        help="Pixel dimensions (e.g., 60 or 16x16 for matrix)",
    )

    # Art-Net settings
    parser.add_argument(
        "--host",
        default="255.255.255.255",
        help="Art-Net target IP (default: 255.255.255.255 broadcast)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=ARTNET_PORT,
        help=f"Art-Net port (default: {ARTNET_PORT})",
    )
    parser.add_argument(
        "--start-universe",
        type=int,
        default=0,
        help="Starting universe number (default: 0)",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Enable ArtSync packets for multi-universe sync",
    )
    parser.add_argument(
        "--no-artpoll",
        action="store_true",
        help="Disable ArtPoll discovery responses",
    )

    # Color format
    parser.add_argument(
        "--color-format",
        choices=["rgb", "grb", "rgbw"],
        default="rgb",
        help="Color format (default: rgb)",
    )

    # LTP network settings
    parser.add_argument(
        "--control-port",
        type=int,
        default=0,
        help="LTP control port (default: auto)",
    )
    parser.add_argument(
        "--data-port",
        type=int,
        default=0,
        help="LTP data port (default: auto)",
    )

    # Rate limiting
    parser.add_argument(
        "--fps",
        type=int,
        default=44,
        help="Maximum frames per second (default: 44)",
    )

    # Logging
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    return parser.parse_args()


def parse_dimensions(dim_str: str | None, pixels: int) -> list[int]:
    """Parse dimension string into list."""
    if not dim_str:
        return [pixels]

    if "x" in dim_str.lower():
        parts = dim_str.lower().split("x")
        return [int(p) for p in parts]
    else:
        return [int(dim_str)]


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.debug else (logging.INFO if args.verbose else logging.WARNING)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Always show info for main module
    logging.getLogger("ltp_artnet").setLevel(logging.INFO)

    # Parse dimensions
    dimensions = parse_dimensions(args.dimensions, args.pixels)

    # Parse color format
    color_format = ColorFormat[args.color_format.upper()]

    # Create config
    config = ArtNetSinkConfig(
        name=args.name,
        pixels=args.pixels,
        dimensions=dimensions,
        color_format=color_format,
        max_refresh_hz=args.fps,
        control_port=args.control_port,
        data_port=args.data_port,
        artnet_host=args.host,
        artnet_port=args.port,
        start_universe=args.start_universe,
        enable_sync=args.sync,
        enable_artpoll=not args.no_artpoll,
        device_type=DeviceType.MATRIX if len(dimensions) > 1 else DeviceType.STRING,
    )

    # Create sink
    sink = ArtNetSink(config)

    # Setup signal handlers
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def signal_handler() -> None:
        print("\nShutting down...")
        loop.create_task(sink.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    # Run
    try:
        loop.run_until_complete(sink.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
