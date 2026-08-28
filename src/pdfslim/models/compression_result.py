"""Results returned by the compression engine.

The GUI deliberately consumes a small, serialisable data object instead of
depending on pikepdf objects.  This also makes it possible to use the engine
from a command line wrapper or a future background process.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class CompressionResult:
    source_path: Path
    output_path: Path
    original_size: int
    compressed_size: int
    reduction_ratio: float
    elapsed_seconds: float
    success: bool
    error_message: str | None = None
    warning_message: str | None = None
    images_processed: int = 0
    images_skipped: int = 0

    @property
    def reduction_percent(self) -> float:
        """Return the reduction as a percentage for display."""

        return self.reduction_ratio * 100.0
