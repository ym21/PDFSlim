from pdfslim.models.compression_settings import CompressionSettings


def test_presets_have_expected_ranges() -> None:
    high = CompressionSettings.from_preset("高画質")
    small = CompressionSettings.from_preset("サイズ優先")

    assert 250 <= high.target_dpi <= 300
    assert 80 <= high.jpeg_quality <= 90
    assert small.jpeg_quality < high.jpeg_quality
    assert small.enable_grayscale
    assert small.to_dict()["preset"] == "サイズ優先"
