"""CLI for LTP Media Source."""

import argparse
import asyncio
import logging
import signal
import sys

from libltp import ColorFormat

from ltp_media_source.source import MediaSource, MediaSourceConfig
from ltp_media_source.inputs.base import FitMode

# Optional audio support
try:
    from ltp_media_source.inputs import HAS_AUDIO_INPUTS
    if HAS_AUDIO_INPUTS:
        from ltp_media_source.inputs import list_audio_devices
    from ltp_media_source.audio_playback import (
        AudioPlayback,
        list_output_devices,
        HAS_SOUNDDEVICE,
    )
except ImportError:
    HAS_AUDIO_INPUTS = False
    HAS_SOUNDDEVICE = False
    list_audio_devices = None
    list_output_devices = None
    AudioPlayback = None


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="LTP Media Source - Display video/images on LED matrices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Static image on 16x16 matrix
  ltp-media-source --image logo.png --dimensions 16x16

  # Animated GIF
  ltp-media-source --gif animation.gif --dimensions 32x8

  # Video file with cover fit
  ltp-media-source --video movie.mp4 --dimensions 64x64 --fit cover

  # Video with audio playback through speakers
  ltp-media-source --video music.mp4 --dimensions 32x32 --audio-output

  # Webcam feed
  ltp-media-source --camera 0 --dimensions 16x16

  # Screen capture
  ltp-media-source --screen --dimensions 60x16

  # Screen region capture
  ltp-media-source --screen --region 0,0,1920,1080 --dimensions 60x16

  # Audio file visualization (spectrum, beats, etc.)
  ltp-media-source --audio-file music.mp3 --dimensions 60x16 --audio-output

  # Microphone visualization
  ltp-media-source --microphone --dimensions 32x8

  # List available audio devices
  ltp-media-source --list-audio-devices
""",
    )

    # Input type (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--image",
        metavar="PATH",
        help="Static image file (PNG, JPG, BMP, WebP)",
    )
    input_group.add_argument(
        "--gif",
        metavar="PATH",
        help="Animated GIF file",
    )
    input_group.add_argument(
        "--video",
        metavar="PATH",
        help="Video file (MP4, AVI, MOV, MKV, WebM)",
    )
    input_group.add_argument(
        "--camera",
        metavar="DEVICE",
        nargs="?",
        const="0",
        help="Camera device (default: 0)",
    )
    input_group.add_argument(
        "--screen",
        action="store_true",
        help="Screen capture",
    )
    input_group.add_argument(
        "--stream",
        metavar="URL",
        help="Network stream (RTSP, HTTP)",
    )
    input_group.add_argument(
        "--audio-file",
        metavar="PATH",
        help="Audio file for visualization (MP3, WAV, FLAC, OGG)",
    )
    input_group.add_argument(
        "--microphone",
        nargs="?",
        const="default",
        metavar="DEVICE",
        help="Microphone input for visualization (default: system default)",
    )
    input_group.add_argument(
        "--list-audio-devices",
        action="store_true",
        help="List available audio input/output devices and exit",
    )

    # Output dimensions
    parser.add_argument(
        "--dimensions",
        "-d",
        default="16x16",
        help="Output dimensions (e.g., 60 or 16x16, default: 16x16)",
    )

    # Display options
    parser.add_argument(
        "--fit",
        choices=["contain", "cover", "stretch", "tile", "center"],
        default="contain",
        help="Fit mode (default: contain)",
    )
    parser.add_argument(
        "--rate",
        type=int,
        default=30,
        help="Output frame rate (default: 30)",
    )
    parser.add_argument(
        "--brightness",
        type=float,
        default=1.0,
        help="Initial brightness 0-1 (default: 1.0)",
    )

    # Playback options
    parser.add_argument(
        "--loop",
        action="store_true",
        default=True,
        help="Loop playback (default: True)",
    )
    parser.add_argument(
        "--no-loop",
        action="store_true",
        help="Disable looping",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Playback speed multiplier (default: 1.0)",
    )
    parser.add_argument(
        "--start",
        type=float,
        default=0.0,
        help="Start time in seconds (for video)",
    )
    parser.add_argument(
        "--end",
        type=float,
        default=None,
        help="End time in seconds (for video)",
    )

    # Screen capture options
    parser.add_argument(
        "--monitor",
        type=int,
        default=0,
        help="Monitor index for screen capture (default: 0)",
    )
    parser.add_argument(
        "--region",
        help="Screen region as x,y,width,height",
    )

    # Camera options
    parser.add_argument(
        "--resolution",
        help="Camera resolution as WxH (e.g., 640x480)",
    )

    # Audio options
    parser.add_argument(
        "--audio-output",
        action="store_true",
        help="Play audio through speakers (for video/audio file sources)",
    )
    parser.add_argument(
        "--audio-device",
        metavar="DEVICE",
        help="Audio device (index or name) for input or output",
    )
    parser.add_argument(
        "--volume",
        type=float,
        default=1.0,
        help="Audio output volume 0-1 (default: 1.0)",
    )

    # Network options
    parser.add_argument(
        "--name",
        default="Media Source",
        help="Source name for mDNS (default: Media Source)",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=0,
        help="Control port (default: auto)",
    )

    # Logging
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Debug output",
    )

    return parser.parse_args()


def parse_dimensions(dim_str: str) -> list[int]:
    """Parse dimension string to list."""
    if "x" in dim_str.lower():
        parts = dim_str.lower().split("x")
        return [int(p) for p in parts]
    else:
        return [int(dim_str)]


def parse_region(region_str: str | None) -> tuple[int, int, int, int] | None:
    """Parse region string to tuple."""
    if not region_str:
        return None
    parts = [int(p) for p in region_str.split(",")]
    if len(parts) != 4:
        raise ValueError("Region must be x,y,width,height")
    return tuple(parts)  # type: ignore


def parse_resolution(res_str: str | None) -> tuple[int, int] | None:
    """Parse resolution string to tuple."""
    if not res_str:
        return None
    parts = res_str.lower().split("x")
    if len(parts) != 2:
        raise ValueError("Resolution must be WxH")
    return (int(parts[0]), int(parts[1]))


def list_devices() -> int:
    """List available audio devices."""
    print("\n=== Audio Input Devices ===")
    if HAS_AUDIO_INPUTS and list_audio_devices:
        devices = list_audio_devices()
        if devices:
            for dev in devices:
                print(f"  [{dev['index']}] {dev['name']}")
                print(f"      Channels: {dev['channels']}, Sample Rate: {int(dev['sample_rate'])}Hz")
        else:
            print("  No input devices found")
    else:
        print("  Audio input not available (sounddevice not installed)")

    print("\n=== Audio Output Devices ===")
    if HAS_SOUNDDEVICE and list_output_devices:
        devices = list_output_devices()
        if devices:
            for dev in devices:
                print(f"  [{dev['index']}] {dev['name']}")
                print(f"      Channels: {dev['channels']}, Sample Rate: {int(dev['sample_rate'])}Hz")
        else:
            print("  No output devices found")
    else:
        print("  Audio output not available (sounddevice not installed)")

    print()
    return 0


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Handle --list-audio-devices
    if args.list_audio_devices:
        return list_devices()

    # Setup logging
    log_level = logging.DEBUG if args.debug else (logging.INFO if args.verbose else logging.WARNING)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("ltp_media_source").setLevel(logging.INFO)

    # Parse dimensions
    dimensions = parse_dimensions(args.dimensions)

    # Determine input type and path
    input_type = "image"
    input_path = ""
    input_params: dict = {}

    if args.image:
        input_type = "image"
        input_path = args.image
    elif args.gif:
        input_type = "gif"
        input_path = args.gif
        input_params["speed"] = args.speed
    elif args.video:
        input_type = "video"
        input_path = args.video
        input_params["speed"] = args.speed
        input_params["start_time"] = args.start
        if args.end:
            input_params["end_time"] = args.end
    elif args.stream:
        input_type = "video"  # OpenCV handles streams as video
        input_path = args.stream
    elif args.camera is not None:
        input_type = "camera"
        try:
            input_params["device"] = int(args.camera)
        except ValueError:
            input_params["device"] = args.camera
        resolution = parse_resolution(args.resolution)
        if resolution:
            input_params["resolution"] = resolution
    elif args.screen:
        input_type = "screen"
        input_params["monitor"] = args.monitor
        region = parse_region(args.region)
        if region:
            input_params["region"] = region
    elif args.audio_file:
        if not HAS_AUDIO_INPUTS:
            print("Error: Audio input requires sounddevice. Install with: pip install 'ltp[media]'")
            return 1
        input_type = "audio_file"
        input_path = args.audio_file
        input_params["speed"] = args.speed
        input_params["start_time"] = args.start
        if args.end:
            input_params["end_time"] = args.end
    elif args.microphone is not None:
        if not HAS_AUDIO_INPUTS:
            print("Error: Microphone input requires sounddevice. Install with: pip install 'ltp[media]'")
            return 1
        input_type = "microphone"
        if args.microphone != "default":
            try:
                input_params["device"] = int(args.microphone)
            except ValueError:
                input_params["device"] = args.microphone
        elif args.audio_device:
            try:
                input_params["device"] = int(args.audio_device)
            except ValueError:
                input_params["device"] = args.audio_device

    # Handle loop flag (only for types that support it)
    loop_enabled = args.loop and not args.no_loop
    if input_type in ("gif", "video", "audio_file"):
        input_params["loop"] = loop_enabled

    # Create config
    config = MediaSourceConfig(
        name=args.name,
        dimensions=dimensions,
        rate=args.rate,
        input_type=input_type,
        input_path=input_path,
        input_params=input_params,
        fit_mode=args.fit,
    )

    # Create source
    source = MediaSource(config)

    # Set initial brightness via control
    if args.brightness != 1.0:
        source._controls.set_value("brightness", args.brightness)

    # Setup event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def signal_handler() -> None:
        print("\nShutting down...")
        loop.create_task(source.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    # Run
    try:
        loop.run_until_complete(source.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
