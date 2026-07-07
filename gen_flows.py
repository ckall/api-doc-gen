"""
gen_flows.py — 流程预测：从已生成的接口文档中分析用户操作流程

核心思路：
1. 读取 .api-doc-gen/docs/ 下的所有接口文档
2. 让 AI 分析这些接口之间的业务关系，识别出完整的操作流程
3. 生成流程文档，每步关联对应的接口文档（形成闭环）

输出：
    .api-doc-gen/docs/_flows/
    ├── 内容管理/
    │   ├── 创建并发布新书籍.md
    │   └── 审核书籍.md
    └── _flow_overview.md
"""

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from templates.flow_template import TEMPLATE, EXAMPLE

console = Console()


# ============================================================
# Prompt
# ============================================================



# ============================================================
# 工具函数
# ============================================================

def load_config() -> dict:
    """加载项目配置"""
    work_dir = os.path.join(os.getcwd(), ".api-doc-gen")
    config_path = os.path.join(work_dir, "config.yaml")

    if not os.path.isfile(config_path):
        console.print("[red]错误：找不到配置文件，请先运行 api-doc-gen init[/red]")
        raise SystemExit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_api_docs() -> list[dict]:
    """读取已生成的接口文档，提取关键信息"""
    work_dir = os.path.join(os.getcwd(), ".api-doc-gen")
    docs_dir = os.path.join(work_dir, "docs")

    if not os.path.isdir(docs_dir):
        console.print(f"[red]错误：找不到文档目录 {docs_dir}，请先运行 api-doc-gen run 生成接口文档[/red]")
        raise SystemExit(1)

    console.print(f"   文档目录: {docs_dir}")

    api_docs = []

    for root, _dirs, files in os.walk(docs_dir):
        for filename in files:
            if not filename.endswith(".md"):
                continue
            if filename.startswith("_"):
                continue

            filepath = os.path.join(root, filename)
            doc_info = parse_api_doc(filepath, docs_dir)
            if doc_info:
                api_docs.append(doc_info)

    if not api_docs:
        console.print("[red]错误：没有找到接口文档，请先运行 api-doc-gen run[/red]")
        raise SystemExit(1)

    return api_docs


def parse_api_doc(filepath: str, docs_dir: str) -> dict | None:
    """解析单个接口文档，提取关键信息"""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 解析 YAML frontmatter
    match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
    if not match:
        return None

    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None

    if not meta or not meta.get("method") or not meta.get("path"):
        return None

    # 提取业务逻辑部分
    business_logic = ""
    logic_match = re.search(r"## 业务逻辑\n\n(.+?)(?=\n## |\Z)", content, re.DOTALL)
    if logic_match:
        business_logic = logic_match.group(1).strip()

    # 提取约束部分
    constraints = ""
    constraints_match = re.search(r"## 约束\n\n(.+?)(?=\n## |\Z)", content, re.DOTALL)
    if constraints_match:
        constraints = constraints_match.group(1).strip()

    # 相对路径（用于流程文档中引用）
    rel_path = os.path.relpath(filepath, docs_dir)

    return {
        "method": meta["method"],
        "path": meta["path"],
        "summary": meta.get("summary", ""),
        "module": meta.get("module", ""),
        "group": meta.get("group", ""),
        "handler": meta.get("handler", ""),
        "tags": meta.get("tags", []),
        "business_logic": business_logic,
        "constraints": constraints,
        "doc_file": rel_path,  # 关联的文档文件路径
    }


def build_api_summaries(api_docs: list[dict], include_logic: bool = True) -> str:
    """构建给 AI 看的接口摘要"""
    lines = []
    for doc in api_docs:
        line = f"- {doc['method']} {doc['path']} | {doc['summary']} | 模块: {doc['module']} | 分组: {doc['group']}"
        if include_logic and doc.get("business_logic"):
            # 取前 150 字符，清洗掉可能触发安全过滤的内容
            logic_brief = _sanitize_for_prompt(doc["business_logic"][:150])
            if logic_brief:
                line += f" | 逻辑: {logic_brief}"
        lines.append(line)
    return "\n".join(lines)


def _sanitize_for_prompt(text: str) -> str:
    """清洗文本，去掉可能被模型误判为 injection 的内容"""
    # 去掉换行
    text = text.replace("\n", " ").strip()
    # 去掉看起来像指令的句式（中英文）
    # 这些模式如果出现在数据中会让某些模型拒绝响应
    patterns_to_remove = [
        r"你是.*?[。\.]",
        r"you are.*?[.\n]",
        r"请按照.*?[。\.]",
        r"please follow.*?[.\n]",
        r"ignore.*?instruction",
        r"忽略.*?指令",
    ]
    for pat in patterns_to_remove:
        text = re.sub(pat, "", text, flags=re.IGNORECASE)
    return text.strip()


def build_api_doc_index(api_docs: list[dict]) -> dict[str, str]:
    """构建 API path → 文档文件路径 的索引，用于生成关联链接"""
    index = {}
    for doc in api_docs:
        key = f"{doc['method']} {doc['path']}"
        index[key] = doc["doc_file"]
    return index


# ============================================================
# AI 分析
# ============================================================

class FlowOutline(BaseModel):
    """流程概要（第一步：轻量识别）"""
    flow_name: str
    title: str
    category: str
    role: str
    difficulty: str = "中等"
    description: str
    related_apis: list[str] = []  # 涉及的 api_path 列表


class FlowOutlineResult(BaseModel):
    """第一步 AI 输出"""
    flows: list[FlowOutline]


class FlowStep(BaseModel):
    """流程中的一个步骤"""
    order: int
    title: str
    action: str
    api_method: str
    api_path: str
    key_params: list[str] = []
    result: str = ""
    notes: str = ""


class FlowFAQ(BaseModel):
    """流程 FAQ"""
    question: str
    answer: str


class FlowDetail(BaseModel):
    """单个流程的完整详情（第二步：逐个生成）"""
    flow_name: str
    title: str
    category: str
    role: str
    difficulty: str = "中等"
    description: str
    role_description: str = ""
    prerequisites: list[str] = []
    steps: list[FlowStep]
    cautions: list[str] = []
    faq: list[FlowFAQ] = []
    tags: list[str] = []
    aliases: list[str] = []
    related_flows: list[str] = []


OUTLINE_SYSTEM = """You analyze API endpoint lists and identify user operation workflows.

Output requirements:
- Output ONLY valid JSON, no markdown code blocks, no explanation
- Format: {"flows": [{"flow_name": "snake_case", "title": "中文标题", "category": "分类", "role": "角色", "difficulty": "简单|中等|复杂", "description": "中文描述", "related_apis": ["/path1", "/path2"]}]}

Analysis rules:
- Group by user goals, not by API modules
- Cover complete paths (create → review → publish)
- related_apis must contain only paths from the provided list
- All Chinese for title/category/role/description fields"""

OUTLINE_USER = """Project: {project} | System: {system} | Service: {service}

{api_count} endpoints:

{api_summaries}"""


DETAIL_SYSTEM = """You generate detailed operation guides for a single workflow based on available API endpoints.

Output requirements:
- Output ONLY valid JSON, no markdown code blocks, no explanation
- Format: {"flow_name": "xxx", "title": "xxx", "category": "xxx", "role": "xxx", "difficulty": "xxx", "description": "xxx", "role_description": "xxx", "prerequisites": ["xxx"], "steps": [{"order": 1, "title": "xxx", "action": "xxx", "api_method": "GET", "api_path": "/xxx", "key_params": ["param(必填)"], "result": "xxx", "notes": "xxx"}], "cautions": ["xxx"], "faq": [{"question": "xxx", "answer": "xxx"}], "tags": ["xxx"], "aliases": ["xxx"], "related_flows": ["xxx"]}

Rules:
- Each step must use an actual API from the provided list
- action: describe as UI operations in Chinese (进入XX → 点击XX → 填写XX)
- All text content in Chinese except api_method and api_path"""

DETAIL_USER = """Workflow: {title}
Category: {category} | Role: {role} | Difficulty: {difficulty}
Description: {description}
Related APIs: {related_apis}

Available endpoints:

{api_summaries}"""


def analyze_flow_outlines(config: dict, api_docs: list[dict]) -> list[dict]:
    """第一步：AI 识别流程列表（轻量，不展开步骤）"""
    model = config.get("model") or os.environ.get("OPENAI_MODEL") or "claude-opus-4-8"
    base_url = config.get("base_url") or os.environ.get("OPENAI_BASE_URL") or ""
    api_key = config.get("api_key") or os.environ.get("OPENAI_API_KEY") or ""

    if not api_key:
        console.print("[red]错误：缺少 API Key，请检查配置或设置 OPENAI_API_KEY 环境变量[/red]")
        raise SystemExit(1)

    llm_kwargs = {
        "model": model,
        "api_key": api_key,
        "temperature": 0.3,
        "max_tokens": 4000,
    }
    if base_url:
        llm_kwargs["base_url"] = base_url

    llm = ChatOpenAI(**llm_kwargs)
    api_summaries = build_api_summaries(api_docs)

    user_msg = OUTLINE_USER.format(
        project=config.get("project", ""),
        system=config.get("system", ""),
        service=config.get("service", ""),
        api_count=len(api_docs),
        api_summaries=api_summaries,
    )

    messages = [
        SystemMessage(content=OUTLINE_SYSTEM),
        HumanMessage(content=user_msg),
    ]

    with console.status("[bold cyan]AI 识别流程列表...", spinner="dots"):
        result = _invoke_structured(llm, messages, FlowOutlineResult)

    if not result:
        return []

    return [f.model_dump() for f in result.flows]


def generate_flow_detail(config: dict, outline: dict, api_docs: list[dict]) -> dict | None:
    """第二步：为单个流程生成完整详情"""
    model = config.get("model") or os.environ.get("OPENAI_MODEL") or "claude-opus-4-8"
    base_url = config.get("base_url") or os.environ.get("OPENAI_BASE_URL") or ""
    api_key = config.get("api_key") or os.environ.get("OPENAI_API_KEY") or ""

    llm_kwargs = {
        "model": model,
        "api_key": api_key,
        "temperature": 0.3,
        "max_tokens": 4000,
    }
    if base_url:
        llm_kwargs["base_url"] = base_url

    llm = ChatOpenAI(**llm_kwargs)
    api_summaries = build_api_summaries(api_docs)

    user_msg = DETAIL_USER.format(
        title=outline["title"],
        category=outline["category"],
        role=outline["role"],
        difficulty=outline.get("difficulty", "中等"),
        description=outline["description"],
        related_apis=", ".join(outline.get("related_apis", [])),
        api_summaries=api_summaries,
    )

    messages = [
        SystemMessage(content=DETAIL_SYSTEM),
        HumanMessage(content=user_msg),
    ]

    result = _invoke_structured(llm, messages, FlowDetail)
    if not result:
        return None

    return result.model_dump()


def _invoke_structured(llm, messages, schema):
    """调用 LLM 并解析为结构化输出，自动回退"""
    # 先尝试 structured output
    try:
        structured_llm = llm.with_structured_output(schema)
        return structured_llm.invoke(messages)
    except Exception:
        pass

    # 回退：普通调用 + 手动解析
    try:
        response = llm.invoke(messages)
    except Exception as e:
        console.print(f"[red]AI 调用失败: {e}[/red]")
        return None

    content = response.content.strip()

    # 清理 markdown 代码块
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*\n?", "", content)
        content = re.sub(r"\n?\s*```\s*$", "", content)

    # 去掉尾随逗号
    content = re.sub(r",\s*([}\]])", r"\1", content)

    try:
        return schema.model_validate_json(content)
    except Exception as e:
        _write_error_log(content, str(e))
        log_path = os.path.join(os.getcwd(), ".api-doc-gen", "flow_error.log")
        console.print(f"[red]解析失败，日志已保存: {log_path}[/red]")
        return None


def _write_error_log(content: str, error_msg: str):
    """写入错误日志"""
    from datetime import datetime
    log_dir = os.path.join(os.getcwd(), ".api-doc-gen")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "flow_error.log")

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"=== Flow 分析错误日志 ===\n")
        f.write(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"错误: {error_msg}\n")
        f.write(f"输出长度: {len(content)} 字符\n")
        f.write(f"\n=== AI 完整原始输出 ===\n\n")
        f.write(content)


# ============================================================
# 模板渲染工具
# ============================================================

def _load_user_template(template_name: str) -> str | None:
    """加载用户自定义模板，找不到返回 None"""
    work_dir = os.path.join(os.getcwd(), ".api-doc-gen")
    template_path = os.path.join(work_dir, "templates", template_name)
    if os.path.isfile(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    return None


def _render_flow_with_jinja2(template_content: str, flow: dict, config: dict, api_doc_index: dict) -> str:
    """使用 Jinja2 模板渲染流程文档"""
    from jinja2 import Template

    template = Template(template_content, keep_trailing_newline=True)

    # 给每个 step 补充 doc_link
    steps = flow.get("steps", [])
    for step in steps:
        api_key = f"{step.get('api_method', '')} {step.get('api_path', '')}"
        doc_file = api_doc_index.get(api_key, "")
        step["doc_link"] = f"../{doc_file}" if doc_file else ""

    variables = {
        "id": f"{config.get('project', '')}::flow::{flow['flow_name']}",
        "project": config.get("project", ""),
        "system": config.get("system", ""),
        "flow_name": flow["flow_name"],
        "title": flow["title"],
        "category": flow.get("category", ""),
        "role": flow.get("role", ""),
        "difficulty": flow.get("difficulty", "中等"),
        "description": flow.get("description", ""),
        "role_description": flow.get("role_description", flow.get("role", "")),
        "updated_at": date.today().isoformat(),
        "tags": flow.get("tags", []),
        "aliases": flow.get("aliases", []),
        "related_flows": flow.get("related_flows", []),
        "prerequisites": flow.get("prerequisites", []),
        "steps": steps,
        "cautions": flow.get("cautions", []),
        "faq": flow.get("faq", []),
    }

    return template.render(**variables)


# ============================================================
# 渲染流程文档
# ============================================================

def render_flow_doc(flow: dict, config: dict, api_doc_index: dict) -> str:
    """渲染单个流程文档"""
    # 优先使用用户自定义模板
    template_content = _load_user_template("flow.md.j2")
    if template_content:
        return _render_flow_with_jinja2(template_content, flow, config, api_doc_index)

    # 回退到内置渲染逻辑
    parts = []

    # --- YAML frontmatter ---
    parts.append("---")
    parts.append(f'id: "{config.get("project", "")}::flow::{flow["flow_name"]}"')
    parts.append(f'project: {config.get("project", "")}')
    parts.append(f'system: {config.get("system", "")}')
    parts.append(f"type: flow")
    parts.append(f'flow_name: {flow["flow_name"]}')
    parts.append(f'category: {flow.get("category", "")}')
    parts.append(f'role: {flow.get("role", "")}')
    parts.append(f'difficulty: {flow.get("difficulty", "中等")}')
    parts.append("tags:")
    for tag in flow.get("tags", []):
        parts.append(f"  - {tag}")
    parts.append("aliases:")
    for alias in flow.get("aliases", []):
        parts.append(f"  - {alias}")
    parts.append("related_flows:")
    for rf in flow.get("related_flows", []):
        parts.append(f"  - {rf}")
    parts.append(f"updated_at: {date.today().isoformat()}")
    parts.append("---")
    parts.append("")

    # --- 标题和描述 ---
    parts.append(f"# {flow['title']}")
    parts.append("")
    parts.append(flow.get("description", ""))
    parts.append("")

    # --- 适用角色 ---
    parts.append("## 适用角色")
    parts.append("")
    parts.append(flow.get("role_description", flow.get("role", "")))
    parts.append("")

    # --- 前置条件 ---
    parts.append("## 前置条件")
    parts.append("")
    for prereq in flow.get("prerequisites", []):
        parts.append(f"- {prereq}")
    parts.append("")

    # --- 操作步骤（关联接口文档）---
    parts.append("## 操作步骤")
    parts.append("")
    for step in flow.get("steps", []):
        parts.append(f"### 第 {step['order']} 步：{step['title']}")
        parts.append("")
        parts.append(f"**操作**：{step.get('action', '')}")
        parts.append("")

        # 关联接口 — 带文档链接
        api_key = f"{step.get('api_method', '')} {step.get('api_path', '')}"
        api_display = f"`{api_key}`"
        doc_file = api_doc_index.get(api_key, "")
        if doc_file:
            # 生成相对链接（流程文档在 _flows/ 下，接口文档在上层）
            parts.append(f"**调用接口**：{api_display}  ")
            parts.append(f"📄 [查看接口详情](../{doc_file})")
        else:
            parts.append(f"**调用接口**：{api_display}")
        parts.append("")

        # 关键参数
        if step.get("key_params"):
            parts.append("**关键参数**：")
            for param in step["key_params"]:
                parts.append(f"- {param}")
            parts.append("")

        # 预期结果
        if step.get("result"):
            parts.append(f"**结果**：{step['result']}")
            parts.append("")

        # 补充说明
        if step.get("notes"):
            parts.append(f"> 💡 {step['notes']}")
            parts.append("")

        parts.append("---")
        parts.append("")

    # --- 注意事项 ---
    parts.append("## 注意事项")
    parts.append("")
    for caution in flow.get("cautions", []):
        parts.append(f"- {caution}")
    parts.append("")

    # --- 常见问题 ---
    parts.append("## 常见问题")
    parts.append("")
    for faq in flow.get("faq", []):
        parts.append(f"**Q: {faq['question']}**")
        parts.append(f"A: {faq['answer']}")
        parts.append("")

    # --- 涉及接口汇总表（带关联链接）---
    parts.append("## 涉及接口")
    parts.append("")
    parts.append("| 步骤 | 接口 | 说明 | 文档 |")
    parts.append("|------|------|------|------|")
    for step in flow.get("steps", []):
        api_key = f"{step.get('api_method', '')} {step.get('api_path', '')}"
        doc_file = api_doc_index.get(api_key, "")
        doc_link = f"[详情](../{doc_file})" if doc_file else "-"
        parts.append(f"| {step['title']} | {api_key} | {step.get('result', '')} | {doc_link} |")
    parts.append("")

    return "\n".join(parts)


def render_flow_overview(flows: list[dict], config: dict) -> str:
    """生成流程总览文档"""
    parts = []

    parts.append("---")
    parts.append(f"project: {config.get('project', '')}")
    parts.append(f"system: {config.get('system', '')}")
    parts.append(f"type: flow_overview")
    parts.append(f"updated_at: {date.today().isoformat()}")
    parts.append("---")
    parts.append("")
    parts.append(f"# {config.get('system', '')} - 操作流程总览")
    parts.append("")
    parts.append("本文档列出系统所有操作流程，帮助你快速找到「怎么做某件事」。")
    parts.append("")

    # 按 category 分组
    grouped: dict[str, list[dict]] = {}
    for flow in flows:
        cat = flow.get("category", "其他")
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(flow)

    for cat, cat_flows in sorted(grouped.items()):
        parts.append(f"## {cat}")
        parts.append("")
        parts.append("| 流程 | 角色 | 难度 | 说明 |")
        parts.append("|------|------|------|------|")
        for flow in cat_flows:
            title = flow["title"]
            role = flow.get("role", "")
            difficulty = flow.get("difficulty", "")
            desc = flow.get("description", "")[:50]
            parts.append(f"| [{title}]({flow['flow_name']}.md) | {role} | {difficulty} | {desc} |")
        parts.append("")

    return "\n".join(parts)


# ============================================================
# 主流程
# ============================================================

FLOW_MANIFEST_FILE = os.path.join(os.getcwd(), ".api-doc-gen", "flow-manifest.json")


def _load_flow_manifest() -> list[dict] | None:
    """加载已有的 flow-manifest.json"""
    if os.path.isfile(FLOW_MANIFEST_FILE):
        with open(FLOW_MANIFEST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_flow_manifest(outlines: list[dict]):
    """保存流程列表到 flow-manifest.json"""
    os.makedirs(os.path.dirname(FLOW_MANIFEST_FILE), exist_ok=True)
    with open(FLOW_MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(outlines, f, ensure_ascii=False, indent=2)


def _filter_outlines_by_path(outlines: list[dict], path_patterns: list[str]) -> list[dict]:
    """根据 --path 过滤：找出 related_apis 中包含匹配路径的流程"""
    import fnmatch
    matched = []
    for outline in outlines:
        apis = outline.get("related_apis", [])
        for api_path in apis:
            for pattern in path_patterns:
                if fnmatch.fnmatch(api_path, pattern):
                    matched.append(outline)
                    break
            else:
                continue
            break
    return matched


def _filter_outlines_by_module(outlines: list[dict], modules: list[str]) -> list[dict]:
    """根据 --module 过滤：找出 category 或 modules 字段匹配的流程"""
    matched = []
    for outline in outlines:
        # 匹配 category
        if outline.get("category", "") in modules:
            matched.append(outline)
            continue
        # 匹配 modules 字段（如果有）
        flow_modules = outline.get("modules", [])
        if any(m in modules for m in flow_modules):
            matched.append(outline)
    return matched


def run_flow_generation(args=None, auto: bool = False):
    """执行流程预测生成（两步式，支持增量）"""
    config = load_config()
    model = config.get("model") or os.environ.get("OPENAI_MODEL") or "claude-opus-4-8"

    # 解析参数
    path_filter = getattr(args, "path", None)
    module_filter = getattr(args, "module", None)
    force = getattr(args, "force", False)
    is_incremental = bool(path_filter or module_filter)

    console.print(Panel(
        f"[bold]流程预测[/bold]\n"
        f"项目: {config.get('project', '')}\n"
        f"系统: {config.get('system', '')}\n"
        f"模型: {model}\n"
        f"模式: {'增量' if is_incremental else '全量'}",
        title="api-doc-gen flow",
        border_style="cyan",
    ))

    # 1. 加载已生成的接口文档
    console.print("\n[cyan]📂 读取接口文档...[/cyan]")
    api_docs = load_api_docs()
    console.print(f"   找到 {len(api_docs)} 个接口文档")

    # 构建接口文档索引
    api_doc_index = build_api_doc_index(api_docs)

    # 2. 获取流程列表（优先用已有的 flow-manifest.json）
    existing_manifest = _load_flow_manifest()

    if existing_manifest and not force:
        console.print(f"\n[cyan]📋 使用已有的 flow-manifest.json（{len(existing_manifest)} 个流程）[/cyan]")
        console.print(f"   如需重新识别，请加 --force 参数")
        outlines = existing_manifest
    else:
        # AI 识别流程列表
        console.print("\n[cyan]🤖 第一步：AI 识别流程列表...[/cyan]")
        outlines = analyze_flow_outlines(config, api_docs)

        if not outlines:
            console.print("[yellow]未识别出任何流程，请检查接口文档是否已生成[/yellow]")
            return

        # 持久化到 flow-manifest.json
        _save_flow_manifest(outlines)
        console.print(f"   [green]已保存到 flow-manifest.json[/green]")

    # 3. 如果是增量模式，过滤出受影响的流程
    if path_filter:
        patterns = [p.strip() for p in path_filter.split(",")]
        outlines = _filter_outlines_by_path(outlines, patterns)
        console.print(f"\n[cyan]🔍 路径匹配: {len(outlines)} 个流程受影响[/cyan]")
    elif module_filter:
        modules = [m.strip() for m in module_filter.split(",")]
        outlines = _filter_outlines_by_module(outlines, modules)
        console.print(f"\n[cyan]🔍 模块匹配: {len(outlines)} 个流程受影响[/cyan]")

    if not outlines:
        console.print("[yellow]没有匹配的流程需要生成[/yellow]")
        return

    # 4. 人工确认
    console.print(f"\n[green]{'需要更新' if is_incremental else '识别出'} {len(outlines)} 个操作流程：[/green]")
    for i, outline in enumerate(outlines, 1):
        console.print(
            f"  {i}. [bold]{outline['title']}[/bold] "
            f"({outline.get('category', '')}, {outline.get('role', '')}, {outline.get('difficulty', '')})"
        )

    if not auto:
        if not Confirm.ask("\n[yellow]确认生成这些流程文档？[/yellow]", default=True):
            console.print("[dim]已取消[/dim]")
            return

    # 5. 逐个生成流程详情
    console.print(f"\n[cyan]📝 逐个生成流程详情...[/cyan]")
    work_dir = os.path.join(os.getcwd(), ".api-doc-gen")
    flows_dir = os.path.join(work_dir, "docs", "_flows")

    completed_flows = []
    for i, outline in enumerate(outlines, 1):
        console.print(f"\n   [{i}/{len(outlines)}] {outline['title']}...")

        with console.status(f"[cyan]生成中...", spinner="dots"):
            flow_detail = generate_flow_detail(config, outline, api_docs)

        if not flow_detail:
            console.print(f"   [red]✗ 生成失败，跳过[/red]")
            continue

        # 按 category 分目录
        category = flow_detail.get("category", "其他")
        cat_dir = os.path.join(flows_dir, category)
        os.makedirs(cat_dir, exist_ok=True)

        # 渲染并写文件
        content = render_flow_doc(flow_detail, config, api_doc_index)
        filename = f"{flow_detail['flow_name']}.md"
        filepath = os.path.join(cat_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        completed_flows.append(flow_detail)
        console.print(f"   [green]✓ {category}/{filename}[/green]")

    if not completed_flows:
        console.print("[red]所有流程生成失败[/red]")
        return

    # 6. 生成总览（增量模式下合并已有的流程文档）
    if is_incremental:
        # 读取所有已有的 flow 文档重新生成总览
        from gen_skill import load_flows_from_docs
        all_flows = load_flows_from_docs(flows_dir)
        overview_content = render_flow_overview(all_flows, config)
    else:
        overview_content = render_flow_overview(completed_flows, config)

    overview_path = os.path.join(flows_dir, "_flow_overview.md")
    os.makedirs(flows_dir, exist_ok=True)
    with open(overview_path, "w", encoding="utf-8") as f:
        f.write(overview_content)

    console.print(f"\n[green]✅ 流程文档生成完成[/green]")
    console.print(f"   生成 {len(completed_flows)} / {len(outlines)} 个流程文档")
    console.print(f"   总览: {overview_path}")
    console.print(f"   目录: {flows_dir}/")
    if is_incremental:
        console.print(f"   模式: 增量更新")
    console.print(f"\n[dim]提示: flow-manifest.json 已保存流程与接口的映射关系[/dim]")
