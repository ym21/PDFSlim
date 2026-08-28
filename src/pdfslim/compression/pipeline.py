"""Local, stream-safe PDF image compression pipeline."""

from __future__ import annotations

import io
import os
import tempfile
import zlib
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

import pikepdf
import pymupdf
from PIL import Image, ImageChops, ImageFilter, ImageOps

from ..models.compression_result import CompressionResult
from ..models.compression_settings import CompressionSettings
from .analyzer import describe_pdf_error, estimate_effective_dpi


class CompressionCancelled(Exception):
    """Raised internally when the user cancels a running job."""


ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]


def _same_path(left: Path, right: Path) -> bool:
    """Compare paths safely, including a Windows case-insensitive match."""

    try:
        return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))
    except OSError:
        return os.path.normcase(str(left.absolute())) == os.path.normcase(str(right.absolute()))


def output_name(source: str | Path, directory: str | Path) -> Path:
    """Return a non-existing output name without touching the source PDF."""

    source_path = Path(source)
    output_directory = Path(directory)
    candidate = output_directory / f"{source_path.stem}_compressed.pdf"
    counter = 1
    while candidate.exists() or _same_path(candidate, source_path):
        candidate = output_directory / f"{source_path.stem}_compressed_{counter}.pdf"
        counter += 1
    return candidate


def _object_key(reference: Any) -> tuple[str, Any]:
    # pikepdf exposes the source object's (object number, generation) as
    # ``objgen``.  Direct objects have (0, 0), in which case the proxy identity
    # is the best available key.
    try:
        objgen = tuple(reference.objgen)
    except (AttributeError, TypeError, ValueError):
        objgen = ()
    if len(objgen) == 2 and objgen != (0, 0):
        return "objgen", objgen
    return "proxy", id(reference)


def _image_dimensions(reference: Any) -> tuple[int, int] | None:
    try:
        width = int(reference.get("/Width"))
        height = int(reference.get("/Height"))
    except (TypeError, ValueError, KeyError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _needs_grayscale(settings: CompressionSettings) -> bool:
    return settings.enable_grayscale or settings.color_mode == "グレースケール"


def _needs_binarization(settings: CompressionSettings) -> bool:
    return settings.enable_binarization or settings.color_mode == "白黒2値"


def _target_scale(
    page: Any,
    resource_name: Any,
    dimensions: tuple[int, int],
    settings: CompressionSettings,
) -> float:
    """Return a downsampling scale in (0, 1], never an upscale factor."""

    if not settings.enable_downsampling or not settings.target_dpi:
        return 1.0
    source_dpi = estimate_effective_dpi(page, resource_name, dimensions)
    if source_dpi is None or source_dpi <= settings.target_dpi:
        return 1.0
    return min(1.0, settings.target_dpi / source_dpi)


def _auto_crop(image: Image.Image) -> Image.Image:
    """Trim only uniform near-white borders when the option is enabled."""

    if image.width < 3 or image.height < 3:
        return image
    rgb = image.convert("RGB")
    difference = ImageChops.difference(rgb, Image.new("RGB", rgb.size, "white"))
    mask = difference.convert("L").point(lambda value: 255 if value > 12 else 0)
    bounds = mask.getbbox()
    if not bounds or bounds == (0, 0, image.width, image.height):
        return image
    left, top, right, bottom = bounds
    if right - left < 2 or bottom - top < 2:
        return image
    return image.crop(bounds)


def _prepare_image(
    image: Image.Image,
    dimensions: tuple[int, int],
    scale: float,
    settings: CompressionSettings,
) -> Image.Image:
    # Work on a copy: pikepdf/Pillow may keep a reference to decoded buffers.
    prepared = image.copy()
    if scale < 1.0:
        target_size = (
            max(1, int(round(prepared.width * scale))),
            max(1, int(round(prepared.height * scale))),
        )
        if target_size != prepared.size:
            prepared = prepared.resize(target_size, Image.Resampling.LANCZOS)

    if _needs_binarization(settings):
        gray = ImageOps.grayscale(prepared)
        # Local background thresholding avoids turning photographs and shaded
        # scans into a nearly solid black page.
        background = gray.filter(ImageFilter.BoxBlur(9))
        contrast = ImageChops.subtract(gray, background, scale=1, offset=128)
        # Require a meaningful local darkening instead of classifying every
        # below-average photo pixel as black. This keeps text while reducing
        # solid/noisy black regions in photographs and shaded backgrounds.
        prepared = contrast.point(lambda value: 255 if value >= 116 else 0, mode="1")
        gray.close()
        background.close()
        contrast.close()
    elif _needs_grayscale(settings):
        prepared = ImageOps.grayscale(prepared)
    else:
        # JPEG and the lossless fallback below use only these two modes.  This
        # also removes palette/CMYK surprises from the replacement dictionary.
        prepared = prepared.convert("RGB")

    if settings.enable_auto_crop:
        prepared = _auto_crop(prepared)
    return prepared


def _set_image_dictionary(
    reference: Any,
    image: Image.Image,
    filter_name: str,
    *,
    preserve_mask: bool,
) -> None:
    """Update every image dictionary key affected by a new stream."""

    is_gray = image.mode == "L"
    reference["/Width"] = image.width
    reference["/Height"] = image.height
    reference["/ColorSpace"] = pikepdf.Name("/DeviceGray" if is_gray else "/DeviceRGB")
    reference["/BitsPerComponent"] = 8
    reference["/Filter"] = pikepdf.Name(filter_name)
    for key in ("/Decode", "/DecodeParms", "/ColorTransform"):
        if key in reference:
            del reference[key]
    if not preserve_mask:
        for key in ("/SMask", "/Mask"):
            if key in reference:
                del reference[key]


def _write_jpeg(reference: Any, image: Image.Image, quality: int, preserve_mask: bool) -> None:
    encoded = io.BytesIO()
    image.save(encoded, format="JPEG", quality=quality, optimize=True, progressive=False)
    reference.write(encoded.getvalue(), filter=pikepdf.Name("/DCTDecode"))
    _set_image_dictionary(reference, image, "/DCTDecode", preserve_mask=preserve_mask)


def _write_lossless(reference: Any, image: Image.Image, preserve_mask: bool) -> None:
    """Write raw 8-bit samples with Flate when JPEG recompression is off."""

    # A PDF image stream is raw samples, not a PNG file.  Pillow's PNG
    # container cannot be assigned directly to /FlateDecode, so compress the
    # samples ourselves and describe their dimensions explicitly.
    raw = image.tobytes()
    reference.write(zlib.compress(raw), filter=pikepdf.Name("/FlateDecode"))
    _set_image_dictionary(reference, image, "/FlateDecode", preserve_mask=preserve_mask)


def _friendly_image_error(resource_name: Any, exc: BaseException) -> str:
    name = str(resource_name)
    detail = describe_pdf_error(exc)
    return f"画像 {name} を再圧縮できませんでした: {detail}"


def _has_complex_color_images(source: Path) -> bool:
    """Return whether CMYK or palette images need the MuPDF rewrite path."""

    complex_spaces = ("DeviceCMYK", "Indexed", "ICCBased", "Separation", "DeviceN")
    with pikepdf.open(source) as pdf:
        for page in pdf.pages:
            for reference in page.get_images().values():
                if any(name in str(reference.get("/ColorSpace", "")) for name in complex_spaces):
                    return True
    return False


def _rewrite_complex_color_images(
    source: Path,
    temporary: Path,
    settings: CompressionSettings,
    cancel: CancelCallback | None,
    progress: ProgressCallback | None,
) -> tuple[int, int, list[str]]:
    """Safely replace CMYK and other complex images while retaining page structure.

    MuPDF performs the color conversion before Pillow encodes the replacement
    JPEG. This avoids the inverted-CMYK convention mismatch that can otherwise
    produce a photographic negative. Text, vectors, links and page resources
    remain PDF objects instead of being flattened into a page-sized bitmap.
    """

    render_document = pymupdf.open(source)
    processed = 0
    skipped = 0
    warnings: list[str] = []
    try:
        with pikepdf.open(source) as pdf:
            seen: set[tuple[str, Any]] = set()
            total_pages = len(pdf.pages)
            for page_number, page in enumerate(pdf.pages, start=1):
                if cancel and cancel():
                    raise CompressionCancelled()
                for resource_name, reference in page.get_images().items():
                    key = _object_key(reference)
                    if key in seen:
                        continue
                    seen.add(key)
                    dimensions = _image_dimensions(reference)
                    if not dimensions or bool(reference.get("/ImageMask", False)):
                        skipped += 1
                        continue
                    decode = reference.get("/Decode")
                    if decode is not None:
                        try:
                            values = [float(value) for value in decode]
                            unsafe_decode = values != [0.0, 1.0] * (len(values) // 2)
                        except (TypeError, ValueError):
                            unsafe_decode = True
                        if unsafe_decode:
                            skipped += 1
                            warnings.append(f"画像 {resource_name} は非標準のDecode配列のため保持しました。")
                            continue
                    try:
                        xref = int(reference.objgen[0])
                        pixmap = pymupdf.Pixmap(render_document, xref)
                        if pixmap.alpha:
                            pixmap = pymupdf.Pixmap(pixmap, 0)
                        if pixmap.colorspace and pixmap.colorspace.n > 3:
                            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
                        mode = "L" if pixmap.colorspace and pixmap.colorspace.n == 1 else "RGB"
                        image = Image.frombytes(mode, (pixmap.width, pixmap.height), pixmap.samples)
                        try:
                            scale = _target_scale(page, resource_name, dimensions, settings)
                            prepared = _prepare_image(image, dimensions, scale, settings)
                        finally:
                            image.close()
                        try:
                            _write_jpeg(
                                reference,
                                prepared,
                                settings.jpeg_quality,
                                preserve_mask=prepared.size == dimensions,
                            )
                            processed += 1
                        finally:
                            prepared.close()
                    except Exception as exc:
                        skipped += 1
                        warnings.append(_friendly_image_error(resource_name, exc))
                if progress:
                    progress(page_number, total_pages)

            if settings.remove_metadata:
                for key in list(pdf.docinfo.keys()):
                    del pdf.docinfo[key]
                root = getattr(pdf, "Root", None)
                if root is not None and "/Metadata" in root:
                    del root["/Metadata"]
            pdf.save(
                temporary,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
            )
        return processed, skipped, warnings
    finally:
        render_document.close()


def _flatten_pages(
    source: Path,
    temporary: Path,
    settings: CompressionSettings,
    cancel: CancelCallback | None,
    progress: ProgressCallback | None,
) -> int:
    """Render every visible page object into one gray or bitonal image.

    This deliberately changes the PDF structure for the two monochrome modes:
    text, vector graphics, form XObjects, annotations, and background images
    all receive the same color conversion, and their original resources are
    omitted from the rebuilt document.
    """

    source_document = pymupdf.open(source)
    output_document = pymupdf.open()
    try:
        dpi = settings.target_dpi or 200
        total = len(source_document)
        for number, page in enumerate(source_document, 1):
            if cancel and cancel():
                raise CompressionCancelled()
            pixmap = page.get_pixmap(
                dpi=dpi,
                colorspace=pymupdf.csGRAY,
                alpha=False,
                annots=True,
            )
            image = Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)
            if settings.color_mode == "白黒2値":
                rendered_image = image
                grayscale_image = ImageOps.grayscale(rendered_image)
                rendered_image.close()
                background = grayscale_image.filter(ImageFilter.BoxBlur(9))
                contrast = ImageChops.subtract(grayscale_image, background, scale=1, offset=128)
                image = contrast.point(lambda value: 255 if value >= 116 else 0, mode="1")
                grayscale_image.close()
                background.close()
                contrast.close()
                image_format = "PNG"
                save_options: dict[str, Any] = {"optimize": True}
            elif settings.enable_jpeg_recompression:
                image_format = "JPEG"
                save_options = {
                    "quality": settings.jpeg_quality,
                    "optimize": True,
                    "progressive": False,
                }
            else:
                image_format = "PNG"
                save_options = {"optimize": True}
            data = io.BytesIO()
            image.save(data, format=image_format, **save_options)
            new_page = output_document.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(new_page.rect, stream=data.getvalue(), keep_proportion=False)
            image.close()
            if progress:
                progress(number, total)
        if not settings.remove_metadata:
            metadata_keys = {
                "title",
                "author",
                "subject",
                "keywords",
                "creator",
                "producer",
                "creationDate",
                "modDate",
                "trapped",
            }
            metadata = {
                key: value
                for key, value in source_document.metadata.items()
                if key in metadata_keys and value is not None
            }
            output_document.set_metadata(metadata)
        output_document.save(temporary, garbage=4, deflate=True, clean=True)
        return total
    finally:
        source_document.close()
        output_document.close()


def _result(
    source: Path,
    output: Path,
    original_size: int,
    compressed_size: int,
    elapsed: float,
    success: bool,
    error_message: str | None = None,
    warning_message: str | None = None,
    images_processed: int = 0,
    images_skipped: int = 0,
) -> CompressionResult:
    ratio = (original_size - compressed_size) / original_size if original_size else 0.0
    return CompressionResult(
        source_path=source,
        output_path=output,
        original_size=original_size,
        compressed_size=compressed_size,
        reduction_ratio=ratio,
        elapsed_seconds=elapsed,
        success=success,
        error_message=error_message,
        warning_message=warning_message,
        images_processed=images_processed,
        images_skipped=images_skipped,
    )


def compress(
    source: str | Path,
    output: str | Path,
    settings: CompressionSettings,
    cancel: CancelCallback | None = None,
    progress: ProgressCallback | None = None,
) -> CompressionResult:
    """Compress one PDF atomically and return a user-facing result.

    The source is opened read-only from the application's perspective and the
    rewritten document is first saved to a temporary file in the destination
    directory.  The final rename happens only after pikepdf successfully
    writes the complete document, so cancellation or a malformed PDF cannot
    leave a partial output behind.
    """

    started = perf_counter()
    source_path = Path(source)
    output_path = Path(output)
    original_size = 0
    temporary_path: Path | None = None
    processed_images = 0
    skipped_images = 0
    warnings: list[str] = []

    try:
        original_size = source_path.stat().st_size
        if _same_path(source_path, output_path):
            raise ValueError("出力先に元PDF自身を指定することはできません。")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_file = tempfile.NamedTemporaryFile(
            prefix=f".{output_path.stem}.", suffix=".tmp.pdf", dir=output_path.parent, delete=False
        )
        temporary_file.close()
        temporary_path = Path(temporary_file.name)

        safe_complex_rewrite = (
            settings.color_mode == "カラー維持"
            and settings.enable_jpeg_recompression
            and _has_complex_color_images(source_path)
        )
        if safe_complex_rewrite:
            processed_images, skipped_images, rewrite_warnings = _rewrite_complex_color_images(
                source_path,
                temporary_path,
                settings,
                cancel,
                progress,
            )
            warnings.extend(rewrite_warnings)
            warnings.append("CMYKなどの画像は、色反転を防ぐ安全なRGB変換で再圧縮しました。")
            if cancel and cancel():
                raise CompressionCancelled()
            os.replace(temporary_path, output_path)
            temporary_path = None
            compressed_size = output_path.stat().st_size
            if original_size and compressed_size >= original_size:
                warnings.append("圧縮後のファイルは元ファイルより小さくなりませんでした。")
            return _result(
                source_path,
                output_path,
                original_size,
                compressed_size,
                perf_counter() - started,
                True,
                warning_message="\n".join(warnings),
                images_processed=processed_images,
                images_skipped=skipped_images,
            )

        if settings.color_mode in ("グレースケール", "白黒2値"):
            processed_images = _flatten_pages(
                source_path,
                temporary_path,
                settings,
                cancel,
                progress,
            )
            if cancel and cancel():
                raise CompressionCancelled()
            os.replace(temporary_path, output_path)
            temporary_path = None
            compressed_size = output_path.stat().st_size
            if original_size and compressed_size >= original_size:
                warnings.append("圧縮後のファイルは元ファイルより小さくなりませんでした。")
            warning_message = "\n".join(warnings) if warnings else None
            return _result(
                source_path,
                output_path,
                original_size,
                compressed_size,
                perf_counter() - started,
                True,
                warning_message=warning_message,
                images_processed=processed_images,
            )

        with pikepdf.open(source_path) as pdf:
            total_pages = len(pdf.pages)
            seen_images: set[tuple[str, Any]] = set()
            for page_number, page in enumerate(pdf.pages, start=1):
                if cancel and cancel():
                    raise CompressionCancelled()
                for resource_name, reference in list(page.get_images().items()):
                    if cancel and cancel():
                        raise CompressionCancelled()
                    key = _object_key(reference)
                    if key in seen_images:
                        skipped_images += 1
                        continue
                    seen_images.add(key)

                    dimensions = _image_dimensions(reference)
                    if not dimensions or bool(reference.get("/ImageMask", False)):
                        skipped_images += 1
                        continue
                    decode = reference.get("/Decode")
                    unsafe_decode = False
                    if decode is not None:
                        try:
                            values = [float(value) for value in decode]
                            unsafe_decode = values != [0.0, 1.0] * (len(values) // 2)
                        except (TypeError, ValueError):
                            unsafe_decode = True
                    if unsafe_decode:
                        skipped_images += 1
                        warnings.append(f"画像 {resource_name} は非標準のDecode配列のため保持しました。")
                        continue
                    scale = _target_scale(page, resource_name, dimensions, settings)
                    has_transform = (
                        scale < 1.0
                        or settings.enable_auto_crop
                        or _needs_grayscale(settings)
                        or _needs_binarization(settings)
                    )
                    # The explicit checkbox is meaningful: when no other
                    # image transform is requested, leave the original stream
                    # untouched instead of silently JPEG-encoding it.
                    if not settings.enable_jpeg_recompression and not has_transform:
                        skipped_images += 1
                        continue

                    try:
                        pdf_image = pikepdf.PdfImage(reference)
                        image = pdf_image.as_pil_image(apply_mask=False)
                        try:
                            prepared = _prepare_image(image, dimensions, scale, settings)
                        finally:
                            # Decoded images can be large; release the source
                            # Pillow buffer before moving to the next resource.
                            image.close()
                        preserve_mask = prepared.size == dimensions
                        # Binary documents must remain lossless. JPEG would
                        # introduce ringing around glyph edges and create gray
                        # pixels even though the user selected black/white.
                        if settings.enable_jpeg_recompression and not _needs_binarization(settings):
                            _write_jpeg(reference, prepared, settings.jpeg_quality, preserve_mask)
                        else:
                            _write_lossless(reference, prepared, preserve_mask)
                        processed_images += 1
                        prepared.close()
                    except Exception as exc:
                        skipped_images += 1
                        warnings.append(_friendly_image_error(resource_name, exc))
                if progress:
                    progress(page_number, total_pages)

            if settings.remove_metadata:
                for key in list(pdf.docinfo.keys()):
                    del pdf.docinfo[key]
                root = getattr(pdf, "Root", None)
                if root is not None and "/Metadata" in root:
                    del root["/Metadata"]

            # pikepdf/qpdf rewrites the document and removes unreachable
            # objects as part of save.  Linearization is optional because it
            # can increase size for small files.
            pdf.save(temporary_path, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)

        if cancel and cancel():
            raise CompressionCancelled()
        os.replace(temporary_path, output_path)
        temporary_path = None
        compressed_size = output_path.stat().st_size
        if original_size and compressed_size >= original_size:
            warnings.append(
                "圧縮後のファイルは元ファイルより小さくなりませんでした。"
            )
        warning_message = "\n".join(warnings) if warnings else None
        return _result(
            source_path,
            output_path,
            original_size,
            compressed_size,
            perf_counter() - started,
            True,
            warning_message=warning_message,
            images_processed=processed_images,
            images_skipped=skipped_images,
        )
    except CompressionCancelled:
        return _result(
            source_path,
            output_path,
            original_size,
            0,
            perf_counter() - started,
            False,
            error_message="キャンセルされました。",
            images_processed=processed_images,
            images_skipped=skipped_images,
        )
    except Exception as exc:
        return _result(
            source_path,
            output_path,
            original_size,
            0,
            perf_counter() - started,
            False,
            error_message=describe_pdf_error(exc),
            images_processed=processed_images,
            images_skipped=skipped_images,
        )
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                # The primary result already contains the processing error;
                # cleanup failures must not mask it.
                pass
