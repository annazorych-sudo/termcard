import json
import re
import unicodedata
from collections import Counter
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

PAGE_MARK = re.compile(r"---\s*\d+ページ\s*---")
SECTION_NO = re.compile(r"^\d+(?:\s*[-.]\s*\d+)+$")
URL_BIT = re.compile(r"https?://|www\.|\.com/|\.org/|\.jp/", re.I)
UI_NOISE = re.compile(
    r"^(Step|Next|Yes|No|Finish|Install|Warning|CHAPTER|Page|click|the|and|Figure|see|Windows|NOTE|TIPS)$",
    re.I,
)
HEADWORD = (
    r"(?:[A-Z][A-Za-z0-9]{1,18}(?:[ \-][A-Z][A-Za-z0-9]+){0,2}"
    r"|[ァ-ヶー]{3,18}"
    r"|[\u4e00-\u9faf]{2,8}(?:[ァ-ヶー]{2,12})?)"
)
IN_TEXT_DEF = re.compile(
    rf"(?<![A-Za-z0-9ァ-ヶー\u4e00-\u9faf])({HEADWORD})とは(?:いうのは)?[、,]\s*([^\n。]{{8,180}})。"
)
JA_KATA = re.compile(r"[ァ-ヶー]{4,16}")
JA_COMPOUND = re.compile(r"[\u4e00-\u9faf]{1,6}[ァ-ヶー]{2,12}|[\u4e00-\u9faf]{3,8}")
LATIN_NAME = re.compile(r"\b[A-Z][A-Za-z0-9]{2,}(?:[ \-][A-Z][A-Za-z0-9]+)*\b")
ACRONYM = re.compile(r"\b[A-Z]{2,6}\b")
CONTENT = re.compile(r"[ァ-ヶー]{3,}|[\u4e00-\u9faf]{2,}|[A-Za-z]{3,}")
GLOSSARY_HEAD = re.compile(
    r"(?im)^(?:chapter\s+terms?\s+and\s+definitions?|terms?\s+and\s+definitions?|"
    r"key\s+terms?|glossary|vocabulary|用語集|重要用語|用語解説)\s*$"
)

LANG_LABELS = {
    "ja": "日本語",
    "en": "英語",
    "ru": "ロシア語",
    "uk": "ウクライナ語",
    "zh": "中国語",
    "zh-TW": "中国語（繁体）",
    "ko": "韓国語",
    "vi": "ベトナム語",
    "th": "タイ語",
    "id": "インドネシア語",
    "hi": "ヒンディー語",
    "ar": "アラビア語",
    "tr": "トルコ語",
    "fr": "フランス語",
    "de": "ドイツ語",
    "es": "スペイン語",
    "pt": "ポルトガル語",
    "it": "イタリア語",
    "pl": "ポーランド語",
    "nl": "オランダ語",
    "und": "不明",
}
TARGET_LANGS = [
    ("ja", "日本語"),
    ("en", "英語"),
    ("ru", "ロシア語"),
    ("uk", "ウクライナ語"),
    ("zh", "中国語（簡体）"),
    ("zh-TW", "中国語（繁体）"),
    ("ko", "韓国語"),
    ("vi", "ベトナム語"),
    ("th", "タイ語"),
    ("id", "インドネシア語"),
    ("hi", "ヒンディー語"),
    ("ar", "アラビア語"),
    ("tr", "トルコ語"),
    ("fr", "フランス語"),
    ("de", "ドイツ語"),
    ("es", "スペイン語"),
    ("pt", "ポルトガル語"),
    ("it", "イタリア語"),
    ("pl", "ポーランド語"),
    ("nl", "オランダ語"),
]
WIKI_HOST = {
    "ja": "ja", "en": "en", "ru": "ru", "uk": "uk", "zh": "zh", "zh-TW": "zh",
    "ko": "ko", "fr": "fr", "de": "de", "es": "es", "pt": "pt", "it": "it",
    "pl": "pl", "nl": "nl", "vi": "vi", "th": "th", "id": "id", "hi": "hi",
    "ar": "ar", "tr": "tr",
}
JA_GENERIC = {
    "こと", "ため", "よう", "もの", "とき", "ところ", "これ", "それ", "あれ",
    "コマンド", "インストール", "ファイル", "クリック", "ページ", "場合", "必要",
    "設定", "操作", "使用", "機能", "確認", "表示", "入力", "選択", "パソコン",
    "ソフトウェア", "ソフト", "ボタン", "画面", "方法", "説明", "参照", "今回",
    "本章", "本書", "以下", "以上", "環境", "作成", "実行", "利用", "用意",
    "構築", "学習", "自分", "現在", "複数", "最初", "実際", "情報", "技術",
    "コンテンツ", "ウィンドウ", "ダウンロード", "サーバー用",
}


def clean_textbook_text(text: str) -> str:
    """Join OCR line breaks and repair a few common misreads."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text.replace("\x00", ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = PAGE_MARK.sub("\n", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(
        r"(?<=[\u3040-\u30ff\u4e00-\u9faf])\s+(?=[\u3040-\u30ff\u4e00-\u9faf])",
        "",
        text,
    )
    text = re.sub(
        r"(?<=[\u3040-\u30ff\u4e00-\u9faf])\s+(?=ー)|(?<=ー)\s+(?=[\u3040-\u30ff\u4e00-\u9faf])",
        "",
        text,
    )
    text = re.sub(r"(?<=[\u3040-\u30ff\u4e00-\u9faf])\s+(?=[。、])", "", text)
    text = text.replace("の十です", "のことです")
    lines = []
    buf = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or re.fullmatch(r"[A-Za-z]", line) or line.upper() in {"CHAPTER", "TIPS", "NOTE"}:
            if buf:
                lines.append(buf)
                buf = ""
            continue
        if not buf:
            buf = line
        elif buf.endswith(("。", "！", "？", "」")) or re.match(r"^\d+\s*[-.]", line):
            lines.append(buf)
            buf = line
        elif re.search(r"[A-Za-z]$", buf) and re.match(r"[A-Za-z]", line):
            buf += " " + line
        else:
            buf += line
    if buf:
        lines.append(buf)
    return "\n".join(lines).strip()


def detect_language(text: str) -> str:
    sample = text[:5000]
    jp = len(re.findall(r"[\u3040-\u30ff\u4e00-\u9faf]", sample))
    kana = len(re.findall(r"[\u3040-\u30ff]", sample))
    cy = len(re.findall(r"[А-Яа-яЁё]", sample))
    ko = len(re.findall(r"[\uac00-\ud7af]", sample))
    en = len(re.findall(r"[A-Za-z]", sample))
    if jp >= 20 and kana >= 8 and jp > en:
        return "ja"
    if cy >= 20 and cy > jp and cy > ko:
        return "ru"
    if ko >= 20 and ko > jp:
        return "ko"
    if en >= 40 and en > jp:
        return "en"
    if jp >= 20:
        return "zh" if kana < 8 else "ja"
    return "und"


def guess_topic(text: str) -> str:
    """Title under the chapter number, even if OCR glued it to the next sentence."""
    raw = clean_textbook_text(text)
    match = re.search(
        r"(?:第\s*\d+\s*章|CHAPTER\s*\d+|\b\d{1,2})?\s*"
        r"((?:Web\s*)?[^\n。]{0,12}?[\u3040-\u30ff\u4e00-\u9faf].{4,28}?)"
        r"(?=。|Web\s*サーバーは|この章|本章)",
        raw,
    )
    if match:
        title = re.sub(r"\s+", " ", match.group(1)).strip(" 　-—_")
        title = re.sub(r"^(?:第\s*\d+\s*章|CHAPTER\s*\d+|\d+)\s*", "", title, flags=re.I)
        ja = len(re.findall(r"[\u3040-\u30ff\u4e00-\u9faf]", title))
        if 6 <= len(title) <= 40 and ja >= 4:
            return title[:80]
    for line in raw.splitlines():
        line = line.strip()
        if 8 <= len(line) <= 36 and "。" not in line:
            ja = len(re.findall(r"[\u3040-\u30ff\u4e00-\u9faf]", line))
            if ja >= 6 and not re.search(r"[—_]{3,}", line):
                return line[:80]
    return ""


def _wiki_host(lang: str) -> str:
    return WIKI_HOST.get(lang, "ja")


def _get_json(url: str) -> dict | list:
    request = Request(url, headers={"User-Agent": "TermCard/0.1 (graduation project)"})
    with urlopen(request, timeout=4) as response:
        return json.loads(response.read().decode("utf-8"))


def _title_close(term: str, title: str) -> bool:
    def norm(value: str) -> str:
        value = re.sub(r"\([^)]*\)", "", value)
        value = re.sub(r"\s+", "", value).casefold()
        return value

    left, right = norm(term), norm(title)
    if not left or not right:
        return False
    return left == right or left in right or right in left


def wiki_definition(term: str, lang: str = "ja", topic_keys: set[str] | None = None) -> str:
    """Exact Wikipedia article only. Search hits were attaching the wrong sense."""
    del topic_keys
    host = _wiki_host(lang if lang not in {"", "und"} else "ja")
    try:
        data = _get_json(f"https://{host}.wikipedia.org/api/rest_v1/page/summary/{quote(term)}")
    except (HTTPError, URLError, TimeoutError, ValueError):
        return ""
    if not isinstance(data, dict) or data.get("type") == "disambiguation":
        return ""
    title = data.get("title") or ""
    if not _title_close(term, title):
        return ""
    extract = re.sub(r"\s+", " ", data.get("extract") or "").strip()
    if not extract:
        return ""
    if re.search(r"\b(surname|given name|is a city|is a town|footballer|politician)\b", extract, re.I):
        return ""
    if "。" in extract:
        return extract.split("。")[0].strip() + "。"
    return re.split(r"(?<=[.!?])\s", extract, maxsplit=1)[0][:240]


def _is_generic(term: str) -> bool:
    return term in JA_GENERIC or term.lower() in JA_GENERIC


def _looks_like_term(term: str, lang: str = "ja") -> bool:
    term = re.sub(r"\s+", " ", term).strip().strip("*")
    if not 2 <= len(term) <= 40 or _is_generic(term):
        return False
    if any(ch in term for ch in "。、，,？?！!（）()[]【】<>「」"):
        return False
    if SECTION_NO.match(term) or URL_BIT.search(term) or UI_NOISE.match(term):
        return False
    if re.search(r"\d", term) and term not in {"IPv4", "IPv6"}:
        return False
    has_ja = bool(re.search(r"[\u3040-\u30ff\u4e00-\u9faf]", term))
    has_en = bool(re.search(r"[A-Za-z]{3,}", term))
    if lang == "en":
        return has_en and not has_ja
    if lang == "ja":
        if re.match(r"^[ぁ-ん]", term) or len(re.findall(r"[ぁ-ん]", term)) >= 2:
            return False
        if any(bit in term for bit in ("場合", "本章", "本書", "以下", "以上")):
            return False
        return has_ja and not has_en
    return has_ja or has_en


def _is_procedure(definition: str) -> bool:
    """A step in the textbook, not a definition."""
    compact = re.sub(r"\s+", "", definition)
    if re.search(r"のこと|と呼ばれる|という意味|である", compact):
        return False
    return bool(re.search(
        r"クリック|選択し|入力し|押して|してください|しましょう|構成して|"
        r"許可します|できるように|必要があります|コピーして|ペースト",
        compact,
    ))


def _ocr_noise(definition: str) -> bool:
    """Reject lines that are OCR debris, not a definition."""
    if re.search(r"[①②③④⑤⑥⑦⑧⑨⑩]|---\s*\d+ページ|P\.?\s*\d", definition):
        return True
    # Broken short latin tokens mixed into Japanese: "Ey M キ ax"
    scraps = re.findall(r"\b[A-Za-z]{1,2}\b", definition)
    if len(scraps) >= 3:
        return True
    compact = re.sub(r"\s+", "", definition)
    ja = len(re.findall(r"[\u3040-\u30ff\u4e00-\u9faf]", compact))
    other = len(re.findall(r"[A-Za-z0-9]", compact))
    if ja < 8 and other > ja * 2:
        return True
    return False


def _looks_like_definition(definition: str) -> bool:
    definition = re.sub(r"\s+", " ", definition or "").strip()
    if len(definition) < 12 or URL_BIT.search(definition) or _is_procedure(definition):
        return False
    if _ocr_noise(definition):
        return False
    if "?" in definition or "？" in definition:
        return False
    if re.search(r"いったい|どのようなもの|でしょうか|ですか", definition):
        return False
    if definition.endswith(("、", "は", "が", "を", "の")):
        return False
    compact = re.sub(r"\s+", "", definition)
    kana = len(re.findall(r"[ぁ-んァ-ヶー\u4e00-\u9faf]", compact))
    latin = len(re.findall(r"[A-Za-z]", compact))
    if re.search(r"のこと|と呼ばれる|という意味|である|とは|is a |is the ", compact, re.I):
        return kana >= 4 or latin >= 12
    return kana >= 12 or latin >= 20


def _glossary_definitions(text: str) -> dict[str, str]:
    """Chapter glossary, including multi-line entries. Last header wins over TOC."""
    found: dict[str, str] = {}
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if GLOSSARY_HEAD.search(line.strip()):
            start = i + 1
    if start is None:
        return found
    current_term = None
    current_def: list[str] = []
    entry_re = re.compile(
        r"^\s*\*{0,2}([A-Z][A-Za-z0-9][\w\'\-]*(?:\s+[A-Za-z][\w\'\-]*){0,6})\*{0,2}\s*[:：]\s*(.*)$"
    )
    for raw in lines[start : start + 120]:
        line = raw.strip()
        if not line:
            continue
        if re.match(r"(?i)^(chapter\s+case|references|bibliography|index)\b", line):
            break
        match = entry_re.match(line)
        if match:
            if current_term and current_def:
                definition = re.sub(r"\s+", " ", " ".join(current_def)).strip()
                if _looks_like_term(current_term) and _looks_like_definition(definition):
                    found.setdefault(current_term, definition[:300])
            current_term = re.sub(r"\s+", " ", match.group(1)).strip()
            rest = match.group(2).strip()
            current_def = [rest] if rest else []
        elif current_term and len(line) > 3:
            current_def.append(line)
    if current_term and current_def:
        definition = re.sub(r"\s+", " ", " ".join(current_def)).strip()
        if _looks_like_term(current_term) and _looks_like_definition(definition):
            found.setdefault(current_term, definition[:300])
    return found


def _in_text_definitions(text: str) -> dict[str, str]:
    found = {}
    for match in IN_TEXT_DEF.finditer(text):
        term = re.sub(r"\s+", " ", match.group(1)).strip()
        definition = re.sub(r"\s+", " ", match.group(2)).strip()
        if _looks_like_term(term) and _looks_like_definition(definition):
            found.setdefault(term, definition)
    return found


def _candidates(text: str) -> list[str]:
    counts: Counter[str] = Counter()
    for match in JA_KATA.findall(text):
        counts[match] += 2
    for match in JA_COMPOUND.findall(text):
        counts[match] += 2
    for match in LATIN_NAME.findall(text):
        counts[match] += 2
    for match in ACRONYM.findall(text):
        counts[match] += 1
    ranked = []
    for term, score in counts.most_common(60):
        minimum = 4 if re.fullmatch(r"[\u4e00-\u9faf]{2,3}", term) else 2
        if re.fullmatch(r"[A-Z]{2}", term) and score < 3:
            continue
        if _looks_like_term(term) and score >= minimum:
            ranked.append(term)
    kept = []
    for term in ranked:
        if any(term != other and term in other and len(other) >= len(term) + 2 for other in ranked):
            if len(term) <= 4:
                continue
        kept.append(term)
    return kept


def _definition_overlaps_chapter(definition: str, chapter_text: str, term: str) -> bool:
    """Wikipedia text must share the chapter's vocabulary, not just the term."""
    def tokens(value: str) -> set[str]:
        return {
            token for token in CONTENT.findall(value)
            if token != term and token not in JA_GENERIC and token.lower() not in JA_GENERIC
        }
    return bool(tokens(definition) & tokens(chapter_text))


def extract_terms(
    text: str,
    source_lang: str = "ja",
    extra_defs: dict | None = None,
    limit: int = 20,
) -> list[dict]:
    """
    1. Clean OCR text.
    2. Glossary, then explicit 「用語とは、…。」
    3. Repeated names only if Wikipedia matches the chapter.
    """
    extra_defs = extra_defs or {}
    text = clean_textbook_text(text or "")
    lang = "ja" if source_lang in {"", "und", "ja"} else source_lang
    picked = []
    seen = set()
    wiki_budget = 8

    def add(term: str, definition: str, origin: str) -> bool:
        key = re.sub(r"\s+", "", term).casefold()
        if key in seen or not _looks_like_term(term, lang) or not _looks_like_definition(definition):
            return False
        if origin == "wiki" and not _definition_overlaps_chapter(definition, text, term):
            return False
        seen.add(key)
        picked.append(
            {
                "term": term,
                "definition": re.sub(r"\s+", " ", definition).strip(),
                "source_page": None,
                "origin": origin,
            }
        )
        return len(picked) >= limit

    for term, definition in _glossary_definitions(text).items():
        if add(term, definition, "glossary"):
            return picked

    for term, definition in _in_text_definitions(text).items():
        if add(term, definition, "text"):
            return picked

    for term in _candidates(text):
        if re.sub(r"\s+", "", term).casefold() in seen:
            continue
        cached = extra_defs.get(term) or extra_defs.get(term.lower())
        if (
            cached
            and _looks_like_definition(cached)
            and not _ocr_noise(cached)
            and re.search(r"のこと|と呼ばれる|という意味|である|とは|is a |is the ", cached, re.I)
        ):
            if add(term, cached, "db"):
                return picked
            continue
        if wiki_budget <= 0:
            break
        wiki_budget -= 1
        wiki = wiki_definition(term, lang)
        if wiki and add(term, wiki, "wiki"):
            return picked
    return picked
