from __future__ import annotations

import io
from pathlib import Path

import pytest

# The integration tests exercise real pikepdf image dictionaries.  Keep the
# dependency check before importing the compression module so a developer can
# still run the dependency-free settings tests when pikepdf is not installed.
pikepdf = pytest.importorskip("pikepdf")
pymupdf = pytest.importorskip("pymupdf")

from pdfslim.compression.pipeline import compress, output_name
from pdfslim.models.compression_settings import CompressionSettings


def test_output_name_avoids_collisions_and_source_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "a.pdf"
    source.write_bytes(b"source")
    assert output_name(source, tmp_path).name == "a_compressed.pdf"

    (tmp_path / "a_compressed.pdf").write_bytes(b"existing")
    assert output_name(source, tmp_path).name == "a_compressed_1.pdf"


def _jpeg_bytes(image) -> bytes:
    image_bytes = io.BytesIO()
    image.save(image_bytes, format="JPEG", quality=95)
    return image_bytes.getvalue()


def _make_image_pdf(
    path: Path,
    width: int = 1200,
    height: int = 800,
    *,
    inverted_decode: bool = False,
    mixed_content: bool = False,
) -> None:
    from PIL import Image, ImageDraw, ImageOps

    image = Image.new("RGB", (width, height), (30, 120, 220))
    if mixed_content:
        image = Image.new("RGB", (width, height), (238, 246, 255))
        drawing = ImageDraw.Draw(image)
        drawing.ellipse(
            (width // 10, height // 10, width * 9 // 10, height * 3 // 4),
            fill=(255, 220, 90),
            outline=(20, 100, 180),
            width=max(4, width // 50),
        )
        drawing.rectangle(
            (width // 8, height * 4 // 5, width * 7 // 8, height * 9 // 10),
            fill=(80, 170, 100),
        )
    stored_image = ImageOps.invert(image) if inverted_decode else image

    pdf = pikepdf.Pdf.new()
    page_size = (612, 792) if mixed_content else (72, 48)
    page = pdf.add_blank_page(page_size=page_size)
    image_stream = pdf.make_stream(_jpeg_bytes(stored_image))
    image_stream["/Type"] = pikepdf.Name("/XObject")
    image_stream["/Subtype"] = pikepdf.Name("/Image")
    image_stream["/Width"] = width
    image_stream["/Height"] = height
    image_stream["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
    image_stream["/BitsPerComponent"] = 8
    image_stream["/Filter"] = pikepdf.Name("/DCTDecode")
    if inverted_decode:
        image_stream["/Decode"] = pikepdf.Array([1, 0, 1, 0, 1, 0])
    resources = pikepdf.Dictionary()
    xobjects = pikepdf.Dictionary()
    xobjects["/Im0"] = image_stream
    resources["/XObject"] = xobjects
    if mixed_content:
        resources["/Font"] = pikepdf.Dictionary(
            F1=pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"),
                Subtype=pikepdf.Name("/Type1"),
                BaseFont=pikepdf.Name("/Helvetica-Bold"),
            )
        )
    page.obj["/Resources"] = resources
    if mixed_content:
        page.obj["/Contents"] = pdf.make_stream(
            b"q 612 0 0 792 0 0 cm /Im0 Do Q\n"
            b"0.05 0.55 0.95 rg 54 510 504 130 re f\n"
            b"0.9 0.1 0.15 rg BT /F1 30 Tf 76 590 Td (Colored vector text) Tj ET\n"
        )
    else:
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


def _render_rgb(path: Path, dpi: int = 96) -> bytes:
    with pymupdf.open(path) as document:
        pixmap = document[0].get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
        return pixmap.samples


def test_jpeg_off_preserves_pixels_and_grayscale_flattens_page(tmp_path: Path) -> None:
    source = tmp_path / "scan.pdf"
    untouched = tmp_path / "untouched.pdf"
    grayscale = tmp_path / "grayscale.pdf"
    _make_image_pdf(source, width=900, height=1200, inverted_decode=True, mixed_content=True)
    original_bytes = source.read_bytes()
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
    assert source.read_bytes() == original_bytes
    with pikepdf.open(grayscale) as pdf:
        page = pdf.pages[0]
        assert len(page.get_images()) == 1
        assert "/Font" not in page["/Resources"]
    with pymupdf.open(grayscale) as document:
        assert document[0].get_text().strip() == ""
    pixels = _render_rgb(grayscale)
    assert all(red == green == blue for red, green, blue in zip(pixels[0::3], pixels[1::3], pixels[2::3]))
    assert max(pixels) > 200


def test_binarization_is_lossless_even_when_jpeg_is_enabled(tmp_path: Path) -> None:
    source = tmp_path / "scan.pdf"
    destination = tmp_path / "binary.pdf"
    _make_image_pdf(source, width=900, height=1200, inverted_decode=True, mixed_content=True)
    settings = CompressionSettings(
        target_dpi=None,
        enable_downsampling=False,
        enable_jpeg_recompression=True,
        color_mode="白黒2値",
        enable_binarization=True,
    )

    result = compress(source, destination, settings)

    assert result.success, result.error_message
    with pikepdf.open(destination) as pdf:
        page = pdf.pages[0]
        assert len(page.get_images()) == 1
        assert "/Font" not in page["/Resources"]
        image = next(iter(page.get_images().values()))
        pixels = set(pikepdf.PdfImage(image).as_pil_image(apply_mask=False).convert("L").tobytes())
        assert document_text(destination) == ""
        assert pixels <= {0, 255}
        assert pixels == {0, 255}


def document_text(path: Path) -> str:
    with pymupdf.open(path) as document:
        return "".join(page.get_text() for page in document)


def test_non_identity_decode_image_is_preserved_without_inversion(tmp_path: Path) -> None:
    source = tmp_path / "decoded.pdf"
    destination = tmp_path / "decoded_compressed.pdf"
    _make_image_pdf(source, width=240, height=160, inverted_decode=True)
    before = _render_rgb(source)
    settings = CompressionSettings(
        target_dpi=96,
        enable_downsampling=True,
        enable_jpeg_recompression=True,
        color_mode="カラー維持",
    )

    result = compress(source, destination, settings)

    assert result.success, result.error_message
    assert result.warning_message and "Decode" in result.warning_message
    assert _render_rgb(destination) == before
    with pikepdf.open(destination) as pdf:
        image = next(iter(pdf.pages[0].get_images().values()))
        assert list(image["/Decode"]) == [1, 0, 1, 0, 1, 0]


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

    result = compress(
        source,
        destination,
        CompressionSettings(color_mode="グレースケール"),
        cancel=lambda: True,
    )

    assert not result.success
    assert result.error_message == "キャンセルされました。"
    assert not destination.exists()
    assert not list(tmp_path.glob(".*.tmp.pdf"))
