"""CLI for LTP Media Source Group - multiple outputs from single media."""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from ltp_media_source.source_group import (
    MediaSourceGroup,
    SourceGroupConfig,
    SourceDefinition,
)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="LTP Media Source Group - Multiple outputs from single media",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # From YAML config file
  ltp-media-source-group --config media_group.yaml

  # Inline video source
  ltp-media-source-group --input video.mp4 \\
      --video "MyVideo:64x32"

  # Video + audio visualizer
  ltp-media-source-group --input video.mp4 \\
      --video "MyVideo:64x32:contain" \\
      --audio-viz "Spectrum:spectrum:60"

  # Multiple audio visualizers
  ltp-media-source-group --input video.mp4 \\
      --audio-viz "Linear:spectrum:60" \\
      --audio-viz "Matrix:spectrogram:16x16"

  # Video + multiple visualizers
  ltp-media-source-group --input video.mp4 \\
      --video "Video:64x32" \\
      --audio-viz "Spectrum:spectrum_matrix:16x16" \\
      --audio-viz "VU:vu:60"

  # Audio file only (no video)
  ltp-media-source-group --audio music.mp3 \\
      --audio-viz "Spectrum:spectrum:60" \\
      --audio-output

  # Microphone input
  ltp-media-source-group --microphone \\
      --audio-viz "LiveVU:vu:60"

Source specification format:
  --video "NAME:WIDTHxHEIGHT[:FIT_MODE]"
    NAME       - Source name for mDNS
    WIDTHxHEIGHT - Output dimensions (e.g., 64x32 or 60)
    FIT_MODE   - Optional: contain, cover, stretch, tile, center

  --audio-viz "NAME:VISUALIZER:WIDTHxHEIGHT[:COLOR_MODE]"
    NAME       - Source name for mDNS
    VISUALIZER - Visualizer type (spectrum, vu, waveform, beat, spectrogram, etc.)
    WIDTHxHEIGHT - Output dimensions (e.g., 60 or 16x16)
    COLOR_MODE - Optional: rainbow, fire, ice, ocean, solid, etc.

Available visualizers:
  Linear (1D):  spectrum, vu, waveform, beat, bass_fill, chasing_dots, level_meter
  Matrix (2D):  spectrum_matrix, spectrogram, ripples, heatmap, vu_bar, scope, plasma
""",
    )

    # Configuration file
    parser.add_argument(
        "--config",
        "-c",
        metavar="PATH",
        help="YAML configuration file",
    )

    # Input media (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--input",
        "-i",
        metavar="PATH",
        help="Video file path (MP4, AVI, MOV, etc.)",
    )
    input_group.add_argument(
        "--audio",
        "-a",
        metavar="PATH",
        help="Audio file path (MP3, WAV, FLAC, OGG)",
    )
    input_group.add_argument(
        "--microphone",
        nargs="?",
        const="default",
        metavar="DEVICE",
        help="Microphone input (device index, name, or 'default')",
    )

    # Source specifications (repeatable)
    parser.add_argument(
        "--video",
        action="append",
        metavar="SPEC",
        default=[],
        help="Video source: NAME:WIDTHxHEIGHT[:FIT_MODE]",
    )
    parser.add_argument(
        "--audio-viz",
        action="append",
        metavar="SPEC",
        default=[],
        dest="audio_viz",
        help="Audio visualizer: NAME:VISUALIZER:WIDTHxHEIGHT[:COLOR_MODE]",
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

    # Audio settings
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=44100,
        help="Audio sample rate (default: 44100)",
    )
    parser.add_argument(
        "--audio-buffer",
        type=float,
        default=2.0,
        help="Audio buffer duration in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--audio-output",
        action="store_true",
        help="Play audio through speakers",
    )
    parser.add_argument(
        "--audio-device",
        metavar="DEVICE",
        help="Audio output device (index or name)",
    )
    parser.add_argument(
        "--volume",
        type=float,
        default=1.0,
        help="Audio output volume 0-1 (default: 1.0)",
    )

    # Global rate
    parser.add_argument(
        "--rate",
        type=int,
        default=30,
        help="Default output frame rate (default: 30)",
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


def parse_video_spec(spec: str, default_rate: int) -> SourceDefinition:
    """Parse a video source specification.

    Format: NAME:WIDTHxHEIGHT[:FIT_MODE]
    """
    parts = spec.split(":")
    if len(parts) < 2:
        raise ValueError(f"Invalid video spec: {spec}. Expected NAME:WIDTHxHEIGHT[:FIT_MODE]")

    name = parts[0]

    # Parse dimensions
    dim_str = parts[1]
    if "x" in dim_str.lower():
        dim_parts = dim_str.lower().split("x")
        dimensions = [int(d) for d in dim_parts]
    else:
        dim = int(dim_str)
        dimensions = [dim, dim]

    fit_mode = parts[2] if len(parts) > 2 else "contain"

    return SourceDefinition(
        name=name,
        type="video",
        dimensions=dimensions,
        rate=default_rate,
        fit_mode=fit_mode,
    )


def parse_audio_viz_spec(spec: str, default_rate: int) -> SourceDefinition:
    """Parse an audio visualizer specification.

    Format: NAME:VISUALIZER:WIDTHxHEIGHT[:COLOR_MODE]
    """
    parts = spec.split(":")
    if len(parts) < 3:
        raise ValueError(
            f"Invalid audio-viz spec: {spec}. "
            f"Expected NAME:VISUALIZER:WIDTHxHEIGHT[:COLOR_MODE]"
        )

    name = parts[0]
    visualizer_type = parts[1]

    # Parse dimensions
    dim_str = parts[2]
    if "x" in dim_str.lower():
        dim_parts = dim_str.lower().split("x")
        dimensions = [int(d) for d in dim_parts]
    else:
        dimensions = [int(dim_str)]

    color_mode = parts[3] if len(parts) > 3 else "rainbow"

    return SourceDefinition(
        name=name,
        type="audio_visualizer",
        dimensions=dimensions,
        rate=default_rate,
        visualizer_type=visualizer_type,
        color_mode=color_mode,
    )


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
    logging.getLogger("ltp_media_source").setLevel(logging.INFO)

    # Determine configuration source
    if args.config:
        # Load from YAML file
        config_path = Path(args.config)
        if not config_path.exists():
            print(f"Error: Configuration file not found: {args.config}", file=sys.stderr)
            return 1

        try:
            group = MediaSourceGroup.from_yaml(config_path)
        except Exception as e:
            print(f"Error loading configuration: {e}", file=sys.stderr)
            return 1

    elif args.input or args.audio or args.microphone is not None:
        # Build config from command-line arguments
        sources = []

        # Parse video sources
        for spec in args.video:
            try:
                source_def = parse_video_spec(spec, args.rate)
                sources.append(source_def)
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                return 1

        # Parse audio visualizer sources
        for spec in args.audio_viz:
            try:
                source_def = parse_audio_viz_spec(spec, args.rate)
                sources.append(source_def)
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                return 1

        if not sources:
            print("Error: No sources specified. Use --video or --audio-viz", file=sys.stderr)
            return 1

        loop_enabled = args.loop and not args.no_loop

        # Determine input type and path
        if args.input:
            input_type = "video"
            media_path = args.input
            input_device = None
        elif args.audio:
            input_type = "audio_file"
            media_path = args.audio
            input_device = None
        else:  # args.microphone
            input_type = "microphone"
            media_path = None
            # Parse microphone device
            input_device = None
            if args.microphone != "default":
                try:
                    input_device = int(args.microphone)
                except ValueError:
                    input_device = args.microphone

        # Parse audio output device
        audio_output_device = None
        if args.audio_device:
            try:
                audio_output_device = int(args.audio_device)
            except ValueError:
                audio_output_device = args.audio_device

        config = SourceGroupConfig(
            media_path=media_path,
            input_type=input_type,
            input_device=input_device,
            loop=loop_enabled,
            speed=args.speed,
            audio_sample_rate=args.sample_rate,
            audio_buffer_duration=args.audio_buffer,
            audio_output=args.audio_output,
            audio_output_device=audio_output_device,
            volume=args.volume,
            sources=sources,
        )

        group = MediaSourceGroup(config)

    else:
        print("Error: One of --config, --input, --audio, or --microphone is required", file=sys.stderr)
        return 1

    # Setup event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def signal_handler() -> None:
        print("\nShutting down...")
        loop.create_task(group.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    # Run
    try:
        loop.run_until_complete(group.run_forever())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
