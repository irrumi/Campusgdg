"""Bounded local assignment parsing; no OCR, code execution, or network access.

``DocumentPage.number`` is a physical page for PDFs. For DOCX it is the
1-based body paragraph number (including table cells and empty paragraphs);
for TXT/MD it is a 1-based blank-line-delimited paragraph number. Word's rendered
pagination cannot be recovered reliably from DOCX. Headers/footers are not body
paragraphs. Text is never truncated to make an oversized assignment appear valid.
"""
from __future__ import annotations

import hashlib
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable
from pathlib import PurePosixPath

from .models import Document, DocumentPage

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PAGES = 100
MAX_PAGE_CHARACTERS = 100_000
MAX_TEXT_CHARACTERS = 500_000
MAX_ZIP_ENTRIES = 512
MAX_ZIP_ENTRY_BYTES = 8 * 1024 * 1024
MAX_ZIP_UNCOMPRESSED_BYTES = 25 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 200
MAX_PDF_PAGE_STREAM_BYTES = 2 * 1024 * 1024
MAX_PDF_TOTAL_STREAM_BYTES = 10 * 1024 * 1024
MAX_XML_DEPTH = 128
MAX_XML_ELEMENTS = 100_000
_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_CONTENT_TYPES = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_DOCUMENT_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"


class DocumentParseError(ValueError):
    """An unsupported, malformed, encrypted, or textless document."""


class DocumentLimitError(DocumentParseError):
    """The document exceeds a documented resource limit."""


def _bounded_pages(texts: Iterable[str]) -> list[DocumentPage]:
    pages: list[DocumentPage] = []
    total = 0
    for number, text in enumerate(texts, start=1):
        if number > MAX_PAGES:
            raise DocumentLimitError(f"Document exceeds {MAX_PAGES} pages or paragraphs.")
        if len(text) > MAX_PAGE_CHARACTERS:
            raise DocumentLimitError("A page or paragraph exceeds the text limit.")
        total += len(text)
        if total > MAX_TEXT_CHARACTERS:
            raise DocumentLimitError("Document exceeds the total text limit.")
        if "\x00" in text:
            raise DocumentParseError("Document contains invalid NUL characters.")
        pages.append(DocumentPage(number=number, text=text))
    if not pages or not any(page.text.strip() for page in pages):
        raise DocumentParseError("Document contains no extractable text; scanned documents require OCR.")
    return pages


def _text_paragraphs(content: bytes) -> Iterable[str]:
    try:
        text = content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        raise DocumentParseError("Text assignments must use UTF-8 encoding.") from None
    if any(ord(char) < 32 and char not in "\t\n\r\f" for char in text):
        raise DocumentParseError("Text assignment contains binary or unsupported control characters.")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Preserve line breaks inside a paragraph, including Markdown code/list blocks.
    yield from re.split(r"\n[\t ]*\n(?:[\t ]*\n)*", text.strip("\n"))


class _BoundedXMLBuilder(ET.TreeBuilder):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.elements = 0

    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        # Parser callbacks also catch declarations in UTF-16, unlike byte scanning.
        raise DocumentParseError("DOCX XML document types and entities are not supported.")

    def start(self, tag: str, attrs: dict[str, str]) -> ET.Element:
        self.depth += 1
        self.elements += 1
        if self.depth > MAX_XML_DEPTH or self.elements > MAX_XML_ELEMENTS:
            raise DocumentLimitError("DOCX XML exceeds the structural limit.")
        return super().start(tag, attrs)

    def end(self, tag: str) -> ET.Element:
        self.depth -= 1
        return super().end(tag)


def _parse_xml(content: bytes) -> ET.Element:
    return ET.fromstring(content, parser=ET.XMLParser(target=_BoundedXMLBuilder()))


def _docx_paragraphs(content: bytes) -> Iterable[str]:
    # Validate the whole package before opening XML. Nothing is extracted to disk.
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ZIP_ENTRIES:
            raise DocumentLimitError("DOCX archive contains too many entries.")
        total = 0
        names: set[str] = set()
        parts: dict[str, bytes] = {}
        for entry in entries:
            name = entry.filename
            path = PurePosixPath(name)
            if (name in names or path.is_absolute() or ".." in path.parts
                    or "\\" in entry.orig_filename or "\x00" in entry.orig_filename):
                raise DocumentParseError("DOCX archive contains ambiguous or unsafe entry names.")
            names.add(name)
            if entry.flag_bits & 1:
                raise DocumentParseError("Encrypted DOCX documents are not supported.")
            if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise DocumentParseError("DOCX archive uses unsupported compression.")
            total += entry.file_size
            if entry.file_size > MAX_ZIP_ENTRY_BYTES or total > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise DocumentLimitError("DOCX archive exceeds the uncompressed size limit.")
            if entry.file_size > MAX_ZIP_COMPRESSION_RATIO * max(1, entry.compress_size):
                raise DocumentLimitError("DOCX archive exceeds the compression ratio limit.")
            with archive.open(entry) as stream:
                data = stream.read(MAX_ZIP_ENTRY_BYTES + 1)
            if len(data) > MAX_ZIP_ENTRY_BYTES or len(data) != entry.file_size:
                raise DocumentLimitError("DOCX archive exceeds its declared size limit.")
            if name in ("[Content_Types].xml", "word/document.xml"):
                parts[name] = data
        if len(parts) != 2:
            raise DocumentParseError("File is not a DOCX document package.")
        content_types = _parse_xml(parts["[Content_Types].xml"])
        if content_types.tag != _CONTENT_TYPES + "Types" or not any(
            item.get("PartName") == "/word/document.xml"
            and item.get("ContentType") == _DOCUMENT_CONTENT_TYPE
            for item in content_types.findall(_CONTENT_TYPES + "Override")
        ):
            raise DocumentParseError("File is not a supported DOCX document.")
        document = _parse_xml(parts["word/document.xml"])
        body = document.find(_WORD + "body")
        if document.tag != _WORD + "document" or body is None:
            raise DocumentParseError("DOCX document body is missing.")
        for paragraph in body.iter(_WORD + "p"):
            fragments: list[str] = []
            pending = list(reversed(list(paragraph)))
            while pending:
                node = pending.pop()
                # Nested textbox paragraphs have their own source number below.
                if node.tag == _WORD + "p":
                    continue
                if node.tag == _WORD + "t":
                    fragments.append(node.text or "")
                elif node.tag == _WORD + "tab":
                    fragments.append("\t")
                elif node.tag in (_WORD + "br", _WORD + "cr"):
                    fragments.append("\n")
                else:
                    pending.extend(reversed(list(node)))
            yield "".join(fragments)


def _pdf_pages(content: bytes) -> Iterable[str]:
    from pypdf import PdfReader, apply_configuration
    from pypdf.errors import LimitReachedError

    if not content.startswith(b"%PDF-"):
        raise DocumentParseError("File does not have a valid PDF header.")
    try:
        # Context-local pypdf 6.21+ guards apply before decompression and parsing,
        # including cross-reference/object streams and nested form invocations.
        with apply_configuration(
            maximum_declared_stream_length=MAX_UPLOAD_BYTES,
            array_based_stream_maximum_output_length=MAX_PDF_PAGE_STREAM_BYTES,
            zlib_maximum_output_length=MAX_PDF_PAGE_STREAM_BYTES,
            lzw_maximum_output_length=MAX_PDF_PAGE_STREAM_BYTES,
            run_length_maximum_output_length=MAX_PDF_PAGE_STREAM_BYTES,
            brotli_maximum_output_length=MAX_PDF_PAGE_STREAM_BYTES,
            zlib_maximum_recovery_input_length=100_000,
            flate_maximum_row_length=MAX_PDF_PAGE_STREAM_BYTES,
            stream_decoding_work_maximum_length=MAX_PDF_TOTAL_STREAM_BYTES,
            stream_parsing_work_maximum_count=100_000,
            page_tree_maximum_entries=1_000,
            page_tree_maximum_depth=20,
            xform_maximum_invocations_per_extraction=100,
            jbig2dec_binary=None,
        ):
            reader = PdfReader(io.BytesIO(content), strict=True)
            if reader.is_encrypted:
                raise DocumentParseError("Encrypted PDF documents are not supported.")
            if len(reader.pages) > MAX_PAGES:
                raise DocumentLimitError(f"PDF exceeds {MAX_PAGES} pages.")
            total_stream_bytes = 0
            total_text = 0
            texts: list[str] = []
            for number, page in enumerate(reader.pages, start=1):
                stream = page.get_contents()
                stream_bytes = len(stream.get_data()) if stream is not None else 0
                total_stream_bytes += stream_bytes
                if stream_bytes > MAX_PDF_PAGE_STREAM_BYTES or total_stream_bytes > MAX_PDF_TOTAL_STREAM_BYTES:
                    raise DocumentLimitError("PDF content streams exceed the processing limit.")
                text = page.extract_text() or ""
                if not text.strip() and stream_bytes:
                    raise DocumentParseError(
                        f"PDF page {number} has no extractable text; scanned or graphical pages require OCR."
                    )
                total_text += len(text)
                if len(text) > MAX_PAGE_CHARACTERS or total_text > MAX_TEXT_CHARACTERS:
                    raise DocumentLimitError("PDF exceeds the extracted text limit.")
                texts.append(text)
            # Complete inside the context so an outer validation error cannot
            # leave a suspended generator holding the temporary configuration.
            return texts
    except LimitReachedError:
        raise DocumentLimitError("PDF exceeds the bounded processing limits.") from None


def parse_document(content: bytes, filename: str) -> Document:
    """Parse an in-memory PDF/DOCX/UTF-8 TXT/MD assignment, preserving source indices.

    Raises ``DocumentParseError`` for invalid input and its ``DocumentLimitError``
    subclass for oversized input. The hard byte ceiling also applies to callers
    that bypass the HTTP upload limit.
    """
    if not isinstance(content, bytes):
        raise DocumentParseError("Document content must be bytes.")
    if not content:
        raise DocumentParseError("Document is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise DocumentLimitError("Document exceeds the 10 MiB upload limit.")
    if not isinstance(filename, str) or not filename.strip() or "\x00" in filename:
        raise DocumentParseError("A valid document filename is required.")
    # A browser may supply a local path; retain only its display name.
    display_name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not display_name or len(display_name) > 255:
        raise DocumentParseError("Document filename is invalid or too long.")
    extension = PurePosixPath(display_name).suffix.lower()
    parser = {".pdf": _pdf_pages, ".docx": _docx_paragraphs, ".txt": _text_paragraphs,
              ".md": _text_paragraphs}.get(extension)
    if parser is None:
        raise DocumentParseError("Supported assignment formats are PDF, DOCX, UTF-8 TXT and MD.")
    try:
        pages = _bounded_pages(parser(content))
    except DocumentParseError:
        raise
    except Exception:
        # Parser exception text may include document content and internal paths.
        raise DocumentParseError("Document is malformed or uses unsupported features.") from None
    return Document(filename=display_name, pages=pages, sha256=hashlib.sha256(content).hexdigest())
