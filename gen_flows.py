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
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from templates.flow_template import TEMPLATE, EXAMPLE

console = Console()


# ============================================================
# Prompt
# ============================================================

SYSTEM_PROMPT = """你是一个业务流程分析专家。你的任务是从 API 接口文档中分析出完整的用户操作流程。

你的目标用户是：**完全不懂这个系统的人**。他们需要知道"我要完成某个目标，需要按什么顺序做什么操作"。

## 你会收到的信息

1. 所有接口的摘要信息（method、path、summary、module、group、业务逻辑）
2. 项目基本信息（系统名称、功能模块等）

## 你需要输出

分析这些接口的业务关系，识别出若干完整的操作流程。每个流程包含：

```json
{
  "flows": [
    {
      "flow_name": "create_and_publish_book",
      "title": "创建并发布新书籍",
      "category": "内容管理",
      "role": "管理员",
      "difficulty": "中等",
      "description": "一段话描述这个流程是干什么的",
      "role_description": "什么角色在什么场景下用到",
      "prerequisites": ["前置条件1", "前置条件2"],
      "steps": [
        {
          "order": 1,
          "title": "创建书籍基本信息",
          "action": "进入书籍管理 → 点击「添加书籍」→ 填写表单",
          "api_method": "POST",
          "api_path": "/admin/book/add",
          "key_params": ["book_name(必填)", "author(必填)", "category(必填)"],
          "result": "书籍创建成功，状态为「待审核」",
          "notes": "书名不能重复"
        }
      ],
      "cautions": ["注意事项1", "注意事项2"],
      "faq": [
        {"question": "审核被拒怎么办？", "answer": "修改信息后重新提交"}
      ],
      "tags": ["书籍", "发布", "上架"],
      "aliases": ["怎么发布一本新书", "新书上架流程"],
      "related_flows": ["审核书籍", "绑定作者"]
    }
  ]
}
```

## 分析原则

1. **从用户目标出发**：不是按接口分组，而是按"用户想完成什么"分组
2. **完整路径**：从第一步到最后一步，包括可能的分支（如审核拒绝后的处理）
3. **跨角色流程**：如果一个流程涉及多个角色（管理员创建→作者签约），要完整体现
4. **关联接口文档**：每步明确标注调用的 API method + path，这样可以链接到接口文档
5. **实用性优先**：FAQ 要覆盖真实使用中容易遇到的问题
6. **难度标注**：简单（1-2步）/ 中等（3-5步）/ 复杂（5步以上或有分支）

## 注意

- 只分析你能从接口文档中确认的流程，不要凭空编造
- 每个流程的 api_path 必须是接口列表中实际存在的
- 如果某些接口是独立操作（如查看列表），可以组合成"日常运营"类流程
- 输出纯 JSON，不要加 markdown 代码块标记"""


ANALYSIS_PROMPT = """## 项目信息

- 项目: {project}
- 系统: {system}
- 服务: {service}

## 接口清单（共 {api_count} 个）

{api_summaries}

## 要求

请分析以上接口，识别出所有有意义的用户操作流程。
每个流程的 steps 里的 api_path 必须是上面列表中实际存在的接口路径。

输出 JSON 格式（直接输出，不要 markdown 代码块）。"""


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
        console.print("[red]错误：找不到文档目录，请先运行 api-doc-gen run 生成接口文档[/red]")
        raise SystemExit(1)

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


def build_api_summaries(api_docs: list[dict]) -> str:
    """构建给 AI 看的接口摘要"""
    lines = []
    for doc in api_docs:
        line = f"- **{doc['method']} {doc['path']}** | {doc['summary']} | 模块: {doc['module']} | 分组: {doc['group']}"
        if doc["business_logic"]:
            # 只取前 200 字符的业务逻辑摘要
            logic_brief = doc["business_logic"][:200].replace("\n", " ")
            line += f"\n  业务逻辑: {logic_brief}"
        lines.append(line)
    return "\n".join(lines)


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

def analyze_flows(config: dict, api_docs: list[dict]) -> list[dict]:
    """调用 AI 分析接口关系，生成流程"""
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
    }
    if base_url:
        llm_kwargs["base_url"] = base_url

    llm = ChatOpenAI(**llm_kwargs)

    api_summaries = build_api_summaries(api_docs)

    prompt = ANALYSIS_PROMPT.format(
        project=config.get("project", ""),
        system=config.get("system", ""),
        service=config.get("service", ""),
        api_count=len(api_docs),
        api_summaries=api_summaries,
    )

    console.print("[cyan]正在分析接口关系，识别操作流程...[/cyan]")

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ]

    response = llm.invoke(messages)
    content = response.content.strip()

    # 清理可能的 markdown 代码块标记
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\n?", "", content)
        content = re.sub(r"\n?```$", "", content)

    try:
        result = json.loads(content)
        flows = result.get("flows", [])
        console.print(f"[green]识别出 {len(flows)} 个操作流程[/green]")
        return flows
    except json.JSONDecodeError as e:
        console.print(f"[red]AI 输出解析失败: {e}[/red]")
        console.print(Panel(content[:500], title="AI 原始输出"))
        return []


# ============================================================
# 渲染流程文档
# ============================================================

def render_flow_doc(flow: dict, config: dict, api_doc_index: dict) -> str:
    """渲染单个流程文档"""
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

def run_flow_generation(args=None, auto: bool = False):
    """执行流程预测生成"""
    config = load_config()

    console.print(Panel(
        f"[bold]流程预测[/bold]\n"
        f"项目: {config.get('project', '')}\n"
        f"系统: {config.get('system', '')}",
        title="api-doc-gen flow",
        border_style="cyan",
    ))

    # 1. 加载已生成的接口文档
    console.print("\n[cyan]📂 读取接口文档...[/cyan]")
    api_docs = load_api_docs()
    console.print(f"   找到 {len(api_docs)} 个接口文档")

    # 构建接口文档索引（path → 文件路径）
    api_doc_index = build_api_doc_index(api_docs)

    # 2. AI 分析
    console.print("\n[cyan]🤖 AI 分析接口关系...[/cyan]")
    flows = analyze_flows(config, api_docs)

    if not flows:
        console.print("[yellow]未识别出任何流程，请检查接口文档是否已生成[/yellow]")
        return

    # 3. 人工确认
    if not auto:
        console.print(f"\n[green]识别出 {len(flows)} 个操作流程：[/green]")
        for i, flow in enumerate(flows, 1):
            steps_count = len(flow.get("steps", []))
            console.print(
                f"  {i}. [bold]{flow['title']}[/bold] "
                f"({flow.get('category', '')}, {flow.get('role', '')}, {steps_count} 步)"
            )

        if not Confirm.ask("\n[yellow]确认生成这些流程文档？[/yellow]", default=True):
            console.print("[dim]已取消[/dim]")
            return

    # 4. 生成流程文档
    console.print("\n[cyan]📝 生成流程文档...[/cyan]")
    work_dir = os.path.join(os.getcwd(), ".api-doc-gen")
    flows_dir = os.path.join(work_dir, "docs", "_flows")

    file_count = 0
    for flow in flows:
        # 按 category 分目录
        category = flow.get("category", "其他")
        cat_dir = os.path.join(flows_dir, category)
        os.makedirs(cat_dir, exist_ok=True)

        # 渲染文档
        content = render_flow_doc(flow, config, api_doc_index)

        # 写文件
        filename = f"{flow['flow_name']}.md"
        filepath = os.path.join(cat_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        file_count += 1
        console.print(f"   ✓ {category}/{filename}")

    # 5. 生成总览
    overview_content = render_flow_overview(flows, config)
    overview_path = os.path.join(flows_dir, "_flow_overview.md")
    os.makedirs(flows_dir, exist_ok=True)
    with open(overview_path, "w", encoding="utf-8") as f:
        f.write(overview_content)

    console.print(f"\n[green]✅ 流程文档生成完成[/green]")
    console.print(f"   生成 {file_count} 个流程文档")
    console.print(f"   总览: {overview_path}")
    console.print(f"   目录: {flows_dir}/")
    console.print(f"\n[dim]提示: 流程文档中的接口链接已关联到对应的 API 详细文档[/dim]")
