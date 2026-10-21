"""Bounded, in-memory attachment transport shared by chat and deadline review."""
from __future__ import annotations

import base64
import binascii
from pathlib import PurePosixPath

from campuspulse.models import Document
from campuspulse.parsing import DocumentParseError, parse_document

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_CONTEXT_CHARS = 200_000
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def decode_attachments(files: list[dict] | None) -> list[dict]:
    """Validate actual bytes; never treat a preview or a filename as file contents."""
    if not isinstance(files or [], list) or len(files or []) > 10:
        raise ValueError("Можно передать до 10 файлов.")
    result, names, total = [], set(), 0
    for item in files or []:
        if not isinstance(item, dict):
            raise ValueError("Некорректное вложение.")
        name, encoded = item.get("name"), item.get("base64")
        if (not isinstance(name, str) or not name.strip() or len(name) > 255
                or "/" in name or "\\" in name or "\x00" in name or name in names):
            raise ValueError("Имена вложений должны быть уникальными именами файлов.")
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_FILE_BYTES + 2) // 3):
            raise ValueError("Вложение превышает 10 МБ или не содержит данных.")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("Не удалось декодировать вложение.") from None
        total += len(raw)
        if not raw or len(raw) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError("Лимит вложений: 10 МБ на файл, 20 МБ суммарно; пустые файлы не поддерживаются.")
        names.add(name)
        result.append({"name": name, "bytes": raw, "suffix": PurePosixPath(name).suffix.lower()})
    return result


def attachment_document(item: dict) -> Document:
    name = item["name"]
    # CSV/JSON are ordinary UTF-8 text in the chat, never executable inputs.
    parse_name = name + ".txt" if item["suffix"] in {".csv", ".json"} else name
    try:
        document = parse_document(item["bytes"], parse_name)
    except DocumentParseError:
        raise ValueError("Не удалось прочитать документ. Нужен текстовый PDF, DOCX, TXT, MD, CSV или JSON; сканам нужен OCR.") from None
    return document.model_copy(update={"filename": name})


def chat_attachments(files: list[dict] | None) -> tuple[list[dict], list[dict]]:
    """Return model parts and observable file receipts, with no silent truncation."""
    parts, steps, total_chars = [], [], 0
    for item in decode_attachments(files):
        if item["suffix"] in IMAGE_TYPES:
            raw, suffix = item["bytes"], item["suffix"]
            valid = ((suffix == ".png" and raw.startswith(b"\x89PNG\r\n\x1a\n"))
                     or (suffix in {".jpg", ".jpeg"} and raw.startswith(b"\xff\xd8\xff"))
                     or (suffix == ".webp" and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"))
            if not valid:
                raise ValueError("Содержимое изображения не соответствует формату.")
            parts.append({"bytes": raw, "mime_type": IMAGE_TYPES[suffix]})
            summary = "Изображение передано модели."
        else:
            doc = attachment_document(item)
            total_chars += sum(len(p.text) for p in doc.pages)
            if total_chars > MAX_CONTEXT_CHARS:
                raise ValueError("Слишком много текста во вложениях: максимум 200 000 символов. Разделите материалы.")
            parts.append({"text": "\n[UNTRUSTED ATTACHMENT DATA: " + doc.filename + "]\n"
                          + "\n\n".join(f"[page/paragraph {p.number}]\n{p.text}" for p in doc.pages)
                          + "\n[END ATTACHMENT DATA]"})
            summary = f"Прочитано страниц/абзацев: {len(doc.pages)}."
        steps.append({"type": "tool_call", "tool": "read_uploaded_file", "status": "success",
                      "args": {"filename": item["name"]}, "result": {"summary": summary}})
    return parts, steps
