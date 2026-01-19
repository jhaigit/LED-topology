"""CLI for multi-channel media source.

This CLI provides the ltp-multi-channel-source command for running a
media source with multiple output channels under a single network identity.

Usage:
    # From YAML config
    ltp-multi-channel-source --config multi_channel.yaml

    # Inline specification
    ltp-multi-channel-source video.mp4 \\
        --name "My Media" \\
        --video "video:Video:64x32" \\
        --audio "spectrum:Spectrum:spectrum:60" \\
        --audio "spectrogram:Spectrogram:spectrogram:16x16:fire" \\
        --default-channel video
"""

import argparse
import asyncio
import logging
import sys

from ltp_media_source.multi_channel import (
    MultiChannelSource,
    MultiChannelConfig,
    ChannelConfig,
    run_multi_channel_from_yaml,
)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Multi-channel media source with single network identity",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Using YAML configuration
  ltp-multi-channel-source --config multi_channel.yaml

  # Inline video and audio channels
  ltp-multi-channel-source /path/to/video.mp4 \\
      --name "My Media" \\
      --video "video:Video:64x32:contain" \\
      --audio "spectrum:Spectrum:spectrum:60:rainbow"

Channel specification formats:
  Video:  id:name:WxH:fit_mode
          Example: "video:Video:64x32:cover"

  Audio:  id:name:visualizer:WxH:color_mode
          Example: "spectrum:Spectrum:spectrum:60:rainbow"
          Example: "spectro:Spectrogram:spectrogram:16x16:fire"

Available visualizers:
  Linear:  spectrum, vu_meter, waveform, beat_pulse, bass_fill, chasing_dots, level_meter
  Matrix:  spectrum_bars, spectrogram, beat_ripples, frequency_heatmap,
           vu_meter_bar, waveform_scope, plasma

Color modes: rainbow, fire, ice, forest, sunset, ocean, purple, neon, grayscale
        """,
    )

    parser.add_argument(
        "media_path",
        nargs="?",
        help="Path to media file (video, audio, or image)",
    )

    parser.add_argument(
        "-c", "--config",
        help="Path to YAML configuration file",
    )

    parser.add_argument(
        "-n", "--name",
        default="Multi-Channel Media",
        help="Source name for mDNS advertisement (default: Multi-Channel Media)",
    )

    parser.add_argument(
        "--video",
        action="append",
        dest="video_specs",
        metavar="SPEC",
        help="Video channel: id:name:WxH:fit_mode (can specify multiple)",
    )

    parser.add_argument(
        "--audio",
        action="append",
        dest="audio_specs",
        metavar="SPEC",
        help="Audio visualizer channel: id:name:type:WxH:color (can specify multiple)",
    )

    parser.add_argument(
        "--default-channel",
        help="Default channel ID for backward compatibility",
    )

    parser.add_argument(
        "--loop/--no-loop",
        dest="loop",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable looping (default: enabled)",
    )

    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Playback speed multiplier (default: 1.0)",
    )

    parser.add_argument(
        "-p", "--port",
        type=int,
        default=0,
        help="Control port (default: auto)",
    )

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


def main() -> None:
    """Main entry point."""
    args = parse_args()

    # Configure logging
    if args.debug:
        log_level = logging.DEBUG
    elif args.verbose:
        log_level = logging.INFO
    else:
        log_level = logging.WARNING

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Run from config file if provided
    if args.config:
        try:
            asyncio.run(run_multi_channel_from_yaml(args.config))
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except KeyboardInterrupt:
            pass
        return

    # Otherwise require media path
    if not args.media_path:
        print("Error: media_path is required when not using --config", file=sys.stderr)
        sys.exit(1)

    # Require at least one channel
    if not args.video_specs and not args.audio_specs:
        print(
            "Error: At least one --video or --audio channel is required",
            file=sys.stderr,
        )
        sys.exit(1)

    # Parse channel specs
    channels = []

    if args.video_specs:
        for spec in args.video_specs:
            try:
                channel = parse_video_spec(spec)
                channels.append(channel)
            except ValueError as e:
                print(f"Error parsing video spec '{spec}': {e}", file=sys.stderr)
                sys.exit(1)

    if args.audio_specs:
        for spec in args.audio_specs:
            try:
                channel = parse_audio_spec(spec)
                channels.append(channel)
            except ValueError as e:
                print(f"Error parsing audio spec '{spec}': {e}", file=sys.stderr)
                sys.exit(1)

    # Determine default channel
    default_channel = args.default_channel
    if not default_channel and channels:
        default_channel = channels[0].id

    # Build config
    config = MultiChannelConfig(
        name=args.name,
        media_path=args.media_path,
        loop=args.loop,
        speed=args.speed,
        control_port=args.port,
        channels=channels,
        default_channel=default_channel,
    )

    # Run
    try:
        source = MultiChannelSource(config)
        asyncio.run(source.run_forever())
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass


def parse_video_spec(spec: str) -> ChannelConfig:
    """Parse a video channel specification.

    Format: id:name:WxH:fit_mode
    Examples:
        "video:Video:64x32"
        "main:Main Video:32x16:cover"
    """
    parts = spec.split(":")
    if len(parts) < 1:
        raise ValueError("Video spec requires at least an ID")

    channel_id = parts[0]
    name = parts[1] if len(parts) > 1 else channel_id.title()

    dimensions = [16, 16]
    if len(parts) > 2:
        dim_parts = parts[2].lower().split("x")
        dimensions = [int(d) for d in dim_parts]
        if len(dimensions) == 1:
            dimensions = [dimensions[0], dimensions[0]]

    fit_mode = parts[3] if len(parts) > 3 else "contain"

    return ChannelConfig(
        id=channel_id,
        name=name,
        channel_type="video",
        dimensions=dimensions,
        fit_mode=fit_mode,
    )


def parse_audio_spec(spec: str) -> ChannelConfig:
    """Parse an audio visualizer channel specification.

    Format: id:name:visualizer:WxH:color_mode
    Examples:
        "spectrum:Spectrum:spectrum:60"
        "spectro:Spectrogram:spectrogram:16x16:fire"
    """
    parts = spec.split(":")
    if len(parts) < 1:
        raise ValueError("Audio spec requires at least an ID")

    channel_id = parts[0]
    name = parts[1] if len(parts) > 1 else channel_id.title()
    visualizer_type = parts[2] if len(parts) > 2 else "spectrum"

    dimensions = [60]
    if len(parts) > 3:
        dim_parts = parts[3].lower().split("x")
        dimensions = [int(d) for d in dim_parts]

    color_mode = parts[4] if len(parts) > 4 else "rainbow"

    return ChannelConfig(
        id=channel_id,
        name=name,
        channel_type="audio_visualizer",
        dimensions=dimensions,
        visualizer_type=visualizer_type,
        color_mode=color_mode,
    )


if __name__ == "__main__":
    main()
