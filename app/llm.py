import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MODELS = [
    os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]
ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


def translate_with_gemini(texts: list[str], source: str, target: str) -> list[str] | None:
    """Translate a batch with Gemini. None if the call fails."""
    key = os.getenv("GEMINI_API_KEY")
    if not key or not texts:
        return None
    lang = {"ja": "Japanese", "en": "English", "ru": "Russian", "uk": "Ukrainian"}.get(target, target)
    numbered = "\n".join(f"{i + 1}. {text}" for i, text in enumerate(texts))
    prompt = (
        f"Translate each line from {source} to {lang}. "
        "Reply with JSON only: {\"lines\": [\"...\", ...]}. "
        "Same number of lines, same order. No notes.\n\n"
        f"{numbered}"
    )
    body = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }
    ).encode("utf-8")
    for model in MODELS:
        request = Request(
            ENDPOINT.format(model=model),
            data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
        )
        try:
            with urlopen(request, timeout=40) as response:
                payload = json.loads(response.read().decode("utf-8"))
            raw = payload["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(raw)
            lines = data.get("lines") or []
            if len(lines) == len(texts):
                return [str(line).strip() for line in lines]
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, IndexError):
            continue
    return None


LAST_ERROR = ""


def _log(message: str) -> None:
    global LAST_ERROR
    LAST_ERROR = message
    try:
        path = os.path.join(os.path.dirname(__file__), "llm.log")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
    except OSError:
        pass


def read_chapter(text: str, source_lang: str = "ja") -> dict | None:
    """Ask Gemini for the chapter topic and key terms. None if no key or call fails."""
    key = os.getenv("GEMINI_API_KEY")
    if not key or not text.strip():
        _log("no key or empty text")
        return None
    sample = text[:12000]
    prompt = (
        "You read one textbook chapter. Reply with JSON only, no markdown.\n"
        "Keys: topic (short title in the chapter language), terms (list of objects "
        "with term and definition).\n"
        "Rules: 8 to 15 key concepts of THIS chapter. "
        "term must be in the chapter language only. "
        "If the chapter is Japanese, terms are Japanese words, not English file names. "
        "If the chapter is English, terms are English words, not Japanese. "
        "definition is one plain sentence in the chapter language. "
        "Skip UI steps, page numbers, and OCR garbage.\n\n"
        f"Chapter language code: {source_lang}\n\n{sample}"
    )
    body = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        }
    ).encode("utf-8")
    last_error = "no model tried"
    for model in MODELS:
        url = ENDPOINT.format(model=model)
        request = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
        )
        try:
            with urlopen(request, timeout=40) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            last_error = f"{model} HTTP {exc.code}: {detail}"
            _log(last_error)
            continue
        except (URLError, TimeoutError, ValueError) as exc:
            last_error = f"{model} {exc}"
            _log(last_error)
            continue
        try:
            raw = payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            last_error = f"{model} empty response"
            _log(last_error)
            continue
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.I).strip()
        try:
            data = json.loads(raw)
        except ValueError:
            last_error = f"{model} not json"
            _log(last_error)
            continue
        topic = str(data.get("topic") or "").strip()[:120]
        terms = []
        for item in data.get("terms") or []:
            if not isinstance(item, dict):
                continue
            term = str(item.get("term") or "").strip()
            definition = str(item.get("definition") or "").strip()
            if 2 <= len(term) <= 40 and len(definition) >= 8:
                has_ja = bool(re.search(r"[\u3040-\u30ff\u4e00-\u9faf]", term))
                has_en = bool(re.search(r"[A-Za-z]{3,}", term))
                if source_lang == "ja" and (not has_ja or has_en):
                    continue
                if source_lang == "en" and (not has_en or has_ja):
                    continue
                terms.append({"term": term, "definition": definition, "origin": "llm"})
        if topic or terms:
            _log(f"ok {model} terms={len(terms)}")
            return {"topic": topic, "terms": terms[:15]}
    _log(f"failed: {last_error}")
    return None
