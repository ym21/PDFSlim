from pdfslim.models.compression_settings import CompressionSettings


def test_presets_have_expected_ranges() -> None:
    high = CompressionSettings.from_preset("高画質")
    small = CompressionSettings.from_preset("サイズ優先")

    assert 250 <= high.target_dpi <= 300
    assert 80 <= high.jpeg_quality <= 90
    assert small.jpeg_quality < high.jpeg_quality
    assert small.enable_grayscale
    assert small.to_dict()["preset"] == "サイズ優先"


def test_color_mode_is_the_only_source_of_truth() -> None:
    grayscale = CompressionSettings(
        color_mode="グレースケール",
        enable_grayscale=False,
        enable_binarization=True,
    )
    binary = CompressionSettings(
        color_mode="白黒2値",
        enable_grayscale=True,
        enable_binarization=False,
    )
    color = CompressionSettings(
        color_mode="カラー維持",
        enable_grayscale=True,
        enable_binarization=True,
    )

    assert (grayscale.enable_grayscale, grayscale.enable_binarization) == (True, False)
    assert (binary.enable_grayscale, binary.enable_binarization) == (False, True)
    assert (color.enable_grayscale, color.enable_binarization) == (False, False)
