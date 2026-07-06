"""
PDF MCP Server - 本地 PDF 文件处理工具

提供功能：
- 提取 PDF 文本内容（自动分片、增量提取、plain_text 模式）
- 获取 PDF 元信息（页数、标题、作者等）
- 合并多个 PDF
- 拆分 PDF 页面
- 搜索 PDF 中的文本
- 提取 PDF 中的表格
"""

import uuid
import tempfile
from pathlib import Path
from typing import Optional
from fastmcp import FastMCP
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError

mcp = FastMCP("PDF 文件工具")

# ============ 常量 ============

TEMP_DIR = Path(tempfile.gettempdir()) / "pdf-tools"  # 分片临时文件目录
DEFAULT_MAX_CHARS = 60000                              # 单次返回最大字符数
MAX_PAGES = 200                                        # 单次最大提取页数
MAX_SEARCH_PAGES = 500                                 # 单次最大搜索页数

# 分割线（已移到 _tag 中管理）


# ============ 辅助函数 ============


def _ensure_temp_dir() -> Path:
    """确保分片临时目录存在。"""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    return TEMP_DIR


def _resolve_path(path_str: str) -> Path:
    """解析文件路径，不存在时抛出 FileNotFoundError。"""
    p = Path(path_str).resolve()
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")
    return p


_SEP_CHARS = "━" * 40


def _tag(plain: bool = False) -> dict:
    """根据 plain_text 模式返回标签映射，用于避免 GBK 终端上的 emoji 编码问题。

    plain=True 时所有非 ASCII 标识符替换为纯文本等价物。
    """
    if plain:
        return dict(doc="[DOC]", err="[ERROR]", ok="[OK]", warn="[WARN]",
                    search="[SEARCH]", table="[TABLE]", tools="[TOOLS]",
                    tip="[TIP]", info="[INFO]",
                    bullet="-", dash="-", sep="-" * 40)
    return dict(doc="📄", err="❌", ok="✅", warn="⚠️",
                search="🔍", table="📊", tools="🛠️",
                tip="💡", info="📋",
                bullet="•", dash="—", sep=_SEP_CHARS)


def _parse_pages(spec: str, total: int) -> list[int]:
    """解析页码范围，返回 0-based 页码列表（已排序、去重）。

    Raises:
        ValueError: spec 格式非法时抛出。
    """
    result: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", maxsplit=1)
            start = max(1, int(a.strip()))
            end = min(total, int(b.strip()))
            result.update(range(start - 1, end))
        else:
            n = int(part)
            if 1 <= n <= total:
                result.add(n - 1)
    return sorted(result)


def _resolve_page_indices(
    total: int,
    pages: Optional[str] = None,
    start_page: Optional[int] = None,
    end_page: Optional[int] = None,
) -> list[int]:
    """统一计算需要提取的页码列表。

    优先级: pages > (start_page, end_page) > 全部。
    """
    if pages:
        return _parse_pages(pages, total)

    s = max(0, (start_page or 1) - 1)   # 转 0-based
    e = min(total, end_page or total)    # exclusive end in range()

    if s >= e:
        return []
    return list(range(s, e))


def _format_size(bytes_: int) -> str:
    """人类可读的文件大小。"""
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_ < 1024:
            return f"{bytes_:.1f} {unit}"
        bytes_ /= 1024
    return f"{bytes_:.1f} TB"


def _write_chunks(
    page_texts: list[tuple[int, str]],
    header: str,
    file_stem: str,
    max_chars: int,
) -> list[Path]:
    """将页面文本列表按 max_chars 分片写入临时文件。

    Args:
        page_texts: [(0-based_page_index, page_content), ...]
        header: 文档头行（如 "📄 paper.pdf — 80 页"）
        file_stem: 用于生成文件名的原始文件名（不含扩展名）
        max_chars: 每片的最大字符数

    Returns:
        写入的临时文件路径列表。
    """
    tmp_dir = _ensure_temp_dir()
    session_id = uuid.uuid4().hex[:8]

    chunks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []

    # 估算头部 + 当前页的总长度
    def _estimate(pages: list[tuple[int, str]]) -> int:
        total = len(header) + 2  # header + final newline
        for pi, pt in pages:
            total += len(f"\n--- 第 {pi+1} 页 ---\n") + len(pt)
        return total

    for item in page_texts:
        # 计算单加这一页的估算
        trial = current + [item]
        if _estimate(trial) > max_chars and current:
            # 当前段已足够，开启新段
            chunks.append(current)
            current = [item]
        else:
            current.append(item)

    if current:
        chunks.append(current)

    result_files: list[Path] = []
    for i, chunk_pages in enumerate(chunks):
        lines = [header]
        page_nums: list[int] = []
        for pi, pt in chunk_pages:
            lines.append(f"\n--- 第 {pi+1} 页 ---")
            lines.append(pt.strip())
            page_nums.append(pi + 1)

        text = "\n".join(lines)

        # 文件名: xxx_p1-15_abc123.txt
        p_start, p_end = min(page_nums), max(page_nums)
        fname = f"{file_stem}_p{p_start}-{p_end}_{session_id}.txt"
        fpath = tmp_dir / fname
        fpath.write_text(text, encoding="utf-8")
        result_files.append(fpath)

    return result_files


# ============ MCP 工具 ============


@mcp.tool()
def pdf_extract_text(
    file_path: str,
    pages: Optional[str] = None,
    start_page: Optional[int] = None,
    end_page: Optional[int] = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    plain_text: bool = False,
    output_file: Optional[str] = None,
) -> str:
    """提取 PDF 文件的文本内容，大文本自动分片为临时文件。

    Args:
        file_path: PDF 文件路径（绝对路径）
        pages: 指定页码，如 "1,3,5-10"，留空则提取全部
        start_page: 起始页码（从 1 开始），与 end_page 配合实现增量提取
        end_page: 结束页码（包含），与 start_page 配合实现增量提取
        max_chars: 单次返回最大字符数，超过则自动分片写入临时文件
        plain_text: 为 True 时使用纯文本标记 [DOC] [ERROR] 替代 emoji，避免 GBK 终端编码问题
        output_file: 指定输出文件路径（绝对路径），文本将直接写入该文件而非返回
    """
    t = _tag(plain_text)
    path = _resolve_path(file_path)
    reader = PdfReader(path)
    total = len(reader.pages)

    page_indices = _resolve_page_indices(total, pages, start_page, end_page)
    if not page_indices:
        return f"{t['warn']} 页码范围无效，没有匹配的页面"

    if len(page_indices) > MAX_PAGES:
        return (
            f"{t['warn']} 文档共 {total} 页，请求提取 {len(page_indices)} 页，"
            f"超过单次限制（{MAX_PAGES} 页）。请缩小页码范围，如 '1-{MAX_PAGES}'"
        )

    # --- 逐页提取 ---
    page_texts: list[tuple[int, str]] = []
    for i in page_indices:
        text = reader.pages[i].extract_text()
        page_texts.append((i, text.strip()))

    header = f"{t['doc']} {path.name} — {total} 页"

    # --- 如果指定了 output_file，直接写到文件 ---
    if output_file:
        out = Path(output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [header]
        for pi, pt in page_texts:
            lines.append(f"\n--- 第 {pi+1} 页 ---")
            lines.append(pt)
        out.write_text("\n".join(lines), encoding="utf-8")
        return f"{t['ok']} 文本已写入: {out}"

    # --- 检查是否需要分片 ---
    total_chars = len(header) + sum(14 + len(str(pi + 1)) + 6 + len(pt) for pi, pt in page_texts)

    if total_chars <= max_chars:
        # 直接返回
        lines = [header]
        for pi, pt in page_texts:
            lines.append(f"\n--- 第 {pi+1} 页 ---")
            lines.append(pt)
        return "\n".join(lines)

    # --- 超限：分片写入临时文件 ---
    chunk_files = _write_chunks(page_texts, header, path.stem, max_chars)

    result = [
        header,
        f"{t['info']} 文本共 {total_chars:,} 字符，已自动分为 {len(chunk_files)} 段：",
    ]
    for i, cf in enumerate(chunk_files):
        # 从文件名解析页码范围
        stem = cf.stem
        if "_p" in stem:
            try:
                range_part = stem.split("_p")[1].split("_")[0]
                result.append(f"  [{i+1}/{len(chunk_files)}] 第 {range_part} 页 → {cf}")
            except (IndexError, ValueError):
                result.append(f"  [{i+1}/{len(chunk_files)}] {cf}")
        else:
            result.append(f"  [{i+1}/{len(chunk_files)}] {cf}")

    result.append(f"\n{t['tip']} 使用 Read 工具读取上方文件路径以获取完整内容")
    return "\n".join(result)


@mcp.tool()
def pdf_info(
    file_path: str,
    plain_text: bool = False,
) -> str:
    """获取 PDF 文件的元信息（页数、大小、标题、作者等）。

    Args:
        file_path: PDF 文件路径（绝对路径）
        plain_text: 为 True 时使用纯文本标记替代 emoji
    """
    t = _tag(plain_text)
    path = _resolve_path(file_path)
    reader = PdfReader(path)
    meta = reader.metadata or {}

    size_bytes = path.stat().st_size

    return (
        f"{t['doc']} {path.name}\n"
        f"{t['sep']}\n"
        f"文件路径: {path}\n"
        f"文件大小: {_format_size(size_bytes)}\n"
        f"总页数:   {len(reader.pages)}\n"
        f"PDF 版本: {getattr(reader, 'pdf_header', 'N/A')}\n"
        f"\n"
        f"{t['info']} 元数据:\n"
        f"  标题:    {meta.get('/Title', 'N/A')}\n"
        f"  作者:    {meta.get('/Author', 'N/A')}\n"
        f"  主题:    {meta.get('/Subject', 'N/A')}\n"
        f"  创建者:  {meta.get('/Creator', 'N/A')}\n"
        f"  生产者:  {meta.get('/Producer', 'N/A')}"
    )


@mcp.tool()
def pdf_search(
    file_path: str,
    keyword: str,
    plain_text: bool = False,
) -> str:
    """在 PDF 文件中搜索关键词，返回匹配的页码和上下文。

    Args:
        file_path: PDF 文件路径（绝对路径）
        keyword: 要搜索的关键词
        plain_text: 为 True 时使用纯文本标记替代 emoji
    """
    t = _tag(plain_text)
    path = _resolve_path(file_path)
    reader = PdfReader(path)

    total_pages = len(reader.pages)
    if total_pages > MAX_SEARCH_PAGES:
        return (
            f"{t['warn']} 文档共 {total_pages} 页，超过单次搜索限制（{MAX_SEARCH_PAGES} 页）。"
            f"请使用 pdf_extract_text 按范围提取后手动搜索"
        )

    total_matches = 0
    results: list[str] = []
    kw_lower = keyword.lower()

    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        text_lower = text.lower()
        if kw_lower not in text_lower:
            continue

        pos = 0
        while True:
            idx = text_lower.find(kw_lower, pos)
            if idx == -1:
                break
            start = max(0, idx - 50)
            end = min(len(text), idx + len(keyword) + 50)
            context = text[start:end].replace("\n", " ")
            results.append(f"    第 {i+1} 页: ...{context}...")
            total_matches += 1
            pos = idx + len(kw_lower)

    if not results:
        return f"未在 \"{path.name}\" 中找到关键词 \"{keyword}\""

    return f"{t['search']} 在 \"{path.name}\" 中找到 {total_matches} 处 \"{keyword}\":\n" + "\n".join(results)


@mcp.tool()
def pdf_merge(file_paths: list[str], output_path: str) -> str:
    """合并多个 PDF 文件为一个。

    Args:
        file_paths: 要合并的 PDF 文件路径列表（绝对路径）
        output_path: 输出文件路径（绝对路径）
    """
    if not file_paths:
        return "❌ 文件列表为空，请提供至少一个 PDF 文件"

    if Path(output_path).exists():
        return f"❌ 输出文件已存在: {output_path}，请先删除或使用其他路径"

    writer = PdfWriter()
    total_pages = 0
    merged_files: list[str] = []

    for fp in file_paths:
        path = _resolve_path(fp)
        reader = PdfReader(path)
        for page in reader.pages:
            writer.add_page(page)
        total_pages += len(reader.pages)
        merged_files.append(path.name)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "wb") as f:
        writer.write(f)

    return (
        f"✅ 合并完成！\n"
        f"合并文件: {', '.join(merged_files)}\n"
        f"总页数: {total_pages}\n"
        f"输出: {output}"
    )


@mcp.tool()
def pdf_split(
    file_path: str,
    output_dir: str,
    page_range: Optional[str] = None,
) -> str:
    """拆分 PDF 文件为单页或多个页面组。

    Args:
        file_path: PDF 文件路径（绝对路径）
        output_dir: 输出目录（绝对路径）
        page_range: 指定页码范围，如 "1-3,5,7-9"，留空则每页一个文件
    """
    path = _resolve_path(file_path)
    reader = PdfReader(path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = path.stem

    if page_range:
        page_indices = _parse_pages(page_range, len(reader.pages))
        if not page_indices:
            return "⚠️ 页码范围无效，没有匹配的页面"
        out_path = out_dir / f"{stem}_pages_{page_range.replace(',', '_')}.pdf"
        if out_path.exists():
            return f"❌ 输出文件已存在: {out_path}，请先删除或使用其他路径"
        writer = PdfWriter()
        for i in page_indices:
            writer.add_page(reader.pages[i])
        with open(out_path, "wb") as f:
            writer.write(f)
        return f"✅ 已提取 {len(page_indices)} 页 → {out_path}"

    # 每页一个文件
    out_parts: list[str] = []
    for i in range(len(reader.pages)):
        writer = PdfWriter()
        writer.add_page(reader.pages[i])
        out_path = out_dir / f"{stem}_p{i+1}.pdf"
        with open(out_path, "wb") as f:
            writer.write(f)
        out_parts.append(f"  第 {i+1} 页 → {out_path}")
    return f"✅ 已拆分为 {len(reader.pages)} 个文件:\n" + "\n".join(out_parts)


@mcp.tool()
def pdf_extract_tables(
    file_path: str,
    page_number: Optional[int] = None,
    plain_text: bool = False,
) -> str:
    """提取 PDF 中的表格数据（需安装 pdfplumber）。

    Args:
        file_path: PDF 文件路径（绝对路径）
        page_number: 指定页码（从 1 开始），留空则提取所有页
        plain_text: 为 True 时使用纯文本标记替代 emoji
    """
    t = _tag(plain_text)
    try:
        import pdfplumber  # noqa: F811
    except ImportError:
        return f"{t['err']} 需要安装 pdfplumber 库才能提取表格，请运行: pip install pdfplumber"

    path = _resolve_path(file_path)

    results: list[str] = []
    with pdfplumber.open(path) as pdf:
        if page_number is not None:
            if page_number < 1 or page_number > len(pdf.pages):
                return f"{t['warn']} 页码无效: {page_number}，文档共 {len(pdf.pages)} 页"
            pages_to_check = [pdf.pages[page_number - 1]]
        else:
            pages_to_check = pdf.pages

        for page in pages_to_check:
            tables = page.extract_tables()
            if not tables:
                continue

            pn = page.page_number
            results.append(f"\n{t['table']} 第 {pn} 页 — {len(tables)} 个表格:")
            for ti, table in enumerate(tables):
                results.append(f"\n  表格 {ti+1}:")
                for row in table:
                    cleaned = [str(cell).strip() if cell else "" for cell in row]
                    results.append(f"    | {'  |  '.join(cleaned)} |")

    if not results:
        return f"未在 \"{path.name}\" 中找到表格数据。"

    return "\n".join(results)


@mcp.tool()
def pdf_list_tools(plain_text: bool = False) -> str:
    """列出本 PDF MCP 服务器提供的所有工具及其说明。

    Args:
        plain_text: 为 True 时使用纯文本标记替代 emoji
    """
    t = _tag(plain_text)
    tools = [
        ("pdf_extract_text", "提取文本（自动分片、增量提取 start_page/end_page、plain_text 模式）"),
        ("pdf_info",       "获取元信息（页数、大小、作者等）"),
        ("pdf_search",     "搜索关键词，返回页码和上下文"),
        ("pdf_merge",      "合并多个 PDF 文件"),
        ("pdf_split",      "拆分 PDF（按页或按范围）"),
        ("pdf_extract_tables", "提取表格数据（需 pdfplumber）"),
    ]

    lines = [f"{t['tools']} PDF MCP 服务器 {t['dash']} 可用工具:\n"]
    for name, desc in tools:
        lines.append(f"   {t['bullet']} {name} {t['dash']} {desc}")
    lines.extend([
        "",
        f"{t['tip']} 提取工具支持 plain_text=True 以关闭 emoji，避免 GBK 终端编码问题",
        f"{t['tip']} pdf_extract_text 支持 start_page/end_page 增量提取，超限自动分片",
        f"{t['tip']} pdf_extract_text 支持 output_file 参数直接写入文件",
        f"{t['tip']} 文件路径使用绝对路径，如 D:/Documents/file.pdf",
    ])
    return "\n".join(lines)


# ============ 入口 ============

if __name__ == "__main__":
    mcp.run(transport="stdio")
