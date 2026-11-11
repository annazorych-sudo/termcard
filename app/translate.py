import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from analyze import LANG_LABELS
from llm import translate_with_gemini

GOOGLE_CODES = {
    "ja": "ja",
    "en": "en",
    "ru": "ru",
    "uk": "uk",
    "zh": "zh-CN",
    "zh-TW": "zh-TW",
    "ko": "ko",
    "vi": "vi",
    "th": "th",
    "id": "id",
    "hi": "hi",
    "ar": "ar",
    "tr": "tr",
    "fr": "fr",
    "de": "de",
    "es": "es",
    "pt": "pt",
    "it": "it",
    "pl": "pl",
    "nl": "nl",
}
WIKI_HOST = {code: ("zh" if code.startswith("zh") else code) for code in GOOGLE_CODES}

JA = re.compile(r"[\u3040-\u30ff\u4e00-\u9faf]")


def lang_name(code: str) -> str:
    return LANG_LABELS.get(code, code)


def _get_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "TermCard/0.1 (graduation project)"})
    with urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def wiki_term_equivalent(term: str, source: str, target: str) -> str:
    if source == target or source not in WIKI_HOST or target not in WIKI_HOST:
        return ""
    host = WIKI_HOST[source]
    lllang = "zh" if target.startswith("zh") else WIKI_HOST[target]
    query = urlencode(
        {
            "action": "query",
            "format": "json",
            "redirects": 1,
            "prop": "langlinks",
            "lllang": lllang,
            "titles": term,
        }
    )
    url = f"https://{host}.wikipedia.org/w/api.php?{query}"
    try:
        data = _get_json(url)
    except (HTTPError, URLError, TimeoutError, ValueError):
        return ""
    pages = (data.get("query") or {}).get("pages") or {}
    for page in pages.values():
        links = page.get("langlinks") or []
        if links:
            title = (links[0].get("*") or "").strip()
            if title:
                return title
    return ""


def _mymemory(text: str, source: str, target: str) -> str:
    query = urlencode({"q": text[:450], "langpair": f"{source}|{target}"})
    url = f"https://api.mymemory.translated.net/get?{query}"
    try:
        data = _get_json(url)
    except (HTTPError, URLError, TimeoutError, ValueError):
        return ""
    translated = ((data.get("responseData") or {}).get("translatedText") or "").strip()
    if translated.upper().startswith("MYMEMORY WARNING"):
        return ""
    return translated


def translate_texts(texts: list[str], source: str, target: str) -> list[str]:
    if source == target or source in {"und", ""}:
        return list(texts)
    src = GOOGLE_CODES.get(source, source)
    dst = GOOGLE_CODES.get(target, target)
    if src == dst:
        return list(texts)
    from deep_translator import GoogleTranslator

    translator = GoogleTranslator(source=src, target=dst)
    result = []
    for text in texts:
        piece = (text or "").strip()
        if not piece:
            result.append("")
            continue
        translated = ""
        try:
            translated = translator.translate(piece) or ""
        except Exception:
            translated = ""
        if not translated or _still_source(translated, source):
            translated = _mymemory(piece, src, dst) or translated
        result.append(translated or piece)
    return result


def _still_source(text: str, source: str) -> bool:
    if not text:
        return True
    if source == "ja":
        return bool(JA.search(text))
    return False


def _clean_machine(text: str) -> str:
    return re.sub(r"^.{0,40}：( )?", "", text).strip()


def translate_cards(cards: list, source: str, target: str) -> None:
    if not cards or source == target:
        return
    gemini = translate_with_gemini(
        [item.term for item in cards] + [item.definition or "" for item in cards],
        source,
        target,
    )
    if gemini and len(gemini) == len(cards) * 2:
        half = len(cards)
        for item, term_tr, def_tr in zip(cards, gemini[:half], gemini[half:]):
            item.translated_term = term_tr or item.term
            item.translated_definition = def_tr or item.definition
            item.translation_lang = target
        return
    for item in cards:
        term_tr = wiki_term_equivalent(item.term, source, target)
        if not term_tr:
            machine_term = translate_texts([item.term], source, target)[0]
            term_tr = machine_term or item.term
        if _still_source(term_tr, source):
            term_tr = item.term
        source_def = item.definition or ""
        def_tr = translate_texts([source_def], source, target)[0]
        if _still_source(def_tr, source):
            def_tr = _mymemory(source_def, source, target) or def_tr
        item.translated_term = term_tr
        item.translated_definition = def_tr
        item.translation_lang = target
