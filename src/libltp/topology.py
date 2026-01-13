"""Topology utilities for mapping pixel layouts."""

from typing import Any

import numpy as np

from libltp.types import (
    Coordinate,
    CustomTopology,
    LinearTopology,
    MatrixOrigin,
    MatrixOrder,
    MatrixTopology,
    Topology,
    TopologyType,
)


class TopologyMapper:
    """Maps between logical pixel indices and physical/display coordinates."""

    def __init__(self, topology: Topology):
        self.topology = topology
        self._index_to_coord: dict[int, tuple[float, float]] = {}
        self._coord_to_index: dict[tuple[int, int], int] = {}

        self._build_mapping()

    @property
    def pixel_count(self) -> int:
        """Total number of pixels."""
        if isinstance(self.topology, LinearTopology):
            return self.topology.dimensions[0]
        elif isinstance(self.topology, MatrixTopology):
            return self.topology.dimensions[0] * self.topology.dimensions[1]
        else:  # CustomTopology
            return self.topology.pixels

    @property
    def dimensions(self) -> tuple[int, ...]:
        """Get topology dimensions."""
        return tuple(self.topology.dimensions)

    @property
    def is_1d(self) -> bool:
        """Check if topology is one-dimensional."""
        return isinstance(self.topology, LinearTopology)

    @property
    def is_2d(self) -> bool:
        """Check if topology is two-dimensional."""
        return isinstance(self.topology, (MatrixTopology, CustomTopology))

    def _build_mapping(self) -> None:
        """Build the index-to-coordinate mapping."""
        if isinstance(self.topology, LinearTopology):
            self._build_linear_mapping()
        elif isinstance(self.topology, MatrixTopology):
            self._build_matrix_mapping()
        else:  # CustomTopology
            self._build_custom_mapping()

    def _build_linear_mapping(self) -> None:
        """Build mapping for linear topology."""
        length = self.topology.dimensions[0]
        for i in range(length):
            # Normalize to 0-1 range
            x = i / max(length - 1, 1)
            self._index_to_coord[i] = (x, 0.5)

    def _build_matrix_mapping(self) -> None:
        """Build mapping for matrix topology."""
        width, height = self.topology.dimensions
        origin = self.topology.origin
        order = self.topology.order
        serpentine = self.topology.serpentine

        index = 0

        if order == MatrixOrder.ROW_MAJOR:
            rows = range(height)
            cols_base = range(width)

            # Adjust for origin
            if origin in (MatrixOrigin.BOTTOM_LEFT, MatrixOrigin.BOTTOM_RIGHT):
                rows = reversed(rows)
            if origin in (MatrixOrigin.TOP_RIGHT, MatrixOrigin.BOTTOM_RIGHT):
                cols_base = list(reversed(cols_base))

            for row_idx, row in enumerate(rows):
                cols = cols_base
                if serpentine and row_idx % 2 == 1:
                    cols = list(reversed(cols_base))

                for col in cols:
                    x = col / max(width - 1, 1)
                    y = row / max(height - 1, 1)
                    self._index_to_coord[index] = (x, y)
                    self._coord_to_index[(col, row)] = index
                    index += 1
        else:  # COLUMN_MAJOR
            cols = range(width)
            rows_base = range(height)

            if origin in (MatrixOrigin.TOP_RIGHT, MatrixOrigin.BOTTOM_RIGHT):
                cols = reversed(cols)
            if origin in (MatrixOrigin.BOTTOM_LEFT, MatrixOrigin.BOTTOM_RIGHT):
                rows_base = list(reversed(rows_base))

            for col_idx, col in enumerate(cols):
                rows = rows_base
                if serpentine and col_idx % 2 == 1:
                    rows = list(reversed(rows_base))

                for row in rows:
                    x = col / max(width - 1, 1)
                    y = row / max(height - 1, 1)
                    self._index_to_coord[index] = (x, y)
                    self._coord_to_index[(col, row)] = index
                    index += 1

    def _build_custom_mapping(self) -> None:
        """Build mapping for custom topology."""
        for coord in self.topology.coordinates:
            self._index_to_coord[coord.index] = (coord.x, coord.y)

    def index_to_normalized(self, index: int) -> tuple[float, float]:
        """Convert pixel index to normalized (0-1) coordinates."""
        return self._index_to_coord.get(index, (0.0, 0.0))

    def grid_to_index(self, col: int, row: int) -> int | None:
        """Convert grid coordinates to pixel index (for matrix topology)."""
        return self._coord_to_index.get((col, row))

    def index_to_grid(self, index: int) -> tuple[int, int] | None:
        """Convert pixel index to grid coordinates (for matrix topology)."""
        if not isinstance(self.topology, MatrixTopology):
            return None

        for (col, row), idx in self._coord_to_index.items():
            if idx == index:
                return (col, row)
        return None

    def get_all_coordinates(self) -> list[tuple[int, float, float]]:
        """Get all (index, x, y) tuples."""
        return [(i, x, y) for i, (x, y) in sorted(self._index_to_coord.items())]

    def to_dict(self) -> dict[str, Any]:
        """Export topology as dictionary."""
        return self.topology.model_dump()


def create_linear_topology(length: int) -> LinearTopology:
    """Create a linear (1D) topology."""
    return LinearTopology(dimensions=(length,))


def create_matrix_topology(
    width: int,
    height: int,
    origin: MatrixOrigin = MatrixOrigin.TOP_LEFT,
    order: MatrixOrder = MatrixOrder.ROW_MAJOR,
    serpentine: bool = False,
) -> MatrixTopology:
    """Create a matrix (2D) topology."""
    return MatrixTopology(
        dimensions=(width, height),
        origin=origin,
        order=order,
        serpentine=serpentine,
    )


def create_custom_topology(coordinates: list[tuple[int, float, float]]) -> CustomTopology:
    """Create a custom topology from (index, x, y) tuples."""
    coords = [
        Coordinate(index=idx, x=x, y=y)
        for idx, x, y in coordinates
    ]
    return CustomTopology(
        pixels=len(coordinates),
        coordinates=coords,
    )


def topology_from_dict(data: dict[str, Any]) -> Topology:
    """Create topology from dictionary."""
    topology_type = TopologyType(data.get("topology", "linear"))

    if topology_type == TopologyType.LINEAR:
        return LinearTopology(**data)
    elif topology_type == TopologyType.MATRIX:
        return MatrixTopology(**data)
    else:
        return CustomTopology(**data)


class PixelBuffer:
    """Pixel buffer with topology awareness."""

    def __init__(
        self,
        topology: Topology,
        channels: int = 3,
        dtype: np.dtype = np.uint8,
    ):
        self.topology = topology
        self.mapper = TopologyMapper(topology)
        self.channels = channels

        # Create buffer
        if isinstance(topology, MatrixTopology):
            width, height = topology.dimensions
            self._buffer = np.zeros((height, width, channels), dtype=dtype)
            self._is_2d = True
        else:
            self._buffer = np.zeros((self.mapper.pixel_count, channels), dtype=dtype)
            self._is_2d = False

    @property
    def buffer(self) -> np.ndarray:
        """Get the underlying numpy buffer."""
        return self._buffer

    @property
    def flat(self) -> np.ndarray:
        """Get buffer as flat (pixels, channels) array."""
        if self._is_2d:
            return self._buffer.reshape(-1, self.channels)
        return self._buffer

    def clear(self, color: tuple[int, ...] | None = None) -> None:
        """Clear the buffer to a color (default black)."""
        if color:
            self._buffer[:] = color
        else:
            self._buffer.fill(0)

    def set_pixel(self, index: int, color: tuple[int, ...]) -> None:
        """Set a pixel by index."""
        if self._is_2d:
            grid = self.mapper.index_to_grid(index)
            if grid:
                col, row = grid
                self._buffer[row, col] = color
        else:
            if 0 <= index < len(self._buffer):
                self._buffer[index] = color

    def get_pixel(self, index: int) -> np.ndarray:
        """Get a pixel by index."""
        if self._is_2d:
            grid = self.mapper.index_to_grid(index)
            if grid:
                col, row = grid
                return self._buffer[row, col]
            return np.zeros(self.channels, dtype=self._buffer.dtype)
        else:
            if 0 <= index < len(self._buffer):
                return self._buffer[index]
            return np.zeros(self.channels, dtype=self._buffer.dtype)

    def set_grid(self, col: int, row: int, color: tuple[int, ...]) -> None:
        """Set a pixel by grid coordinates (for 2D topologies)."""
        if self._is_2d:
            height, width = self._buffer.shape[:2]
            if 0 <= col < width and 0 <= row < height:
                self._buffer[row, col] = color

    def get_grid(self, col: int, row: int) -> np.ndarray:
        """Get a pixel by grid coordinates (for 2D topologies)."""
        if self._is_2d:
            height, width = self._buffer.shape[:2]
            if 0 <= col < width and 0 <= row < height:
                return self._buffer[row, col]
        return np.zeros(self.channels, dtype=self._buffer.dtype)

    def to_stream_order(self) -> np.ndarray:
        """Convert buffer to stream order based on topology mapping."""
        if self._is_2d and isinstance(self.topology, MatrixTopology):
            # Reorder pixels according to topology mapping
            result = np.zeros((self.mapper.pixel_count, self.channels), dtype=self._buffer.dtype)
            for index in range(self.mapper.pixel_count):
                grid = self.mapper.index_to_grid(index)
                if grid:
                    col, row = grid
                    result[index] = self._buffer[row, col]
            return result
        return self.flat

    def from_stream_order(self, data: np.ndarray) -> None:
        """Load buffer from stream-ordered data."""
        if self._is_2d and isinstance(self.topology, MatrixTopology):
            for index in range(min(len(data), self.mapper.pixel_count)):
                grid = self.mapper.index_to_grid(index)
                if grid:
                    col, row = grid
                    self._buffer[row, col] = data[index]
        else:
            count = min(len(data), len(self._buffer))
            self._buffer[:count] = data[:count]


def scale_buffer(
    source: np.ndarray,
    target_shape: tuple[int, ...],
    mode: str = "fit",
) -> np.ndarray:
    """Scale a pixel buffer to a new shape.

    Args:
        source: Source buffer (pixels, channels) or (height, width, channels)
        target_shape: Target shape
        mode: "fit", "fill", "stretch", or "none"

    Returns:
        Scaled buffer
    """
    from scipy.ndimage import zoom

    if mode == "none":
        # Just truncate or pad
        result = np.zeros(target_shape + (source.shape[-1],), dtype=source.dtype)
        copy_shape = tuple(min(s, t) for s, t in zip(source.shape[:-1], target_shape))
        slices = tuple(slice(0, s) for s in copy_shape)
        result[slices] = source[slices]
        return result

    # Handle 1D to 1D scaling
    if source.ndim == 2 and len(target_shape) == 1:
        src_len = source.shape[0]
        tgt_len = target_shape[0]
        if src_len == tgt_len:
            return source.copy()

        # Simple linear interpolation
        indices = np.linspace(0, src_len - 1, tgt_len)
        result = np.zeros((tgt_len, source.shape[1]), dtype=source.dtype)
        for i, idx in enumerate(indices):
            low = int(idx)
            high = min(low + 1, src_len - 1)
            frac = idx - low
            result[i] = (1 - frac) * source[low] + frac * source[high]
        return result.astype(source.dtype)

    # For 2D scaling, use scipy zoom
    if source.ndim == 3 and len(target_shape) == 2:
        src_h, src_w = source.shape[:2]
        tgt_h, tgt_w = target_shape

        if mode == "stretch":
            zoom_h = tgt_h / src_h
            zoom_w = tgt_w / src_w
        elif mode == "fit":
            scale = min(tgt_h / src_h, tgt_w / src_w)
            zoom_h = zoom_w = scale
        else:  # fill
            scale = max(tgt_h / src_h, tgt_w / src_w)
            zoom_h = zoom_w = scale

        # Zoom each channel
        result = np.zeros((tgt_h, tgt_w, source.shape[2]), dtype=source.dtype)
        zoomed = zoom(source, (zoom_h, zoom_w, 1), order=1)

        # Center crop/pad
        zh, zw = zoomed.shape[:2]
        src_y = max(0, (zh - tgt_h) // 2)
        src_x = max(0, (zw - tgt_w) // 2)
        dst_y = max(0, (tgt_h - zh) // 2)
        dst_x = max(0, (tgt_w - zw) // 2)

        copy_h = min(zh - src_y, tgt_h - dst_y)
        copy_w = min(zw - src_x, tgt_w - dst_x)

        result[dst_y : dst_y + copy_h, dst_x : dst_x + copy_w] = zoomed[
            src_y : src_y + copy_h, src_x : src_x + copy_w
        ]
        return result

    return source.copy()
