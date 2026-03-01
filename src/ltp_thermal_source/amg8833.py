"""AMG8833 Grid-EYE infrared thermal sensor driver."""

import logging
import struct

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
    """

    def __init__(self, bus: int = 1, address: int = 0x69):
        """Open the I2C bus and initialize the sensor.

        Args:
            bus: I2C bus number (default: 1 for /dev/i2c-1)
            address: I2C address (default: 0x69, AD_SELECT high)
        """
        self._address = address
        self._bus = SMBus(bus)
        logger.info(f"AMG8833 opened on bus {bus}, address 0x{address:02X}")

    def read_pixels(self) -> list[float]:
        """Read all 64 pixel temperatures.

        Returns:
            List of 64 temperature values in °C, row-major order (top-left to bottom-right).
        """
        # Read 128 bytes (64 pixels x 2 bytes each)
        raw = self._bus.read_i2c_block_data(self._address, _REG_PIXELS, 128)

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
        """
        raw = self._bus.read_i2c_block_data(self._address, _REG_THERMISTOR, 2)
        raw_val = ((raw[1] & 0x0F) << 8) | raw[0]
        if raw[1] & 0x08:  # sign bit
            raw_val -= 4096
        return raw_val * _THERMISTOR_CONVERSION

    def close(self) -> None:
        """Close the I2C bus."""
        self._bus.close()
        logger.info("AMG8833 closed")
