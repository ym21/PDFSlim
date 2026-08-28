"""User-selectable compression settings and the built-in presets."""

from dataclasses import asdict, dataclass


COLOR_MODES = ("カラー維持", "グレースケール", "白黒2値")

# The values are intentionally inside the ranges documented in the
# specification.  Individual controls can still be changed after selecting a
# preset.
PRESETS = {
    "高画質": dict(
        target_dpi=300,
        jpeg_quality=88,
        color_mode="カラー維持",
        enable_downsampling=True,
        enable_jpeg_recompression=True,
        enable_grayscale=False,
        enable_binarization=False,
        enable_auto_crop=False,
        remove_metadata=False,
    ),
    "バランス": dict(
        target_dpi=180,
        jpeg_quality=72,
        color_mode="カラー維持",
        enable_downsampling=True,
        enable_jpeg_recompression=True,
        enable_grayscale=False,
        enable_binarization=False,
        enable_auto_crop=False,
        remove_metadata=False,
    ),
    "サイズ優先": dict(
        target_dpi=120,
        jpeg_quality=52,
        color_mode="グレースケール",
        enable_downsampling=True,
        enable_jpeg_recompression=True,
        enable_grayscale=True,
        enable_binarization=False,
        enable_auto_crop=False,
        remove_metadata=False,
    ),
    "文書": dict(
        target_dpi=180,
        jpeg_quality=65,
        color_mode="グレースケール",
        enable_downsampling=True,
        enable_jpeg_recompression=True,
        enable_grayscale=True,
        enable_binarization=False,
        enable_auto_crop=False,
        remove_metadata=False,
    ),
}


@dataclass
class CompressionSettings:
    preset: str = "バランス"
    target_dpi: int | None = 180
    jpeg_quality: int = 72
    color_mode: str = "カラー維持"
    enable_downsampling: bool = True
    enable_jpeg_recompression: bool = True
    enable_grayscale: bool = False
    enable_binarization: bool = False
    enable_auto_crop: bool = False
    remove_metadata: bool = False

    def __post_init__(self) -> None:
        if self.target_dpi is not None:
            self.target_dpi = int(self.target_dpi)
            if self.target_dpi <= 0:
                self.target_dpi = None
        self.jpeg_quality = max(1, min(100, int(self.jpeg_quality)))
        if self.color_mode not in COLOR_MODES:
            raise ValueError(f"unknown color mode: {self.color_mode}")

        # Color mode is the single source of truth.  Keep legacy fields for
        # serialized settings, but canonicalize them so they cannot conflict.
        self.enable_grayscale = self.color_mode == "グレースケール"
        self.enable_binarization = self.color_mode == "白黒2値"

    @classmethod
    def from_preset(cls, name: str) -> "CompressionSettings":
        if name not in PRESETS:
            raise ValueError(f"unknown preset: {name}")
        return cls(preset=name, **PRESETS[name])

    def to_dict(self) -> dict:
        return asdict(self)
