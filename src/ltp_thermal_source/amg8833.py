"""AMG8833 Grid-EYE infrared thermal sensor driver."""

import logging
import time

from smbus2 import SMBus

logger = logging.getLogger(__name__)

# Register addresses
_REG_THERMISTOR = 0x0E  # 2 bytes, on-chip temperature
_REG_PIXELS = 0x80  # 128 bytes, 64 pixels x 2 bytes each

# Temperature resolution
_PIXEL_TEMP_CONVERSION = 0.25  # °C per LSB (12-bit signed)
_THERMISTOR_CONVERSION = 0.0625  # °C per LSB (12-bit signed)


class AMG8833:
    """Driver for the Panasonic AMG8833 Grid-EYE 8x8 infrared thermal sensor.

    Communicates via I2C/SMBus. The sensor provides 64 temperature readings
    in an 8x8 grid, plus an on-chip thermistor for ambient temperature.

    Automatically reopens the I2C bus after errors with exponential backoff.
    """

    def __init__(self, bus: int = 1, address: int = 0x69):
        """Open the I2C bus and initialize the sensor.

        Args:
            bus: I2C bus number (default: 1 for /dev/i2c-1)
            address: I2C address (default: 0x69, AD_SELECT high)
        """
        self._address = address
        self._bus_num = bus
        self._bus: SMBus | None = None
        self._consecutive_errors = 0
        self._reopen_after: float = 0.0  # monotonic time to wait until
        self._open_bus()

    def _open_bus(self) -> None:
        """Open (or reopen) the I2C bus."""
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:
                pass
        self._bus = SMBus(self._bus_num)
        logger.info(f"AMG8833 opened on bus {self._bus_num}, address 0x{self._address:02X}")

    def _handle_error(self) -> None:
        """Handle a bus error: close bus, set backoff delay before reopen."""
        self._consecutive_errors += 1
        # Exponential backoff: 0.25s, 0.5s, 1s, 2s (capped)
        delay = min(2.0, 0.25 * (2 ** (self._consecutive_errors - 1)))
        self._reopen_after = time.monotonic() + delay
        logger.warning(f"I2C error, will reopen bus after {delay:.2f}s backoff")
        try:
            self._bus.close()
        except Exception:
            pass
        self._bus = None

    def _ensure_bus(self) -> bool:
        """Ensure the bus is open. Returns False if still in backoff."""
        if self._bus is not None:
            return True
        if time.monotonic() < self._reopen_after:
            return False
        try:
            self._open_bus()
            return True
        except Exception as e:
            logger.error(f"Failed to reopen I2C bus: {e}")
            self._reopen_after = time.monotonic() + 2.0
            return False

    def read_pixels(self) -> list[float]:
        """Read all 64 pixel temperatures.

        Returns:
            List of 64 temperature values in °C, row-major order.

        Raises:
            OSError: If the bus is unavailable or the read fails.
        """
        if not self._ensure_bus():
            raise OSError("I2C bus unavailable (backoff)")

        try:
            # SMBus limits reads to 32 bytes per transaction.
            # Read 128 bytes in 4 chunks of 32 bytes each.
            raw = []
            for offset in range(0, 128, 32):
                raw.extend(
                    self._bus.read_i2c_block_data(self._address, _REG_PIXELS + offset, 32)
                )
        except OSError:
            self._handle_error()
            raise

        self._consecutive_errors = 0

        temps = []
        for i in range(64):
            lo = raw[2 * i]
            hi = raw[2 * i + 1]
            # 12-bit signed value: bits [11:0], sign in bit 11
            raw_val = ((hi & 0x0F) << 8) | lo
            if hi & 0x08:  # sign bit (bit 11)
                raw_val -= 4096
            temps.append(raw_val * _PIXEL_TEMP_CONVERSION)

        return temps

    def read_thermistor(self) -> float:
        """Read the on-chip thermistor (ambient temperature).

        Returns:
            Ambient temperature in °C.

        Raises:
            OSError: If the bus is unavailable or the read fails.
        """
        if not self._ensure_bus():
            raise OSError("I2C bus unavailable (backoff)")

        try:
            raw = self._bus.read_i2c_block_data(self._address, _REG_THERMISTOR, 2)
        except OSError:
            self._handle_error()
            raise

        self._consecutive_errors = 0
        raw_val = ((raw[1] & 0x0F) << 8) | raw[0]
        if raw[1] & 0x08:  # sign bit
            raw_val -= 4096
        return raw_val * _THERMISTOR_CONVERSION

    def close(self) -> None:
        """Close the I2C bus."""
        if self._bus is not None:
            self._bus.close()
            self._bus = None
        logger.info("AMG8833 closed")
