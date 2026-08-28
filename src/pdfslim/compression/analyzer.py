"""Read-only PDF analysis helpers used by the GUI and compression engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import pikepdf


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _page_size_points(page: Any) -> tuple[float, float] | None:
    """Return the crop/media box size in points when it is available."""

    for key in ("/CropBox", "/MediaBox"):
        box = page.get(key)
        if box is None or len(box) < 4:
            continue
        try:
            width = abs(float(box[2]) - float(box[0]))
            height = abs(float(box[3]) - float(box[1]))
        except (TypeError, ValueError):
            continue
        if width > 0 and height > 0:
            return width, height
    return None


def _matrix_multiply(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, ...]:
    """Multiply two PDF affine matrices represented as ``a b c d e f``."""

    a, b, c, d, e, f = left
    g, h, i, j, k, l = right
    return (
        a * g + c * h,
        b * g + d * h,
        a * i + c * j,
        b * i + d * j,
        a * k + c * l + e,
        b * k + d * l + f,
    )


def _operator_name(operator: Any) -> str:
    value = str(operator)
    return value[1:] if value.startswith("/") else value


def _resource_name(value: Any) -> str:
    value = str(value)
    return value if value.startswith("/") else f"/{value}"


def image_placements(page: Any) -> dict[str, list[tuple[float, float]]]:
    """Return displayed image sizes in points keyed by XObject name.

    Content streams can contain arbitrary PDF operators and nested form
    XObjects.  We intentionally handle the common page-level ``q/cm/Do/Q``
    sequence and return an empty mapping if a producer uses a construct that
    pikepdf cannot parse.  The caller then uses the documented full-page
    approximation instead of making a risky upscale decision.
    """

    placements: dict[str, list[tuple[float, float]]] = {}
    identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    matrix = identity
    stack: list[tuple[float, ...]] = []

    try:
        operations: Iterable[tuple[Any, Any]] = pikepdf.parse_content_stream(page)
        for operands, operator in operations:
            name = _operator_name(operator)
            if name == "q":
                stack.append(matrix)
            elif name == "Q":
                matrix = stack.pop() if stack else identity
            elif name == "cm" and len(operands) >= 6:
                values = tuple(float(operands[index]) for index in range(6))
                matrix = _matrix_multiply(matrix, values)
            elif name == "Do" and operands:
                image_width = (matrix[0] ** 2 + matrix[1] ** 2) ** 0.5
                image_height = (matrix[2] ** 2 + matrix[3] ** 2) ** 0.5
                if image_width > 0 and image_height > 0:
                    resource = _resource_name(operands[0])
                    placements.setdefault(resource, []).append((image_width, image_height))
    except Exception:
        return {}
    return placements


def estimate_effective_dpi(
    page: Any,
    resource_name: Any,
    image_size: tuple[int, int],
) -> float | None:
    """Estimate source DPI without ever requiring an image upscale.

    When a page content stream contains image placement matrices, the largest
    displayed size (the lowest effective DPI) is used.  If placement cannot be
    read, the image is conservatively assumed to fill the page's crop/media
    box.  If even that approximation is unavailable, ``None`` is returned and
    the caller leaves the pixels untouched.
    """

    width, height = image_size
    if width <= 0 or height <= 0:
        return None

    placements = image_placements(page).get(_resource_name(resource_name), [])
    if placements:
        dpis = [
            min(width * 72.0 / displayed_width, height * 72.0 / displayed_height)
            for displayed_width, displayed_height in placements
            if displayed_width > 0 and displayed_height > 0
        ]
        if dpis:
            return min(dpis)

    # PDF has no universal image-DPI metadata.  This full-page fallback is
    # deliberately conservative and is also used for malformed/unusual
    # content streams; in either case the caller clamps the resize scale to
    # <= 1, so low-resolution images are never upscaled.
    page_size = _page_size_points(page)
    if page_size:
        page_width, page_height = page_size
        return min(width * 72.0 / page_width, height * 72.0 / page_height)
    return None


def _colour_bucket(color_space: Any) -> str:
    text = str(color_space or "")
    if "DeviceGray" in text or "CalGray" in text or ("ICCBased" in text and "1" in text):
        return "グレースケール"
    if "Indexed" in text or "DeviceRGB" in text or "DeviceCMYK" in text:
        return "カラー"
    # Unknown color spaces are safer to display as color than to promise that
    # no chroma is present.
    return "カラー"


def describe_pdf_error(exc: BaseException) -> str:
    """Convert common pikepdf/filesystem errors into actionable Japanese."""

    password_error = getattr(pikepdf, "PasswordError", None)
    if password_error and isinstance(exc, password_error):
        return "パスワードで保護されたPDFです。解除済みのPDFを指定してください。"
    name = type(exc).__name__.lower()
    message = str(exc).strip()
    if "password" in name or "password" in message.lower() or "encrypted" in message.lower():
        return "パスワードで保護されたPDFです。解除済みのPDFを指定してください。"
    if isinstance(exc, PermissionError):
        return "PDFまたは保存先にアクセスする権限がありません。"
    if isinstance(exc, FileNotFoundError):
        return "指定されたPDFが見つかりません。"
    if "pdf" in name or "qpdf" in name or "syntax" in message.lower():
        suffix = f" ({message})" if message else ""
        return f"PDFを読み込めません。ファイルが壊れている可能性があります。{suffix}"
    return message or f"{type(exc).__name__} が発生しました。"


def analyze(path: str | Path) -> dict[str, Any]:
    """Return inexpensive metadata for a PDF without changing the file."""

    pdf_path = Path(path)
    result: dict[str, Any] = {
        "path": pdf_path,
        "size": 0,
        "pages": 0,
        "images": 0,
        "image_centric": False,
        "color_tendency": {"カラー": 0, "グレースケール": 0},
        "image_dimensions": [],
        "effective_dpi": [],
        "error": None,
    }
    try:
        result["size"] = pdf_path.stat().st_size
        with pikepdf.open(pdf_path) as pdf:
            result["pages"] = len(pdf.pages)
            for page in pdf.pages:
                for name, image in page.get_images().items():
                    result["images"] += 1
                    bucket = _colour_bucket(image.get("/ColorSpace"))
                    result["color_tendency"][bucket] = result["color_tendency"].get(bucket, 0) + 1
                    width = _number(image.get("/Width"))
                    height = _number(image.get("/Height"))
                    if width and height:
                        dimensions = (int(width), int(height))
                        result["image_dimensions"].append(dimensions)
                        dpi = estimate_effective_dpi(page, name, dimensions)
                        if dpi is not None:
                            result["effective_dpi"].append(round(dpi, 1))
            result["image_centric"] = result["images"] >= result["pages"] and result["images"] > 0
    except Exception as exc:
        result["error"] = describe_pdf_error(exc)
    return result
