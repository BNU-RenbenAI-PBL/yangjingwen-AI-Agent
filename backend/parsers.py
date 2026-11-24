# -*- coding: utf-8 -*-
"""多格式文档解析器：支持 PDF / Markdown / TXT / DOCX / EPUB。

统一输出格式：list[dict]，每项为：
{
    "text": str,   # 文本内容（正文可包含行内 Markdown 如 **bold**、`code`）
    "kind": str    # h1 | h2 | h3 | p | quote | list | code | hr | table
}
"""
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from collections import Counter


def _clean_text(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def _join_lines_smart(lines: list[str]) -> str:
    """智能拼接 PDF 同一段落内的多行：处理英文连字符、中英文空格。"""
    out = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not out:
            out = line
            continue
        # 英文行尾连字符断词合并
        if out.endswith("-") and len(out) >= 2 and out[-2].isascii() and line[0].isascii():
            out = out[:-1] + line
        # 中日韩字符之间不加多余空格
        elif ("\u4e00" <= out[-1] <= "\u9fff") and ("\u4e00" <= line[0] <= "\u9fff"):
            out += line
        else:
            out += " " + line
    return out


def parse_markdown_text(text: str) -> list[dict]:
    """解析 Markdown 或纯文本，保留标题、引用、列表、代码块、表格与段落结构。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    lines = text.split("\n")

    # 检测是否为“无空行纯文本”（如部分中文 TXT 电子书每行一段）
    non_empty = [l for l in lines if l.strip()]
    blank_count = sum(1 for l in lines if not l.strip())
    is_dense_plain = (
        len(non_empty) > 15
        and blank_count < len(non_empty) * 0.08
        and not any(l.strip().startswith(("#", ">", "```", "- ", "* ")) for l in non_empty[:30])
    )

    items: list[dict] = []
    buf: list[str] = []
    in_code = False
    code_buf: list[str] = []
    table_buf: list[str] = []

    def flush_p():
        nonlocal buf
        if buf:
            joined = _join_lines_smart(buf)
            if joined:
                items.append({"text": joined, "kind": "p"})
            buf = []

    def flush_table():
        nonlocal table_buf
        if table_buf:
            items.append({"text": "\n".join(table_buf), "kind": "table"})
            table_buf = []

    for line in lines:
        stripped = line.strip()

        # 代码块围栏
        if stripped.startswith("```"):
            flush_p()
            flush_table()
            if in_code:
                items.append({"text": "\n".join(code_buf), "kind": "code"})
                code_buf = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_buf.append(line.rstrip())
            continue

        # 空行
        if not stripped:
            flush_p()
            flush_table()
            continue

        # 分隔线
        if re.match(r"^[-*_]{3,}$", stripped):
            flush_p()
            flush_table()
            items.append({"text": "---", "kind": "hr"})
            continue

        # 表格行
        if stripped.startswith("|") and "|" in stripped[1:]:
            flush_p()
            table_buf.append(stripped)
            continue
        else:
            flush_table()

        # 标题
        m = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if m:
            flush_p()
            lvl = len(m.group(1))
            kind = "h1" if lvl == 1 else ("h2" if lvl == 2 else "h3")
            items.append({"text": m.group(2).strip(), "kind": kind})
            continue

        # 引用块
        if stripped.startswith(">"):
            flush_p()
            q_text = stripped.lstrip(">").strip()
            if q_text:
                if items and items[-1]["kind"] == "quote":
                    items[-1]["text"] += "\n" + q_text
                else:
                    items.append({"text": q_text, "kind": "quote"})
            continue

        # 列表项
        if re.match(r"^([-*+]|\d+[.)])\s+", stripped):
            flush_p()
            items.append({"text": stripped, "kind": "list"})
            continue

        # 密集纯文本模式：每行作为独立段落
        if is_dense_plain:
            items.append({"text": stripped, "kind": "p"})
        else:
            buf.append(stripped)

    if in_code and code_buf:
        items.append({"text": "\n".join(code_buf), "kind": "code"})
    flush_table()
    flush_p()

    return items or [{"text": text.strip() or "(空文档)", "kind": "p"}]


def parse_pdf_text(data: bytes) -> list[dict]:
    """高质量 PDF 解析：
    1. 优先使用 pymupdf4llm 提取保留排版与阅读顺序（双栏、标题、表格、列表）的 Markdown；
    2. 若不可用或失败，回退到 PyMuPDF 块级智能分段（过滤页眉页脚、按块分段、合并断行）。
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise RuntimeError("未安装 pymupdf，请执行 pip install pymupdf")

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        # 尝试 pymupdf4llm
        try:
            import pymupdf4llm
            md_text = pymupdf4llm.to_markdown(doc, show_progress=False)
            if md_text and len(md_text.strip()) > 30:
                # 清理 pymupdf4llm 偶尔产生的多余分页符与连续空行
                md_text = md_text.replace("\x0c", "\n\n")
                md_text = re.sub(r"\n{3,}", "\n\n", md_text)
                parsed = parse_markdown_text(md_text)
                if parsed:
                    return parsed
        except Exception:
            pass

        # 回退方案：PyMuPDF 块级解析（按页面坐标排序 + 过滤页眉页脚 + 保留块间段落）
        raw_blocks = []
        all_sizes = []
        for page in doc:
            page_h = page.rect.height or 800
            d = page.get_text("dict", sort=True)
            for b in d.get("blocks", []):
                if b.get("type") != 0:
                    continue
                bbox = b.get("bbox", (0, 0, 0, 0))
                # 过滤典型页眉 / 页脚短文本（如页码 "12"、"- 3 -"）
                lines_text = []
                max_size = 0.0
                for line in b.get("lines", []):
                    spans = line.get("spans", [])
                    if not spans:
                        continue
                    lt = "".join(s.get("text", "") for s in spans).strip()
                    if lt:
                        lines_text.append(lt)
                        s_max = max(s.get("size", 10.0) for s in spans)
                        if s_max > max_size:
                            max_size = s_max
                block_text = _join_lines_smart(lines_text)
                if not block_text:
                    continue
                is_edge = (bbox[1] < page_h * 0.06) or (bbox[3] > page_h * 0.94)
                if is_edge and len(block_text) < 40:
                    continue
                raw_blocks.append({"text": block_text, "size": round(max_size, 1)})
                all_sizes.append(round(max_size, 1))

        if not raw_blocks:
            return [{"text": "(未能从 PDF 中提取出文字，请确认不是纯图片扫描版 PDF)", "kind": "p"}]

        base_size = Counter(all_sizes).most_common(1)[0][0]
        result = []
        for b in raw_blocks:
            s = b["size"]
            t = b["text"]
            if s >= base_size + 3.5 and len(t) < 120:
                kind = "h1"
            elif s >= base_size + 1.4 and len(t) < 140:
                kind = "h2"
            else:
                kind = "p"
            result.append({"text": t, "kind": kind})
        return result
    finally:
        doc.close()


def parse_docx_text(data: bytes) -> list[dict]:
    """原生解析 .docx（无需 python-docx 依赖），识别标题层级与段落。"""
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    items = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if "word/document.xml" not in zf.namelist():
            return [{"text": "(无效的 DOCX 文件)", "kind": "p"}]
        xml_data = zf.read("word/document.xml")
    root = ET.fromstring(xml_data)
    for p in root.findall(".//w:body/w:p", ns):
        texts = [t.text for t in p.findall(".//w:t", ns) if t.text]
        line = "".join(texts).strip()
        if not line:
            continue
        style_el = p.find("./w:pPr/w:pStyle", ns)
        style_val = style_el.attrib.get(f"{{{ns['w']}}}val", "") if style_el is not None else ""
        sl = style_val.lower()
        if "heading1" in sl or sl in ("1", "title"):
            kind = "h1"
        elif "heading2" in sl or sl == "2":
            kind = "h2"
        elif "heading3" in sl or sl == "3":
            kind = "h3"
        else:
            kind = "p"
        items.append({"text": line, "kind": kind})
    return items or [{"text": "(DOCX 文档无正文内容)", "kind": "p"}]


class _HTMLBlockExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self._cur_tag = None
        self._buf = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style", "head"):
            self._skip = True
            return
        if tag in ("h1", "h2", "h3", "h4", "p", "li", "blockquote"):
            self._flush()
            self._cur_tag = tag

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style", "head"):
            self._skip = False
            return
        if tag == self._cur_tag:
            self._flush()
            self._cur_tag = None

    def handle_data(self, data):
        if not self._skip and self._cur_tag:
            self._buf.append(data)

    def _flush(self):
        if self._buf:
            txt = _clean_text("".join(self._buf))
            self._buf = []
            if txt:
                kind = "p"
                if self._cur_tag == "h1":
                    kind = "h1"
                elif self._cur_tag == "h2":
                    kind = "h2"
                elif self._cur_tag in ("h3", "h4"):
                    kind = "h3"
                elif self._cur_tag == "blockquote":
                    kind = "quote"
                elif self._cur_tag == "li":
                    kind = "list"
                    txt = "- " + txt
                self.items.append({"text": txt, "kind": kind})


def parse_epub_text(data: bytes) -> list[dict]:
    """原生解析 .epub 电子书（按 OPF spine 顺序提取章节标题与段落）。"""
    items = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        opf_path = None
        if "META-INF/container.xml" in names:
            c_root = ET.fromstring(zf.read("META-INF/container.xml"))
            rf = c_root.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
            if rf is not None:
                opf_path = rf.attrib.get("full-path")
        html_files = []
        if opf_path and opf_path in names:
            base_dir = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
            opf_root = ET.fromstring(zf.read(opf_path))
            manifest = {}
            for item in opf_root.iter():
                if item.tag.endswith("item"):
                    manifest[item.attrib.get("id", "")] = item.attrib.get("href", "")
            for itemref in opf_root.iter():
                if itemref.tag.endswith("itemref"):
                    idref = itemref.attrib.get("idref", "")
                    href = manifest.get(idref, "")
                    if href:
                        full = base_dir + href
                        if full in names:
                            html_files.append(full)
        if not html_files:
            html_files = sorted(n for n in names if n.lower().endswith((".xhtml", ".html", ".htm")))

        for hf in html_files:
            raw_html = zf.read(hf).decode("utf-8", errors="ignore")
            parser = _HTMLBlockExtractor()
            parser.feed(raw_html)
            items.extend(parser.items)
    return items or [{"text": "(未能从 EPUB 中提取出正文)", "kind": "p"}]


def parse_document(filename: str, data: bytes) -> list[dict]:
    """按文件扩展名分发解析器。"""
    lower = (filename or "").lower()
    if lower.endswith(".pdf"):
        return parse_pdf_text(data)
    if lower.endswith(".docx"):
        return parse_docx_text(data)
    if lower.endswith(".epub"):
        return parse_epub_text(data)
    text = data.decode("utf-8", errors="ignore")
    return parse_markdown_text(text)
