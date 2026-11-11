import re

KANJI = re.compile(r"[\u4e00-\u9faf]")


def term_reading(term: str, source_lang: str) -> str:
    if source_lang != "ja" or not term or not KANJI.search(term):
        return ""
    try:
        import pykakasi
    except ImportError:
        return ""
    converted = pykakasi.kakasi().convert(term)
    reading = "".join(part.get("hira", "") for part in converted).strip()
    if not reading or reading == term:
        return ""
    return reading
