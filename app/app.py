import os
from datetime import datetime
from pathlib import Path
import uuid

from dotenv import load_dotenv
import csv
import io

from flask import Flask, Response, flash, redirect, render_template, request, url_for
from sqlalchemy import event
from werkzeug.utils import secure_filename

from analyze import LANG_LABELS, TARGET_LANGS, detect_language, extract_terms, guess_topic
from llm import read_chapter
import llm
from db import db
from models import Document, Export, Glossary, Term
from text_extract import IMAGE_SUFFIXES, PDF_SUFFIXES, clean_text, extract_image_text, extract_pdf_text
from reading import term_reading
from translate import translate_cards

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
load_dotenv(BASE_DIR / ".env")

ALLOWED_SUFFIXES = PDF_SUFFIXES | IMAGE_SUFFIXES

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-change-me")
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{(BASE_DIR / 'termcard.db').as_posix()}",
)
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
if "mysql" in app.config["SQLALCHEMY_DATABASE_URI"]:
    app.config["SQLALCHEMY_ENGINE_OPTIONS"]["connect_args"] = {"charset": "utf8mb4"}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)


def _force_utf8mb4(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci")
    cursor.close()


with app.app_context():
    if "mysql" in app.config["SQLALCHEMY_DATABASE_URI"]:
        event.listen(db.engine, "connect", _force_utf8mb4)
    db.create_all()
    for stmt in (
        "ALTER TABLE terms ADD COLUMN selected BOOLEAN DEFAULT 1",
        "ALTER TABLE terms ADD COLUMN translated_term VARCHAR(255) DEFAULT ''",
        "ALTER TABLE terms ADD COLUMN translated_definition TEXT DEFAULT ''",
        "ALTER TABLE terms ADD COLUMN translation_lang VARCHAR(10)",
        "ALTER TABLE terms ADD COLUMN reading VARCHAR(255) DEFAULT ''",
        "ALTER TABLE documents ADD COLUMN raw_text TEXT DEFAULT ''",
    ):
        try:
            db.session.execute(db.text(stmt))
            db.session.commit()
        except Exception:
            db.session.rollback()

APP_NAME = "用語カード"
APP_NAME_EN = "TermCard"


@app.context_processor
def inject_brand():
    return {
        "app_name": APP_NAME,
        "app_name_en": APP_NAME_EN,
        "nav_active": "",
    }


def unique_name(original: str) -> str:
    suffix = Path(original).suffix.lower()
    stem = secure_filename(Path(original).stem) or "file"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"{stamp}-{short}-{stem}{suffix}"


def save_document(original_name: str, saved_name: str, result: dict) -> Document:
    raw = result.get("text") or ""
    source_lang = detect_language(raw)
    if source_lang == "und":
        source_lang = "ja"
    cleaned = clean_text(raw, source_lang)
    chapter = read_chapter(cleaned, source_lang)
    topic = (chapter or {}).get("topic") or guess_topic(cleaned)
    document = Document(
        original_name=original_name,
        saved_name=saved_name,
        kind=result.get("kind") or "pdf",
        page_count=result.get("page_count") or 0,
        char_count=len(cleaned),
        ocr_used=bool(result.get("ocr_used")),
        source_lang=source_lang,
        topic=topic,
        raw_text=raw,
        text=cleaned,
    )
    db.session.add(document)
    db.session.flush()
    items = (chapter or {}).get("terms") or []
    if not items:
        saved_defs = {row.term: row.definition for row in Glossary.query.all()}
        items = extract_terms(document.text, source_lang, extra_defs=saved_defs)
    for item in items:
        db.session.add(
            Term(
                document_id=document.id,
                term=item["term"],
                reading=term_reading(item["term"], source_lang),
                definition=item["definition"],
                source_page=item.get("source_page"),
                selected=True,
            )
        )
        if item.get("origin") in {"wiki", "text", "local"}:
            existing = Glossary.query.filter_by(term=item["term"]).first()
            if existing is None:
                db.session.add(
                    Glossary(
                        term=item["term"],
                        definition=item["definition"],
                        source=item.get("origin") or "local",
                        lang=source_lang,
                    )
                )
    db.session.commit()
    return document


@app.route("/")
def index():
    return render_template("index.html", nav_active="upload")


@app.route("/upload", methods=["POST"])
def upload():
    uploaded = request.files.get("file")
    if uploaded is None or uploaded.filename == "":
        flash("ファイルを選んでください。")
        return redirect(url_for("index"))

    suffix = Path(uploaded.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        flash("PDF / JPEG / PNG だけアップロードできます。")
        return redirect(url_for("index"))

    saved_name = unique_name(uploaded.filename)
    saved_path = UPLOAD_DIR / saved_name
    uploaded.save(saved_path)

    if suffix in PDF_SUFFIXES:
        result = extract_pdf_text(saved_path)
    else:
        result = extract_image_text(saved_path)

    if not result.get("text") or result.get("ocr_error"):
        return render_template(
            "result.html",
            app_name=APP_NAME,
            app_name_en=APP_NAME_EN,
            document=None,
            result=result,
            original_name=uploaded.filename,
            lang_labels=LANG_LABELS,
            target_langs=TARGET_LANGS,
            nav_active="upload",
        )

    document = save_document(uploaded.filename, saved_name, result)
    if llm.LAST_ERROR and not llm.LAST_ERROR.startswith("ok "):
        flash(f"モデル未使用: {llm.LAST_ERROR[:180]}")
    return redirect(url_for("document_view", document_id=document.id))


@app.route("/document/<int:document_id>")
def document_view(document_id: int):
    document = Document.query.get_or_404(document_id)
    return render_template(
        "result.html",
        app_name=APP_NAME,
        app_name_en=APP_NAME_EN,
        document=document,
        result=None,
        original_name=document.original_name,
        lang_labels=LANG_LABELS,
        target_langs=TARGET_LANGS,
        nav_active="upload",
    )


@app.route("/document/<int:document_id>/prepare", methods=["POST"])
def prepare_cards(document_id: int):
    document = Document.query.get_or_404(document_id)
    target = request.form.get("target_lang", "")
    allowed = {code for code, _label in TARGET_LANGS}
    chosen = {int(value) for value in request.form.getlist("term_id")}
    for item in document.terms:
        item.selected = item.id in chosen
        if not item.reading:
            item.reading = term_reading(item.term, document.source_lang)
    if target not in allowed:
        flash("翻訳先の言語を選んでください。")
        db.session.commit()
        return redirect(url_for("document_view", document_id=document.id))
    if not chosen:
        flash("カードにする語を1つ以上選んでください。")
        db.session.commit()
        return redirect(url_for("document_view", document_id=document.id))
    document.target_lang = target
    selected = [item for item in document.terms if item.selected]
    if document.source_lang != target:
        translate_cards(selected, document.source_lang, target)
    db.session.commit()
    return redirect(url_for("cards_view", document_id=document.id))


@app.route("/library")
def library():
    decks = []
    for document in Document.query.order_by(Document.created_at.desc()).all():
        count = sum(1 for item in document.terms if item.selected)
        if count:
            decks.append({"document": document, "count": count})
    return render_template("library.html", decks=decks, nav_active="library")


@app.route("/document/<int:document_id>/deck")
def deck_view(document_id: int):
    document = Document.query.get_or_404(document_id)
    cards = [item for item in document.terms if item.selected]
    if not cards:
        flash("この教科書のカードはまだありません。")
        return redirect(url_for("library"))
    return render_template(
        "deck.html",
        document=document,
        cards=cards,
        lang_labels=LANG_LABELS,
        nav_active="library",
    )


@app.route("/anki")
def anki_history():
    exports = Export.query.order_by(Export.created_at.desc()).all()
    return render_template(
        "anki.html",
        exports=exports,
        lang_labels=LANG_LABELS,
        nav_active="anki",
    )


def _anki_back(item: Term) -> str:
    lines = []
    if item.translated_term and item.translated_term != item.term:
        lines.append(item.translated_term)
    if item.translated_definition:
        lines.append(item.translated_definition)
    if item.reading:
        lines.append(f"{item.term}（{item.reading}）")
    if not lines:
        lines.append(item.term)
    return "<br>".join(lines)


@app.route("/document/<int:document_id>/anki.csv", methods=["GET", "POST"])
def anki_csv(document_id: int):
    document = Document.query.get_or_404(document_id)
    if request.method == "POST":
        chosen = {int(value) for value in request.form.getlist("term_id")}
        for item in document.terms:
            item.selected = item.id in chosen
        db.session.commit()
    cards = [item for item in document.terms if item.selected]
    if not cards:
        flash("先にカードにする語を選んでください。")
        return redirect(url_for("document_view", document_id=document.id))

    target = request.form.get("target_lang") or document.target_lang
    if target:
        document.target_lang = target
        db.session.commit()
    if not document.target_lang or document.target_lang == document.source_lang:
        flash("先に翻訳先の言語を選んでください。")
        return redirect(url_for("document_view", document_id=document.id))

    translate_cards(cards, document.source_lang, document.target_lang)
    db.session.commit()

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    for item in cards:
        writer.writerow([item.term, _anki_back(item)])

    stem = secure_filename(Path(document.original_name).stem) or "termcard"
    filename = f"{stem}-anki.csv"
    db.session.add(
        Export(
            document_id=document.id,
            filename=filename,
            card_count=len(cards),
            target_lang=document.target_lang or document.source_lang,
        )
    )
    db.session.commit()
    payload = "\ufeff" + buffer.getvalue()
    return Response(
        payload,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.route("/document/<int:document_id>/cards")
def cards_view(document_id: int):
    document = Document.query.get_or_404(document_id)
    cards = [item for item in document.terms if item.selected]
    if not cards:
        flash("先にカードにする語を選んでください。")
        return redirect(url_for("document_view", document_id=document.id))
    if document.target_lang and document.target_lang != document.source_lang:
        missing = [
            item for item in cards
            if item.translation_lang != document.target_lang or not item.translated_definition
        ]
        if missing:
            translate_cards(missing, document.source_lang, document.target_lang)
            db.session.commit()
    return render_template(
        "cards.html",
        app_name=APP_NAME,
        app_name_en=APP_NAME_EN,
        document=document,
        cards=cards,
        lang_labels=LANG_LABELS,
        nav_active="library",
    )


@app.errorhandler(413)
def too_large(_error):
    flash("ファイルが大きすぎます（32MBまで）。")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
