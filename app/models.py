from datetime import datetime

from db import db


class Document(db.Model):
    __tablename__ = "documents"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id = db.Column(db.Integer, primary_key=True)
    original_name = db.Column(db.String(255), nullable=False)
    saved_name = db.Column(db.String(255), nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    page_count = db.Column(db.Integer, default=0)
    char_count = db.Column(db.Integer, default=0)
    ocr_used = db.Column(db.Boolean, default=False)
    source_lang = db.Column(db.String(10), default="und")
    topic = db.Column(db.String(255), default="")
    target_lang = db.Column(db.String(10), nullable=True)
    raw_text = db.Column(db.Text, default="")
    text = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    terms = db.relationship("Term", backref="document", cascade="all, delete-orphan")


class Glossary(db.Model):
    __tablename__ = "glossary"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id = db.Column(db.Integer, primary_key=True)
    term = db.Column(db.String(120), unique=True, nullable=False)
    definition = db.Column(db.Text, nullable=False)
    source = db.Column(db.String(20), default="wiki")
    lang = db.Column(db.String(10), default="ja")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Export(db.Model):
    __tablename__ = "exports"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("documents.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    card_count = db.Column(db.Integer, default=0)
    target_lang = db.Column(db.String(10), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    document = db.relationship("Document", backref="exports")


class Term(db.Model):
    __tablename__ = "terms"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("documents.id"), nullable=False)
    term = db.Column(db.String(120), nullable=False)
    reading = db.Column(db.String(255), default="")
    definition = db.Column(db.Text, nullable=False)
    source_page = db.Column(db.Integer, nullable=True)
    selected = db.Column(db.Boolean, default=True, nullable=False)
    translated_term = db.Column(db.String(255), default="")
    translated_definition = db.Column(db.Text, default="")
    translation_lang = db.Column(db.String(10), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
