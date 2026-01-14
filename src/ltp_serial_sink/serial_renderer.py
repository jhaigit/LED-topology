"""Serial renderer for LTP Serial Sink."""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    serial = None

logger = logging.getLogger(__name__)


@dataclass
class SerialConfig:
    """Serial port configuration."""

    port: str = ""
    baud: int = 38400
    timeout: float = 1.0
    write_timeout: float = 1.0

    # Advanced serial settings
    data_bits: int = 8
    parity: str = "none"  # none, even, odd
    stop_bits: int = 1
    flow_control: str = "none"  # none, rts_cts, xon_xoff

    # Protocol settings
    hex_format: str = "0x"  # "0x" or "#"
    line_ending: str = "\n"  # "\n", "\r", or "\r\n"
    command_delay: float = 0.001  # Delay between commands (seconds)
    frame_delay: float = 0.0  # Delay between frames (seconds)

    # Optimization
    change_detection: bool = True  # Only send changed pixels
    run_length: bool = True  # Combine consecutive same-color pixels
    min_run_length: int = 1  # Minimum pixels to combine
    max_commands_per_frame: int = 0  # Limit commands per frame (0 = unlimited)

    # Debugging
    trace_commands: bool = False  # Log each serial command sent


class SerialRenderer:
    """Renders pixel data to serial commands."""

    def __init__(self, config: SerialConfig):
        if not SERIAL_AVAILABLE:
            raise ImportError("pyserial is required for serial sink. Install with: pip install pyserial")

        self.config = config
        self._serial: Any = None
        self._last_frame: np.ndarray | None = None
        self._connected = False
        self._frame_count = 0
        self._command_count = 0
        self._last_frame_time = 0.0

    def is_connected(self) -> bool:
        """Check if serial port is connected."""
        return self._connected and self._serial is not None and self._serial.is_open

    def open(self) -> None:
        """Open serial port connection."""
        if not self.config.port:
            raise ValueError("Serial port not specified")

        # Map parity
        parity_map = {
            "none": serial.PARITY_NONE,
            "even": serial.PARITY_EVEN,
            "odd": serial.PARITY_ODD,
        }

        # Map stop bits
        stopbits_map = {
            1: serial.STOPBITS_ONE,
            2: serial.STOPBITS_TWO,
        }

        self._serial = serial.Serial(
            port=self.config.port,
            baudrate=self.config.baud,
            bytesize=self.config.data_bits,
            parity=parity_map.get(self.config.parity, serial.PARITY_NONE),
            stopbits=stopbits_map.get(self.config.stop_bits, serial.STOPBITS_ONE),
            timeout=self.config.timeout,
            write_timeout=self.config.write_timeout,
        )

        # Configure flow control
        if self.config.flow_control == "rts_cts":
            self._serial.rtscts = True
        elif self.config.flow_control == "xon_xoff":
            self._serial.xonxoff = True

        self._connected = True
        self._last_frame = None  # Reset frame buffer on reconnect
        logger.info(f"Serial port {self.config.port} opened at {self.config.baud} baud")

    def close(self) -> None:
        """Close serial port connection."""
        if self._serial:
            try:
                self._serial.close()
            except Exception as e:
                logger.warning(f"Error closing serial port: {e}")
            self._serial = None
        self._connected = False
        logger.info("Serial port closed")

    def clear(self) -> None:
        """Clear renderer state (called when stream stops).

        Resets the last frame buffer so the next data is treated as new.
        """
        self._last_frame = None
        logger.debug("Serial renderer state cleared")

    def render(self, pixels: np.ndarray) -> int:
        """Render pixel data to serial device.

        Args:
            pixels: Pixel data as numpy array (n_pixels, 3) RGB

        Returns:
            Number of commands sent
        """
        if not self.is_connected():
            return 0

        # Handle frame delay
        if self.config.frame_delay > 0:
            elapsed = time.time() - self._last_frame_time
            if elapsed < self.config.frame_delay:
                time.sleep(self.config.frame_delay - elapsed)
        self._last_frame_time = time.time()

        # Detect changes or use full frame
        if self.config.change_detection:
            changes = self._detect_changes(pixels)
        else:
            changes = self._find_runs(pixels)

        if not changes:
            return 0

        # Generate and send commands
        commands = self._generate_commands(changes)
        total_commands = len(commands)

        # Limit commands per frame (0 = unlimited)
        truncated = False
        if self.config.max_commands_per_frame > 0 and len(commands) > self.config.max_commands_per_frame:
            commands = commands[:self.config.max_commands_per_frame]
            truncated = True

        # Log command stats at debug level
        if truncated:
            logger.warning(
                f"Frame {self._frame_count}: truncated {total_commands} -> {len(commands)} commands "
                f"(max_commands_per_frame={self.config.max_commands_per_frame})"
            )
        else:
            logger.debug(
                f"Frame {self._frame_count}: {len(commands)} commands from {len(changes)} changes"
            )

        sent = 0
        for cmd in commands:
            if self._send_command(cmd):
                sent += 1
                self._command_count += 1

            if self.config.command_delay > 0:
                time.sleep(self.config.command_delay)

        self._frame_count += 1

        if sent < len(commands):
            logger.warning(f"Frame {self._frame_count - 1}: only sent {sent}/{len(commands)} commands")

        return sent

    def _detect_changes(self, pixels: np.ndarray) -> list[tuple[int, int, tuple[int, int, int]]]:
        """Detect regions that changed from last frame.

        Returns:
            List of (start, end, color) tuples for changed regions
            Note: end is inclusive (the last LED to set)
        """
        if self._last_frame is None:
            # First frame - everything changed
            self._last_frame = pixels.copy()
            return self._find_runs(pixels)

        # Find pixels that changed
        changed = np.any(pixels != self._last_frame, axis=1)
        self._last_frame = pixels.copy()

        if not np.any(changed):
            return []  # No changes

        # Find runs of changed pixels with same color
        changes = []
        i = 0
        while i < len(changed):
            if changed[i]:
                # Start of changed region
                start = i
                color = tuple(int(c) for c in pixels[i])

                # Extend while same color and changed
                while (
                    i < len(changed)
                    and changed[i]
                    and tuple(int(c) for c in pixels[i]) == color
                ):
                    i += 1

                # end is inclusive (last LED index)
                changes.append((start, i - 1, color))
            else:
                i += 1

        return changes

    def _find_runs(self, pixels: np.ndarray) -> list[tuple[int, int, tuple[int, int, int]]]:
        """Find runs of consecutive same-color pixels.

        Returns:
            List of (start, end, color) tuples
            Note: end is inclusive (the last LED to set)
        """
        if len(pixels) == 0:
            return []

        runs = []
        start = 0
        current_color = tuple(int(c) for c in pixels[0])

        for i in range(1, len(pixels)):
            color = tuple(int(c) for c in pixels[i])
            if color != current_color:
                # Check min run length
                if (i - start) >= self.config.min_run_length:
                    runs.append((start, i - 1, current_color))
                else:
                    # Output individual pixels
                    for j in range(start, i):
                        runs.append((j, j, tuple(int(c) for c in pixels[j])))
                start = i
                current_color = color

        # Final run (end is inclusive)
        if (len(pixels) - start) >= self.config.min_run_length:
            runs.append((start, len(pixels) - 1, current_color))
        else:
            for j in range(start, len(pixels)):
                runs.append((j, j, tuple(int(c) for c in pixels[j])))

        return runs

    def _generate_commands(
        self, changes: list[tuple[int, int, tuple[int, int, int]]]
    ) -> list[str]:
        """Generate serial commands for pixel changes."""
        commands = []

        for start, end, color in changes:
            r, g, b = color

            if self.config.hex_format == "0x":
                color_str = f"0x{r:02X}{g:02X}{b:02X}"
            else:
                color_str = f"#{r:02X}{g:02X}{b:02X}"

            # Use short form for single pixel
            if start == end:
                command = f"{start}={color_str}"
            else:
                command = f"{start},{end}={color_str}"
            commands.append(command)

        return commands

    def _send_command(self, command: str) -> bool:
        """Send command to serial port."""
        if not self.is_connected():
            return False

        try:
            full_cmd = command + self.config.line_ending
            self._serial.write(full_cmd.encode("ascii"))

            # Log command if tracing is enabled
            if self.config.trace_commands:
                logger.info(f"SERIAL TX: {command}")

            return True
        except serial.SerialTimeoutException:
            logger.warning(f"Serial write timeout for command: {command}")
            return False
        except Exception as e:
            logger.error(f"Serial write error: {e}")
            self._connected = False
            return False

    def send_raw(self, command: str) -> bool:
        """Send raw command to serial port (for testing)."""
        return self._send_command(command)

    def get_stats(self) -> dict[str, Any]:
        """Get renderer statistics."""
        return {
            "connected": self.is_connected(),
            "port": self.config.port,
            "baud": self.config.baud,
            "frame_count": self._frame_count,
            "command_count": self._command_count,
        }

    @staticmethod
    def list_ports() -> list[dict[str, str]]:
        """List available serial ports."""
        if not SERIAL_AVAILABLE:
            return []

        ports = []
        for port in serial.tools.list_ports.comports():
            ports.append({
                "device": port.device,
                "description": port.description,
                "hwid": port.hwid,
            })
        return ports
