"""
工具模板库 (Tool Templates)

为能力较弱的模型提供预制的工具模板，大幅提高工具创建成功率：
1. 提供经过验证的代码模板
2. 模型只需填空关键参数即可创建工具
3. 每个模板附带参数定义和依赖声明

提供预制工具模板，提高工具创建成功率
"""

from typing import Any

TOOL_TEMPLATES: dict[str, dict[str, Any]] = {
    "http_get": {
        "name": "http_get",
        "display_name": "HTTP GET 请求",
        "description": "发送HTTP GET请求获取网页或API数据",
        "category": "网络",
        "parameters": [
            {"name": "url", "type": "str", "description": "目标URL地址"},
            {"name": "headers", "type": "dict", "description": "请求头", "default": {}},
        ],
        "dependencies": ["requests"],
        "code": """import requests
response = requests.get(url, headers=headers, timeout=30)
response.raise_for_status()
content_type = response.headers.get('content-type', '')
if 'json' in content_type:
    result = response.json()
else:
    result = response.text[:5000]
""",
    },
    "http_post": {
        "name": "http_post",
        "display_name": "HTTP POST 请求",
        "description": "发送HTTP POST请求提交数据到API",
        "category": "网络",
        "parameters": [
            {"name": "url", "type": "str", "description": "目标URL地址"},
            {"name": "data", "type": "dict", "description": "要发送的JSON数据"},
            {"name": "headers", "type": "dict", "description": "请求头", "default": {}},
        ],
        "dependencies": ["requests"],
        "code": """import requests
response = requests.post(url, json=data, headers=headers, timeout=30)
response.raise_for_status()
content_type = response.headers.get('content-type', '')
if 'json' in content_type:
    result = response.json()
else:
    result = response.text[:5000]
""",
    },
    "web_scraper": {
        "name": "web_scraper",
        "display_name": "网页内容提取",
        "description": "抓取网页并提取文本内容（去除HTML标签）",
        "category": "网络",
        "parameters": [
            {"name": "url", "type": "str", "description": "要抓取的网页URL"},
        ],
        "dependencies": ["requests", "beautifulsoup4"],
        "code": """import requests
from bs4 import BeautifulSoup

response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
response.raise_for_status()
soup = BeautifulSoup(response.text, 'html.parser')

for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
    tag.decompose()

text = soup.get_text(separator='\\n', strip=True)
lines = [line.strip() for line in text.splitlines() if line.strip()]
result = '\\n'.join(lines[:200])
""",
    },
    "web_search": {
        "name": "web_search",
        "display_name": "网络搜索 (DuckDuckGo)",
        "description": "使用DuckDuckGo搜索引擎搜索信息",
        "category": "网络",
        "parameters": [
            {"name": "query", "type": "str", "description": "搜索关键词"},
            {"name": "max_results", "type": "int", "description": "最大结果数", "default": 5},
        ],
        "dependencies": ["duckduckgo-search"],
        "code": """from duckduckgo_search import DDGS

with DDGS() as ddgs:
    results = list(ddgs.text(query, max_results=max_results))

result = []
for r in results:
    result.append({
        'title': r.get('title', ''),
        'url': r.get('href', ''),
        'snippet': r.get('body', '')
    })
""",
    },
    "read_file": {
        "name": "read_file",
        "display_name": "读取文件",
        "description": "读取本地文件内容",
        "category": "文件",
        "parameters": [
            {"name": "filepath", "type": "str", "description": "文件路径"},
            {"name": "encoding", "type": "str", "description": "编码格式", "default": "utf-8"},
        ],
        "dependencies": [],
        "code": """with open(filepath, 'r', encoding=encoding) as f:
    result = f.read()
""",
    },
    "write_file": {
        "name": "write_file",
        "display_name": "写入文件",
        "description": "将内容写入本地文件",
        "category": "文件",
        "parameters": [
            {"name": "filepath", "type": "str", "description": "文件路径"},
            {"name": "content", "type": "str", "description": "要写入的内容"},
            {"name": "encoding", "type": "str", "description": "编码格式", "default": "utf-8"},
        ],
        "dependencies": [],
        "code": """import os
os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else '.', exist_ok=True)
with open(filepath, 'w', encoding=encoding) as f:
    f.write(content)
result = f"文件已写入: {filepath} ({len(content)} 字符)"
""",
    },
    "list_directory": {
        "name": "list_directory",
        "display_name": "列出目录",
        "description": "列出指定目录下的文件和文件夹",
        "category": "文件",
        "parameters": [
            {"name": "path", "type": "str", "description": "目录路径", "default": "."},
        ],
        "dependencies": [],
        "code": """import os
entries = []
for entry in os.scandir(path):
    info = {
        'name': entry.name,
        'type': 'dir' if entry.is_dir() else 'file',
        'size': entry.stat().st_size if entry.is_file() else None
    }
    entries.append(info)
entries.sort(key=lambda x: (x['type'] == 'file', x['name']))
result = entries
""",
    },
    "run_python": {
        "name": "run_python",
        "display_name": "执行Python代码",
        "description": "在安全环境中执行Python代码并返回结果",
        "category": "代码",
        "parameters": [
            {"name": "code_str", "type": "str", "description": "要执行的Python代码"},
        ],
        "dependencies": [],
        "code": """import io, contextlib
output_buffer = io.StringIO()
local_vars = {}
with contextlib.redirect_stdout(output_buffer):
    exec(code_str, {"__builtins__": __builtins__}, local_vars)
stdout_output = output_buffer.getvalue()
result = {
    "stdout": stdout_output,
    "variables": {k: str(v) for k, v in local_vars.items() if not k.startswith('_')}
}
""",
    },
    "json_processor": {
        "name": "json_processor",
        "display_name": "JSON数据处理",
        "description": "解析、过滤、转换JSON数据",
        "category": "数据",
        "parameters": [
            {"name": "json_text", "type": "str", "description": "JSON字符串或文件路径"},
            {"name": "jq_filter", "type": "str", "description": "JQ风格的过滤路径(用.分隔)", "default": ""},
        ],
        "dependencies": [],
        "code": """import json, os

if os.path.exists(json_text):
    with open(json_text, 'r', encoding='utf-8') as f:
        data = json.load(f)
else:
    data = json.loads(json_text)

if jq_filter:
    for key in jq_filter.split('.'):
        if key.strip():
            if isinstance(data, dict):
                data = data[key]
            elif isinstance(data, list) and key.isdigit():
                data = data[int(key)]
result = data
""",
    },
    "csv_reader": {
        "name": "csv_reader",
        "display_name": "CSV文件读取",
        "description": "读取CSV文件并返回结构化数据",
        "category": "数据",
        "parameters": [
            {"name": "filepath", "type": "str", "description": "CSV文件路径"},
            {"name": "max_rows", "type": "int", "description": "最大读取行数", "default": 100},
        ],
        "dependencies": [],
        "code": """import csv

rows = []
with open(filepath, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for i, row in enumerate(reader):
        if i >= max_rows:
            break
        rows.append(dict(row))
result = {"total_rows": len(rows), "columns": list(rows[0].keys()) if rows else [], "data": rows}
""",
    },
    "text_summarizer": {
        "name": "text_summarizer",
        "display_name": "文本统计分析",
        "description": "对文本进行字数统计、词频分析等基础NLP处理",
        "category": "文本",
        "parameters": [
            {"name": "text", "type": "str", "description": "要分析的文本"},
        ],
        "dependencies": [],
        "code": """import re
from collections import Counter

chars = len(text)
words = len(text.split())
lines = len(text.splitlines())
sentences = len(re.split(r'[.!?。！？]', text))

word_freq = Counter(text.lower().split()).most_common(10)

result = {
    "characters": chars,
    "words": words,
    "lines": lines,
    "sentences": sentences,
    "top_words": [{"word": w, "count": c} for w, c in word_freq]
}
""",
    },
    "calculator": {
        "name": "calculator",
        "display_name": "数学计算器",
        "description": "执行数学表达式计算（支持高级数学函数）",
        "category": "工具",
        "parameters": [
            {"name": "expression", "type": "str", "description": "数学表达式，如 '2**10 + math.sqrt(144)'"},
        ],
        "dependencies": [],
        "code": """import math
allowed_names = {k: v for k, v in math.__dict__.items() if not k.startswith('_')}
allowed_names.update({"abs": abs, "round": round, "min": min, "max": max, "sum": sum, "len": len})
result = eval(expression, {"__builtins__": {}}, allowed_names)
""",
    },
    "datetime_tool": {
        "name": "datetime_tool",
        "display_name": "日期时间工具",
        "description": "获取当前时间、计算时间差、格式化日期",
        "category": "工具",
        "parameters": [
            {"name": "operation", "type": "str", "description": "操作: now/format/diff/add", "default": "now"},
            {"name": "date_str", "type": "str", "description": "日期字符串(如 2024-01-15)", "default": ""},
            {"name": "days", "type": "int", "description": "天数(用于add/diff操作)", "default": 0},
        ],
        "dependencies": [],
        "code": """from datetime import datetime, timedelta

now = datetime.now()
if operation == "now":
    result = now.strftime("%Y-%m-%d %H:%M:%S")
elif operation == "format" and date_str:
    dt = datetime.fromisoformat(date_str)
    result = dt.strftime("%Y年%m月%d日 %H:%M:%S")
elif operation == "add":
    base = datetime.fromisoformat(date_str) if date_str else now
    new_date = base + timedelta(days=days)
    result = new_date.strftime("%Y-%m-%d %H:%M:%S")
elif operation == "diff" and date_str:
    dt = datetime.fromisoformat(date_str)
    diff = abs((now - dt).days)
    result = f"相差 {diff} 天"
else:
    result = now.strftime("%Y-%m-%d %H:%M:%S")
""",
    },
    "url_shortener_resolver": {
        "name": "url_info",
        "display_name": "URL信息获取",
        "description": "获取URL的标题、描述等元信息",
        "category": "网络",
        "parameters": [
            {"name": "url", "type": "str", "description": "要分析的URL"},
        ],
        "dependencies": ["requests", "beautifulsoup4"],
        "code": """import requests
from bs4 import BeautifulSoup

resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15, allow_redirects=True)
soup = BeautifulSoup(resp.text, 'html.parser')

title = soup.find('title')
title = title.string.strip() if title and title.string else ''

desc_tag = soup.find('meta', attrs={'name': 'description'}) or soup.find('meta', attrs={'property': 'og:description'})
desc = desc_tag.get('content', '') if desc_tag else ''

result = {
    'url': resp.url,
    'status': resp.status_code,
    'title': title,
    'description': desc[:300]
}
""",
    },
}


def get_template(name: str) -> dict[str, Any]:
    return TOOL_TEMPLATES.get(name)


def list_templates() -> list[dict[str, str]]:
    return [
        {
            "name": t["name"],
            "display_name": t.get("display_name", t["name"]),
            "description": t["description"],
            "category": t.get("category", ""),
        }
        for t in TOOL_TEMPLATES.values()
    ]


def get_templates_by_category() -> dict[str, list[dict]]:
    categories: dict[str, list[dict]] = {}
    for t in TOOL_TEMPLATES.values():
        cat = t.get("category", "其他")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(
            {
                "name": t["name"],
                "display_name": t.get("display_name", t["name"]),
                "description": t["description"],
            }
        )
    return categories


def get_template_prompt_section() -> str:
    cats = get_templates_by_category()
    lines = ["## 预制工具模板 (可通过 create_tool_from_template 一键安装)\n"]
    for cat, templates in cats.items():
        lines.append(f"### {cat}")
        for t in templates:
            lines.append(f"- **{t['display_name']}** (`{t['name']}`) — {t['description']}")
        lines.append("")
    return "\n".join(lines)
