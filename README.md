# PDF MCP Server

一个基于 [FastMCP](https://github.com/jlowin/fastmcp) 的 PDF 处理工具集，通过 MCP (Model Context Protocol) 提供多种 PDF 操作能力。

## 功能

| 工具 | 说明 |
|------|------|
| `pdf_extract_text` | 提取 PDF 文本内容，支持自动分片、增量提取、plain_text 模式 |
| `pdf_info` | 获取 PDF 元信息（页数、大小、标题、作者等） |
| `pdf_search` | 搜索关键词，返回匹配页码和上下文 |
| `pdf_merge` | 合并多个 PDF 文件 |
| `pdf_split` | 拆分 PDF（按页或按范围） |
| `pdf_extract_tables` | 提取表格数据（需安装 pdfplumber） |
| `pdf_list_tools` | 列出所有可用工具 |

## 安装

```bash
# 克隆仓库
git clone https://github.com/your-username/pdf-server.git
cd pdf-server

# 安装依赖
pip install fastmcp pypdf

# 可选：表格提取支持
pip install pdfplumber
```

## 使用

作为 MCP 服务器运行（stdio 传输）：

```bash
python server.py
```

在支持 MCP 的客户端（如 Claude Desktop）中配置：

```json
{
  "mcpServers": {
    "pdf-tools": {
      "command": "python",
      "args": ["path/to/server.py"]
    }
  }
}
```

### 功能亮点

- **自动分片**：提取大文本时自动按字符数切分为多个临时文件
- **增量提取**：通过 `start_page` / `end_page` 参数分批提取
- **plain_text 模式**：设置 `plain_text=True` 使用纯文本标签替代 emoji，避免 GBK 终端编码问题
- **输出到文件**：`pdf_extract_text` 支持 `output_file` 参数直接写入文件

## 依赖

- Python 3.8+
- [fastmcp](https://github.com/jlowin/fastmcp) — MCP 服务器框架
- [pypdf](https://github.com/py-pdf/pypdf) — PDF 处理核心
- [pdfplumber](https://github.com/jsvine/pdfplumber)（可选）— 表格提取

## 许可

MIT
