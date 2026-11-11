from io import BytesIO
from pathlib import Path
import re
import shutil
import unicodedata

PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
OCR_ZOOM = 3.0
# First pass when language is unknown (common for this project: JP textbooks + Latin terms).
OCR_PROBE = "eng+jpn"
WINDOWS_TESSERACT = [
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
]
# App language code → Tesseract traineddata name
TESSERACT_LANG = {
    "ja": "jpn",
    "en": "eng",
    "ru": "rus",
    "uk": "ukr",
    "zh": "chi_sim",
    "zh-TW": "chi_tra",
    "ko": "kor",
    "fr": "fra",
    "de": "deu",
    "es": "spa",
    "pt": "por",
    "it": "ita",
    "pl": "pol",
    "nl": "nld",
    "vi": "vie",
    "th": "tha",
    "ar": "ara",
    "tr": "tur",
    "hi": "hin",
    "id": "ind",
    "und": "eng+jpn",
}


def resolve_ocr_lang(lang: str | None) -> str:
    """Map detected app language to a Tesseract -l string."""
    code = TESSERACT_LANG.get(lang or "und", "eng")
    if code in {"eng", "eng+jpn", "jpn"}:
        return code if code != "jpn" else "jpn+eng"
    # Latin product names often appear in any textbook
    if "eng" not in code:
        return f"{code}+eng"
    return code


def normalize_text(text: str) -> str:
    """NFC, drop NUL, normalize newlines, collapse OCR spaces in Japanese."""
    text = text.replace("\x00", "")
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse spaces that OCR inserts between Japanese characters
    # e.g. "サ ー バ ー" → "サーバー", "フ ァ イ ル" → "ファイル"
    text = re.sub(
        r"(?<=[\u3040-\u30ff\u4e00-\u9faf])\s+(?=[\u3040-\u30ff\u4e00-\u9faf])",
        "",
        text,
    )
    # Also collapse spaces around prolonged sound mark ー
    text = re.sub(
        r"(?<=[\u3040-\u30ff\u4e00-\u9faf])\s+(?=ー)|(?<=ー)\s+(?=[\u3040-\u30ff\u4e00-\u9faf])",
        "",
        text,
    )
    lines = [line.rstrip() for line in text.split("\n")]
    cleaned = "\n".join(lines)
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")
    return cleaned.strip()


def clean_text(text: str, lang: str = "und") -> str:
    """
    Stage 2: language-aware cleanup of raw OCR / extracted text.
    Returns text ready for topic and term extraction.
    """
    if not text:
        return ""
    text = normalize_text(text)
    if lang == "ja":
        text = _clean_japanese(text)
    elif lang == "en":
        text = _clean_english(text)
    else:
        # Generic: still collapse JP spaces if any, fix hyphenation
        text = _clean_english(text)
        text = _clean_japanese(text)
    return text.strip()


def _clean_japanese(text: str) -> str:
    """Fix common Japanese OCR artifacts."""
    # Frequent OCR substitutions in tech textbooks
    fixes = {
        "捨作": "操作",
        "採作": "操作",
        "キーポード": "キーボード",
        "キーボード": "キーボード",
        "ファイル坂送": "ファイル転送",
        "イシストール": "インストール",
        "イシス トール": "インストール",
        "ファイアウオール": "ファイアウォール",
        "ファイアウオール": "ファイアウォール",
    }
    for bad, good in fixes.items():
        text = text.replace(bad, good)
    # Rejoin Latin product names split by OCR (Tera Term, WinSCP already ok)
    text = re.sub(r"\bTera\s+Term\b", "Tera Term", text)
    text = re.sub(r"\bWin\s*SCP\b", "WinSCP", text)
    text = re.sub(r"\bPuTTY\b", "PuTTY", text, flags=re.I)
    return text


def _clean_english(text: str) -> str:
    """Fix common English OCR / PDF artifacts."""
    # Join hyphenated line breaks: "manage-\nment" → "management"
    text = re.sub(r"([A-Za-z])-\n([a-z])", r"\1\2", text)
    # Common OCR confusions
    text = text.replace(" rn ", " m ")
    text = re.sub(r"\bFile\s+Managemen\b", "File Management", text)
    return text


def _join_pages(pages: list[dict]) -> str:
    return "\n\n".join(
        f"--- {item['number']}ページ ---\n{item['text']}" if item["text"]
        else f"--- {item['number']}ページ ---\n"
        for item in pages
    ).strip()


def _result(kind: str, pages: list[dict], **extra) -> dict:
    char_count = sum(len(item["text"]) for item in pages)
    page_count = extra.pop("page_count", len(pages))
    payload = {
        "kind": kind,
        "page_count": page_count,
        "char_count": char_count,
        "pages": pages,
        "text": _join_pages(pages),
        "likely_scanned": extra.pop("likely_scanned", False),
        "ocr_used": extra.pop("ocr_used", False),
        "ocr_error": extra.pop("ocr_error", ""),
        "ocr_lang": extra.pop("ocr_lang", ""),
    }
    payload.update(extra)
    return payload


def configure_tesseract() -> str:
    import pytesseract

    found = shutil.which("tesseract")
    if found:
        pytesseract.pytesseract.tesseract_cmd = found
        return found
    for candidate in WINDOWS_TESSERACT:
        if candidate.exists():
            pytesseract.pytesseract.tesseract_cmd = str(candidate)
            return str(candidate)
    raise FileNotFoundError(
        "Tesseract が見つかりません。Windows に Tesseract-OCR を入れ、"
        "必要な言語パックを追加してください。"
    )


def prepare_for_ocr(image):
    """Grayscale, contrast, light denoise — better Tesseract input."""
    from PIL import ImageEnhance, ImageFilter, ImageOps

    if image.mode not in ("L", "RGB"):
        image = image.convert("RGB")
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = ImageEnhance.Contrast(gray).enhance(1.6)
    gray = gray.filter(ImageFilter.MedianFilter(size=3))
    return gray


def ocr_image(image, lang: str | None = None) -> str:
    import pytesseract

    configure_tesseract()
    image = prepare_for_ocr(image)
    tess_lang = resolve_ocr_lang(lang) if lang else OCR_PROBE
    try:
        raw = pytesseract.image_to_string(
            image,
            lang=tess_lang,
            config="--oem 1 --psm 6",
        ) or ""
    except pytesseract.TesseractError:
        if tess_lang != OCR_PROBE:
            raw = pytesseract.image_to_string(image, lang=OCR_PROBE, config="--oem 1 --psm 6") or ""
        else:
            raise
    return normalize_text(raw)


def ocr_pdf_page(page, lang: str | None = None) -> str:
    from PIL import Image
    import fitz

    pix = page.get_pixmap(matrix=fitz.Matrix(OCR_ZOOM, OCR_ZOOM), alpha=False)
    image = Image.open(BytesIO(pix.tobytes("png")))
    return ocr_image(image, lang=lang)


def extract_pdf_text(path: Path, lang: str | None = None) -> dict:
    import fitz

    doc = fitz.open(path)
    pages = []
    try:
        for i, page in enumerate(doc, start=1):
            raw = page.get_text("text") or ""
            pages.append({"number": i, "text": normalize_text(raw)})
    finally:
        doc.close()

    char_count = sum(len(item["text"]) for item in pages)
    scanned = len(pages) > 0 and char_count < 40 * len(pages)
    if not scanned:
        return _result("pdf", pages, likely_scanned=False, ocr_used=False, ocr_lang="")

    # Scanned: probe language from first pages, then OCR all with matched pack
    used_lang = lang
    try:
        if not used_lang:
            from analyze import detect_language

            doc = fitz.open(path)
            try:
                probe_bits = []
                for page in list(doc)[: min(3, len(doc))]:
                    probe_bits.append(ocr_pdf_page(page, lang=None))
                probe_text = "\n".join(probe_bits)
                used_lang = detect_language(probe_text) if probe_text.strip() else "und"
            finally:
                doc.close()

        tess = resolve_ocr_lang(used_lang)
        doc = fitz.open(path)
        ocr_pages = []
        try:
            for i, page in enumerate(doc, start=1):
                ocr_pages.append({"number": i, "text": ocr_pdf_page(page, lang=used_lang)})
        finally:
            doc.close()
    except Exception as exc:
        return _result(
            "pdf",
            pages,
            likely_scanned=True,
            ocr_used=False,
            ocr_error=str(exc),
            ocr_lang="",
        )

    return _result(
        "pdf",
        ocr_pages,
        likely_scanned=True,
        ocr_used=True,
        ocr_lang=tess,
    )


def extract_image_text(path: Path, lang: str | None = None) -> dict:
    from PIL import Image

    try:
        with Image.open(path) as image:
            if not lang:
                from analyze import detect_language

                probe = ocr_image(image, lang=None)
                used = detect_language(probe) if probe.strip() else "und"
                # Re-run with matched language if it differs from probe default
                if used not in {"und", "en", "ja"}:
                    text = ocr_image(image, lang=used)
                    tess = resolve_ocr_lang(used)
                else:
                    text = probe
                    tess = OCR_PROBE
                    used = used if used != "und" else "ja"
            else:
                text = ocr_image(image, lang=lang)
                tess = resolve_ocr_lang(lang)
                used = lang
    except Exception as exc:
        return _result(
            "image",
            [{"number": 1, "text": ""}],
            likely_scanned=True,
            ocr_used=False,
            ocr_error=str(exc),
            ocr_lang="",
        )
    return _result(
        "image",
        [{"number": 1, "text": text}],
        likely_scanned=True,
        ocr_used=True,
        ocr_lang=tess,
    )
