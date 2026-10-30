"""PDF text extraction: GROBID optional, pypdf default, OCR/scan degrade."""

from __future__ import annotations

import io
import os
import re
from pathlib import Path

from fengsuan.app.core.config import get_settings

def clean_extracted_text(text: str) -> str:
    """pypdf 会从部分 Elsevier / 扫描件抽出 NUL。Postgres 文本列不能存 0x00。"""
    if not text:
        return ""
    return text.replace("\x00", "")


_HEADER_RE = re.compile(
    r"(ieee transactions|trans\.|vol\.\s*\d|no\.\s*\d|copyright|all rights reserved|"
    r"downloaded from|authorized licensed|issn\s*\d|doi:\s*10\.)",
    re.I,
)


def _pypdf_pages(content: bytes) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    return [clean_extracted_text(p.extract_text() or "") for p in reader.pages]


def _plain(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    return re.sub(r"\s+", " ", text).strip()


def grobid_base_url() -> str:
    url = (get_settings().grobid_url or os.environ.get("GROBID_URL") or "").rstrip("/")
    if not url:
        return ""
    try:
        from fengsuan.app.core.netguard import assert_http_url

        return assert_http_url(url, what="GROBID", allow_private=True).rstrip("/")
    except Exception:
        return ""


def grobid_alive() -> bool:
    url = grobid_base_url()
    if not url:
        return False
    try:
        import httpx

        r = httpx.get(f"{url}/api/isalive", timeout=4.0)
        return r.status_code == 200
    except Exception:
        return False


def grobid_xml(content: bytes, *, timeout: float = 25.0, detailed: bool = False) -> str | None:
    """detailed=True 时带段落页坐标和参考文献原文，给后台按章节切块用。"""
    url = grobid_base_url()
    if not url:
        return None
    data: dict[str, str] | None = None
    if detailed:
        data = {
            "consolidateHeader": "0",
            "consolidateCitations": "0",
            "includeRawCitations": "1",
            "teiCoordinates": "p,head,figure,biblStruct",
        }
    try:
        import httpx

        r = httpx.post(
            f"{url}/api/processFulltextDocument",
            files={"input": ("paper.pdf", content, "application/pdf")},
            data=data,
            timeout=timeout,
        )
        if r.status_code >= 400 or not r.text:
            return None
        return r.text
    except Exception:
        return None


def _try_grobid(content: bytes) -> str | None:
    return grobid_xml(content)


def pages_from_grobid_xml(xml: str) -> list[str] | None:
    """GROBID 只在 pypdf 抽不出字时兜底当正文用。它没有真实页界，顺序也是摊平的。"""
    pages: list[str] = []
    for i, head in enumerate(re.findall(r"<head[^>]*>(.*?)</head>", xml, flags=re.I | re.S), start=1):
        text = clean_extracted_text(_plain(head))
        if text:
            pages.append(f"[sec.{i}] {text}")
    for i, fig in enumerate(re.findall(r"<figure[^>]*>(.*?)</figure>", xml, flags=re.I | re.S), start=1):
        text = _plain(fig)
        if text:
            pages.append(f"[fig.{i}] {text[:800]}")
    for block in re.findall(r"<p[^>]*>(.*?)</p>", xml, flags=re.I | re.S):
        text = _plain(block)
        if text:
            pages.append(text)
    if pages:
        return pages
    title = re.search(r"<title[^>]*>(.*?)</title>", xml, flags=re.I | re.S)
    if title:
        return [_plain(title.group(1))]
    return None


def _locate_heading(title: str, pages: list[str]) -> int | None:
    """把章节标题落到真实页码。命不中就留空，不猜。"""
    needle = re.sub(r"\s+", " ", (title or "")).strip()
    if len(needle) < 4:
        return None
    flat = needle.replace(" ", "").lower()
    for idx, page in enumerate(pages or [], start=1):
        body = re.sub(r"\s+", "", (page or "")).lower()
        if flat and flat in body:
            return idx
    return None


def structure_from_grobid_xml(xml: str, pages: list[str] | None = None) -> dict:
    """章节与图表树。层级来自 GROBID 的 `n` 属性（1 / 1.2 / 1.2.3），不再摊平丢掉。"""
    sections: list[dict] = []
    for raw in re.findall(r"<head\b([^>]*)>(.*?)</head>", xml, flags=re.I | re.S):
        attrs, body = raw
        title = _plain(body)
        if not title:
            continue
        num = re.search(r'\bn="([^"]+)"', attrs or "")
        label = (num.group(1) if num else "").strip()
        level = label.count(".") + 1 if label else 1
        sections.append(
            {
                "label": label,
                "title": title[:240],
                "level": min(level, 4),
                "page_no": _locate_heading(title, pages or []),
            }
        )
    figures: list[dict] = []
    for attrs, body in re.findall(r"<figure\b([^>]*)>(.*?)</figure>", xml, flags=re.I | re.S):
        caption = _plain(re.search(r"<figDesc[^>]*>(.*?)</figDesc>", body, flags=re.I | re.S).group(1)) if re.search(
            r"<figDesc[^>]*>", body, flags=re.I
        ) else _plain(body)
        if not caption:
            continue
        kind = "table" if 'type="table"' in (attrs or "").lower() else "figure"
        figures.append(
            {
                "kind": kind,
                "caption": caption[:300],
                "page_no": _locate_heading(caption[:60], pages or []),
            }
        )
    title = re.search(r"<titleStmt[^>]*>.*?<title[^>]*>(.*?)</title>", xml, flags=re.I | re.S)
    return {
        "title": _plain(title.group(1)) if title else "",
        "sections": sections[:120],
        "figures": figures[:80],
    }


def _try_ocr(content: bytes) -> list[str] | None:
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
    except ImportError:
        return None
    try:
        images = convert_from_bytes(content, dpi=200, first_page=1, last_page=8)
    except Exception:
        return None
    pages = []
    for img in images:
        try:
            pages.append(pytesseract.image_to_string(img) or "")
        except Exception:
            pages.append("")
    return pages if any(p.strip() for p in pages) else None


def extract_pdf_full(content: bytes, *, use_grobid: bool = False) -> tuple[list[str], str, str | None, dict]:
    """返回 (页文本, 状态, 错误, 结构树)。

    页文本以 pypdf 的**真实页界**为准，翻页第 N 页就是 PDF 第 N 页。
    GROBID 只负责结构树；只有 pypdf 抽不出字时才退回用它的摊平文本当正文。
    默认不等 GROBID：上传请求先让人能翻页，结构由后台补；后台重试坏文件时才传 use_grobid=True。
    """
    looks_pdf = (content or b"").lstrip().startswith(b"%PDF")
    try:
        pages = _pypdf_pages(content)
    except Exception:
        pages = []
    pages = [clean_extracted_text(p) for p in pages]
    has_text = any((p or "").strip() for p in pages)
    xml = grobid_xml(content) if use_grobid else None
    structure = structure_from_grobid_xml(xml, pages if has_text else None) if xml else {}
    if has_text:
        return pages, "ok", None, structure
    if xml:
        flat = [clean_extracted_text(p) for p in (pages_from_grobid_xml(xml) or [])]
        if flat and any(p.strip() for p in flat):
            return flat, "ok", "grobid_flat", structure
    ocr = [clean_extracted_text(p) for p in (_try_ocr(content) or [])]
    if ocr and any(p.strip() for p in ocr):
        return ocr, "ok", "ocr", structure
    if not looks_pdf:
        return pages or [""], "failed", "extract_failed", structure
    return pages or [""], "unusable", "empty_or_scan", structure


def extract_pdf_bytes(content: bytes) -> tuple[list[str], str, str | None]:
    pages, status, err, _structure = extract_pdf_full(content, use_grobid=False)
    return pages, status, err


def looks_like_running_header(line: str) -> bool:
    s = re.sub(r"\s+", " ", (line or "").strip())
    if not s or len(s) < 8:
        return True
    if re.fullmatch(r"[\d\s\-–.,/]+", s):
        return True
    if _HEADER_RE.search(s):
        return True
    if re.match(r"^\d{3,}\s+\S+", s):
        return True
    return False


def filename_title(filename: str) -> str:
    stem = Path(filename or "").stem.replace("_", " ").replace("-", " ")
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem[:200]


def guess_literature_title(pages, filename: str = "", current: str = "") -> str:
    cur = re.sub(r"\s+", " ", (current or "").strip())
    if cur and cur.lower() not in {"untitled", "uploaded pdf"} and not looks_like_running_header(cur):
        return cur[:200]
    for page in pages or []:
        for raw in str(page or "").splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            if looks_like_running_header(line):
                continue
            if 12 <= len(line) <= 220:
                return line[:200]
    fn = filename_title(filename)
    if fn and fn.lower() not in {"paper", "uploaded pdf"}:
        return fn
    return cur[:200] if cur and not looks_like_running_header(cur) else (fn or "Uploaded PDF")


def extract_pdf_path(path: str) -> tuple[list[str], str, str | None]:
    """只要页文本。结构和参考文献由后台 GROBID 单独补，不在这里等。"""
    pages, status, err, _structure = extract_pdf_path_full(path, use_grobid=False)
    return pages, status, err


def extract_pdf_path_full(path: str, *, use_grobid: bool = False) -> tuple[list[str], str, str | None, dict]:
    p = Path(path)
    if not p.exists():
        return [], "failed", "missing", {}
    return extract_pdf_full(p.read_bytes(), use_grobid=use_grobid)
