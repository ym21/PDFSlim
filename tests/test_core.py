from __future__ import annotations

import io
from pathlib import Path

import pytest

# The integration tests exercise real pikepdf image dictionaries.  Keep the
# dependency check before importing the compression module so a developer can
# still run the dependency-free settings tests when pikepdf is not installed.
pikepdf = pytest.importorskip("pikepdf")

from pdfslim.compression.pipeline import compress, output_name
from pdfslim.models.compression_settings import CompressionSettings


def test_output_name_avoids_collisions_and_source_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "a.pdf"
    source.write_bytes(b"source")
    assert output_name(source, tmp_path).name == "a_compressed.pdf"

    (tmp_path / "a_compressed.pdf").write_bytes(b"existing")
    assert output_name(source, tmp_path).name == "a_compressed_1.pdf"


def _make_image_pdf(path: Path, width: int = 1200, height: int = 800) -> None:
    from PIL import Image

    image = Image.new("RGB", (width, height), (30, 120, 220))
    image_bytes = io.BytesIO()
    image.save(image_bytes, format="JPEG", quality=95)

    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(72, 48))
    image_stream = pdf.make_stream(image_bytes.getvalue())
    image_stream["/Type"] = pikepdf.Name("/XObject")
    image_stream["/Subtype"] = pikepdf.Name("/Image")
    image_stream["/Width"] = width
    image_stream["/Height"] = height
    image_stream["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
    image_stream["/BitsPerComponent"] = 8
    image_stream["/Filter"] = pikepdf.Name("/DCTDecode")
    resources = pikepdf.Dictionary()
    xobjects = pikepdf.Dictionary()
    xobjects["/Im0"] = image_stream
    resources["/XObject"] = xobjects
    page.obj["/Resources"] = resources
    page.obj["/Contents"] = pdf.make_stream(b"q 72 0 0 48 0 0 cm /Im0 Do Q")
    pdf.save(path)


def test_compress_replaces_image_stream_and_does_not_touch_source(tmp_path: Path) -> None:
    source = tmp_path / "scan.pdf"
    destination = tmp_path / "scan_compressed.pdf"
    _make_image_pdf(source)
    original_bytes = source.read_bytes()
    settings = CompressionSettings(
        target_dpi=150,
        jpeg_quality=80,
        color_mode="カラー維持",
        enable_downsampling=True,
        enable_jpeg_recompression=True,
    )

    result = compress(source, destination, settings)

    assert result.success, result.error_message
    assert destination.exists()
    assert source.read_bytes() == original_bytes
    with pikepdf.open(destination) as pdf:
        image = next(iter(pdf.pages[0].get_images().values()))
        assert int(image["/Width"]) == 150
        assert int(image["/Height"]) == 100
        assert str(image["/Filter"]) == "/DCTDecode"
        assert str(image["/ColorSpace"]) == "/DeviceRGB"
        assert int(image["/BitsPerComponent"]) == 8
        assert "/DecodeParms" not in image
        assert pikepdf.PdfImage(image).as_pil_image(apply_mask=False).size == (150, 100)


def test_jpeg_off_preserves_pixels_and_uses_lossless_transform(tmp_path: Path) -> None:
    source = tmp_path / "scan.pdf"
    untouched = tmp_path / "untouched.pdf"
    grayscale = tmp_path / "grayscale.pdf"
    _make_image_pdf(source, width=240, height=160)
    no_change = CompressionSettings(
        target_dpi=None,
        enable_downsampling=False,
        enable_jpeg_recompression=False,
        color_mode="カラー維持",
    )
    result = compress(source, untouched, no_change)
    assert result.success, result.error_message
    with pikepdf.open(untouched) as pdf:
        image = next(iter(pdf.pages[0].get_images().values()))
        assert str(image["/Filter"]) == "/DCTDecode"

    lossless = CompressionSettings(
        target_dpi=None,
        enable_downsampling=False,
        enable_jpeg_recompression=False,
        color_mode="グレースケール",
        enable_grayscale=True,
    )
    result = compress(source, grayscale, lossless)
    assert result.success, result.error_message
    with pikepdf.open(grayscale) as pdf:
        image = next(iter(pdf.pages[0].get_images().values()))
        assert str(image["/Filter"]) == "/FlateDecode"
        assert str(image["/ColorSpace"]) == "/DeviceGray"
        assert pikepdf.PdfImage(image).as_pil_image(apply_mask=False).mode == "L"


def test_corrupt_pdf_reports_error_and_leaves_no_output(tmp_path: Path) -> None:
    source = tmp_path / "broken.pdf"
    destination = tmp_path / "broken_compressed.pdf"
    source.write_bytes(b"not a pdf")

    result = compress(source, destination, CompressionSettings())

    assert not result.success
    assert result.error_message
    assert not destination.exists()


def test_cancelled_job_cleans_temporary_output(tmp_path: Path) -> None:
    source = tmp_path / "scan.pdf"
    destination = tmp_path / "scan_compressed.pdf"
    _make_image_pdf(source)

    result = compress(source, destination, CompressionSettings(), cancel=lambda: True)

    assert not result.success
    assert result.error_message == "キャンセルされました。"
    assert not destination.exists()
    assert not list(tmp_path.glob(".*.tmp.pdf"))
