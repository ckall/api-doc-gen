"""
LangGraph 节点：人工确认 + 文档渲染 + 文件输出
"""

import json
import os
import re
from datetime import date

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

from .state import PipelineState, PipelineConfig

console = Console()


# ============================================================
# 人工确认节点
# ============================================================

def human_review(state: PipelineState) -> dict:
    """人工审核 AI 分析结果，可以 approve / edit / skip"""
    config = PipelineConfig(**state["config"]) if isinstance(state["config"], dict) else state["config"]
    pending = state.get("pending_review", [])

    if not pending:
        return {"human_decision": "approve", "approved_entries": []}

    if config.auto_approve:
        console.print("[yellow]⚡ auto_approve 模式，自动通过[/yellow]")
        return {
            "human_decision": "approve",
            "approved_entries": state.get("approved_entries", []) + pending,
            "pending_review": [],
        }

    # 显示当前批次摘要
    batch_idx = state.get("current_batch_index", 0)
    total_batches = len(state.get("manifest", [])) // config.batch_size + 1
    console.print(f"\n[bold cyan]━━━ 批次 {batch_idx + 1}/{total_batches} ━━━[/bold cyan]")

    table = Table(title=f"AI 分析结果 ({len(pending)} 个接口)")
    table.add_column("#", style="dim", width=4)
    table.add_column("接口", style="green")
    table.add_column("AI 描述", style="white")
    table.add_column("标签", style="cyan")
    table.add_column("约束", style="yellow", width=30)

    for i, entry in enumerate(pending):
        tags_str = ", ".join(entry.get("tags", [])[:4])
        constraints = entry.get("business_constraints", [])
        constraints_str = "; ".join(constraints[:2]) if constraints else "-"
        table.add_row(
            str(i + 1),
            f"{entry['method']} {entry['path']}",
            entry.get("enriched_summary", entry.get("summary", "")),
            tags_str,
            constraints_str,
        )

    console.print(table)

    # 显示错误码摘要（如果有）
    entries_with_errors = [e for e in pending if e.get("error_codes")]
    if entries_with_errors:
        console.print(f"\n[dim]📋 {len(entries_with_errors)} 个接口检测到错误码定义[/dim]")

    # 人工决策
    console.print("\n[bold]操作选项:[/bold]")
    console.print("  [green]a[/green] - 全部通过")
    console.print("  [yellow]d[/yellow] - 查看某个接口详情")
    console.print("  [red]s[/red] - 跳过本批次")
    console.print("  [red]q[/red] - 中止整个流程")

    while True:
        choice = Prompt.ask("\n选择", choices=["a", "d", "s", "q"], default="a")

        if choice == "a":
            return {
                "human_decision": "approve",
                "approved_entries": state.get("approved_entries", []) + pending,
                "pending_review": [],
            }
        elif choice == "d":
            # 查看详情
            idx_str = Prompt.ask("输入序号查看详情", default="1")
            try:
                idx = int(idx_str) - 1
                if 0 <= idx < len(pending):
                    show_entry_detail(pending[idx])
                else:
                    console.print("[red]序号超出范围[/red]")
            except ValueError:
                console.print("[red]请输入数字[/red]")
            continue  # 回到选择循环
        elif choice == "s":
            console.print("[yellow]跳过本批次[/yellow]")
            return {
                "human_decision": "skip",
                "pending_review": [],
            }
        elif choice == "q":
            console.print("[red]中止流程[/red]")
            return {
                "human_decision": "abort",
                "pending_review": [],
            }


def show_entry_detail(entry: dict):
    """显示单个接口的详细 AI 分析结果"""
    content_parts = []
    content_parts.append(f"[bold]{entry['method']} {entry['path']}[/bold]")
    content_parts.append(f"描述: {entry.get('enriched_summary', entry.get('summary', ''))}")
    content_parts.append("")

    # 业务逻辑
    logic = entry.get("business_logic", "")
    if logic:
        content_parts.append("[bold green]业务逻辑:[/bold green]")
        content_parts.append(logic)
        content_parts.append("")

    # 业务约束
    constraints = entry.get("business_constraints", [])
    if constraints:
        content_parts.append("[yellow]业务约束:[/yellow]")
        for c in constraints:
            content_parts.append(f"  • {c}")
        content_parts.append("")

    # 错误码
    error_codes = entry.get("error_codes", [])
    if error_codes:
        content_parts.append("[red]错误码:[/red]")
        for ec in error_codes:
            code = ec.get("code", "")
            msg = ec.get("message", "")
            sug = ec.get("suggestion", "")
            content_parts.append(f"  • {code}: {msg} → {sug}")
        content_parts.append("")

    # 关联接口
    related = entry.get("related_apis", [])
    if related:
        content_parts.append("[cyan]关联接口:[/cyan]")
        for r in related:
            content_parts.append(f"  • {r}")
        content_parts.append("")

    # 标签 & 别名
    content_parts.append(f"标签: {', '.join(entry.get('tags', []))}")
    content_parts.append(f"别名: {', '.join(entry.get('aliases', []))}")

    # notes
    notes = entry.get("notes", "")
    if notes:
        content_parts.append(f"\n[dim]备注: {notes}[/dim]")

    console.print(Panel("\n".join(content_parts), title=entry.get("handler", "")))


# ============================================================
# 文档渲染节点
# ============================================================

def render_batch(state: PipelineState) -> dict:
    """将当前批次已确认的条目立即写入 Markdown 文件"""
    config = PipelineConfig(**state["config"]) if isinstance(state["config"], dict) else state["config"]
    approved = state.get("approved_entries", [])

    # 只渲染本批次新增的（通过对比已生成文件数量判断）
    already_rendered = len(state.get("generated_files", []))
    new_entries = approved[already_rendered:]

    if not new_entries:
        return {"status": "本批次无需渲染（跳过或无新增）"}

    # 输出目录
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_dir = os.path.join(script_dir, config.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    generated_files = state.get("generated_files", [])

    for entry in new_entries:
        # 创建模块目录
        module_dir = os.path.join(output_dir, entry.get("group", "未分类"), entry.get("module", "未分类"))
        os.makedirs(module_dir, exist_ok=True)

        # 文件名
        path_normalized = entry["path"].strip("/").replace("/", "_").replace("{", "").replace("}", "").replace(":", "")
        filename = f"{entry['method']}_{path_normalized}.md"
        file_path = os.path.join(module_dir, filename)

        # 渲染
        content = render_enriched_doc(entry, config)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        generated_files.append(file_path)

    console.print(f"[green]📝 已写入 {len(new_entries)} 个文档 → {output_dir}/[/green]")
    for entry in new_entries:
        path_normalized = entry["path"].strip("/").replace("/", "_").replace("{", "").replace("}", "").replace(":", "")
        console.print(f"   • {entry['method']} {entry['path']} → {entry.get('group', '')}/{entry.get('module', '')}/{entry['method']}_{path_normalized}.md")

    return {
        "generated_files": generated_files,
        "status": f"本批次写入 {len(new_entries)} 个文档，累计 {len(generated_files)}",
    }


def render_overview_node(state: PipelineState) -> dict:
    """最后生成 overview 总览文档"""
    config = PipelineConfig(**state["config"]) if isinstance(state["config"], dict) else state["config"]
    approved = state.get("approved_entries", [])

    if not approved:
        return {"status": "无已确认接口，跳过 overview 生成"}

    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_dir = os.path.join(script_dir, config.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    overview_path = os.path.join(output_dir, "_overview.md")
    overview_content = render_overview(approved, config)
    with open(overview_path, "w", encoding="utf-8") as f:
        f.write(overview_content)

    generated_files = state.get("generated_files", [])
    generated_files.append(overview_path)

    console.print(f"[green]📋 overview 已生成: _overview.md[/green]")

    return {
        "generated_files": generated_files,
        "status": f"全部完成，共 {len(approved)} 个接口文档 + overview",
    }


def render_enriched_doc(entry: dict, config: PipelineConfig) -> str:
    """渲染增强版接口文档"""
    parts = []

    # Frontmatter
    parts.append("---")
    parts.append(f'id: "{entry["id"]}"')
    parts.append(f"project: {config.project}")
    parts.append(f"system: {config.system}")
    parts.append(f"service: {config.service}")
    parts.append(f"module: {entry.get('module', '')}")
    parts.append(f"group: {entry.get('group', '')}")
    parts.append(f"method: {entry['method']}")
    parts.append(f"path: {entry['path']}")
    parts.append(f"summary: {entry.get('enriched_summary', entry.get('summary', ''))}")
    parts.append(f"version: {config.version}")

    if entry.get("handler"):
        parts.append(f"handler: {entry['handler']}")

    tags = entry.get("tags", [])
    if tags:
        parts.append("tags:")
        for t in tags:
            parts.append(f"  - {t}")

    aliases = entry.get("aliases", [])
    if aliases:
        parts.append("aliases:")
        for a in aliases:
            parts.append(f"  - {a}")

    related = entry.get("related_apis", [])
    if related:
        parts.append("related:")
        for r in related:
            parts.append(f"  - {r}")

    parts.append(f"updated_at: {date.today().isoformat()}")
    parts.append("---")
    parts.append("")

    # 标题
    handler_name = entry.get("handler", "").split(".")[-1] if entry.get("handler") else ""
    title = handler_name or f"{entry['method']} {entry['path']}"
    parts.append(f"# {title}")
    parts.append("")
    parts.append(entry.get("enriched_summary", entry.get("summary", "")))
    parts.append("")

    # 请求
    parts.append("## 请求")
    parts.append("")
    parts.append(f"```")
    parts.append(f"{entry['method']} {entry['path']}")
    parts.append(f"```")
    parts.append("")

    # 参数
    params = entry.get("parameters", [])
    if params:
        parts.append("## 参数")
        parts.append("")
        parts.append("| 参数 | 类型 | 必填 | 说明 |")
        parts.append("|------|------|------|------|")
        for p in params:
            required = "是" if p.get("required") else "否"
            desc = p.get("description", "").replace("\n", " ").replace("|", "\\|")
            parts.append(f"| {p.get('name', '')} | {p.get('type', '')} | {required} | {desc} |")
        parts.append("")

    # 业务逻辑（AI 分析源码后生成的核心内容）
    business_logic = entry.get("business_logic", "")
    if business_logic:
        parts.append("## 业务逻辑")
        parts.append("")
        # 确保每个步骤独立成行
        # AI 可能返回 "1. xxx\n2. xxx" 或 "1. xxx 2. xxx"
        logic_text = business_logic.replace("\\n", "\n")  # 处理转义的\n
        # 如果没有换行但有编号模式，强制换行
        if "\n" not in logic_text:
            logic_text = re.sub(r'(\d+\.\s)', r'\n\1', logic_text).strip()
        parts.append(logic_text)
        parts.append("")

    # 响应
    resp_fields = entry.get("response_fields", [])
    if resp_fields:
        parts.append("## 响应")
        parts.append("")
        parts.append("| 字段 | 类型 | 说明 |")
        parts.append("|------|------|------|")
        for f in resp_fields:
            desc = f.get("description", "").replace("\n", " ").replace("|", "\\|")
            parts.append(f"| {f.get('name', '')} | {f.get('type', '')} | {desc} |")
        parts.append("")

    # 数据依赖（AI 增强 - 服务间关联的关键信息）
    data_deps = entry.get("data_dependencies", {})
    has_deps = any([
        data_deps.get("database"),
        data_deps.get("mq_produce"),
        data_deps.get("mq_consume"),
        data_deps.get("http_calls"),
        data_deps.get("config_reads"),
        data_deps.get("hardcoded"),
    ])
    if has_deps:
        parts.append("## 数据依赖")
        parts.append("")

        # 数据库操作
        db_ops = data_deps.get("database", [])
        if db_ops:
            parts.append("### 数据库")
            parts.append("")
            for op in db_ops:
                parts.append(f"- {op.get('operation', 'read')}: `{op.get('table', '')}` — {op.get('description', '')}")
            parts.append("")

        # MQ 发送
        mq_produce = data_deps.get("mq_produce", [])
        if mq_produce:
            parts.append("### MQ 发送")
            parts.append("")
            for mq in mq_produce:
                line = f"- → `{mq.get('topic', '')}` / `{mq.get('event', '')}` — {mq.get('trigger', '')}"
                if mq.get("payload_summary"):
                    line += f"（payload: {mq['payload_summary']}）"
                parts.append(line)
            parts.append("")

        # MQ 消费
        mq_consume = data_deps.get("mq_consume", [])
        if mq_consume:
            parts.append("### MQ 消费")
            parts.append("")
            for mq in mq_consume:
                parts.append(f"- ← `{mq.get('topic', '')}` / `{mq.get('event', '')}` — {mq.get('action', '')}")
            parts.append("")

        # HTTP 外部调用
        http_calls = data_deps.get("http_calls", [])
        if http_calls:
            parts.append("### HTTP 外部调用")
            parts.append("")
            for call in http_calls:
                parts.append(f"- {call.get('method', 'GET')} `{call.get('service', '')}{call.get('path', '')}` — {call.get('description', '')}")
            parts.append("")

        # 配置读取
        config_keys = data_deps.get("config_keys", [])
        if config_keys:
            parts.append("### 配置依赖")
            parts.append("")
            for ck in config_keys:
                parts.append(f"- `{ck.get('key', '')}` (文件: {ck.get('config_file', '未知')}) — {ck.get('usage', '')}")
            parts.append("")

    # 调用链（AI 增强 - 子方法级别，精确到入参/出参/字段操作）
    sub_calls = entry.get("sub_calls", [])
    if sub_calls:
        parts.append("## 调用链")
        parts.append("")
        for sc in sub_calls:
            parts.append(f"### `{sc.get('method', '')}`")
            parts.append("")
            parts.append(f"- 文件: `{sc.get('file', '')}`")
            if sc.get("input"):
                parts.append(f"- 入参: `{sc['input']}`")
            if sc.get("output"):
                parts.append(f"- 返回: `{sc['output']}`")
            if sc.get("logic"):
                parts.append(f"- 逻辑: {sc['logic']}")

            # 数据库操作（字段级）
            for db in sc.get("db_operations", []):
                condition = f" WHERE {db['condition']}" if db.get("condition") else ""
                parts.append(f"  - DB {db.get('action', '')}: `{db.get('table', '')}` 字段: {db.get('fields', '')}{condition}")

            # MQ 操作
            for mq in sc.get("mq_operations", []):
                topic_info = f"配置key: `{mq['topic_config_key']}`" if mq.get("topic_config_key") else f"topic: {mq.get('topic_source', '')}"
                parts.append(f"  - MQ {mq.get('action', 'produce')}: {topic_info} / event: `{mq.get('event', '')}` / payload: {mq.get('payload_fields', '')}")

            # HTTP 调用
            for hc in sc.get("http_calls", []):
                url_info = f"配置key: `{hc['url_config_key']}`" if hc.get("url_config_key") else hc.get("url_source", "")
                parts.append(f"  - HTTP {hc.get('method', '')} {url_info} — {hc.get('description', '')}")

            # 配置读取
            for cr in sc.get("config_reads", []):
                parts.append(f"  - 读配置: `{cr.get('key', '')}` (文件: {cr.get('config_file', '')}) — {cr.get('usage', '')}")

            # 硬编码值
            for hv in sc.get("hardcoded_values", []):
                parts.append(f"  - 硬编码: `{hv.get('value', '')}` → {hv.get('field', '')} ({hv.get('context', '')})")

            parts.append("")

    # 业务约束（AI 增强）
    constraints = entry.get("business_constraints", [])
    if constraints:
        parts.append("## 约束")
        parts.append("")
        for c in constraints:
            parts.append(f"- {c}")
        parts.append("")

    # 错误码（AI 增强）
    error_codes = entry.get("error_codes", [])
    if error_codes:
        parts.append("## 错误码")
        parts.append("")
        parts.append("| code | 说明 | 处理建议 |")
        parts.append("|------|------|----------|")
        for ec in error_codes:
            parts.append(f"| {ec.get('code', '')} | {ec.get('message', '')} | {ec.get('suggestion', '')} |")
        parts.append("")

    # 关联接口（AI 增强）
    if related:
        parts.append("## 关联接口")
        parts.append("")
        for r in related:
            parts.append(f"- {r}")
        parts.append("")

    # 源码位置
    if entry.get("handler_file"):
        parts.append("## 源码")
        parts.append("")
        parts.append(f"- Handler: `{entry.get('handler', '')}` → `{entry['handler_file']}`")
        if entry.get("route_file"):
            parts.append(f"- 路由: `{entry['route_file']}`")
        parts.append("")

    # AI 备注
    notes = entry.get("notes", "")
    if notes and notes != "AI 分析异常":
        parts.append("## 备注")
        parts.append("")
        parts.append(notes)
        parts.append("")

    return "\n".join(parts)


def render_overview(entries: list[dict], config: PipelineConfig) -> str:
    """生成总览文档"""
    parts = []
    parts.append("---")
    parts.append(f"project: {config.project}")
    parts.append(f"system: {config.system}")
    parts.append(f"service: {config.service}")
    parts.append("type: overview")
    parts.append(f"updated_at: {date.today().isoformat()}")
    parts.append("---")
    parts.append("")
    parts.append(f"# {config.system} - API 接口总览")
    parts.append("")
    parts.append(f"服务: `{config.service}`  ")
    parts.append(f"接口总数: {len(entries)}")
    parts.append("")

    # 按 group → module 分组
    grouped: dict[str, dict[str, list[dict]]] = {}
    for entry in entries:
        group = entry.get("group", "未分类")
        module = entry.get("module", "未分类")
        grouped.setdefault(group, {}).setdefault(module, []).append(entry)

    for group, modules in sorted(grouped.items()):
        parts.append(f"## {group}")
        parts.append("")
        for module, items in sorted(modules.items()):
            parts.append(f"### {module} ({len(items)} 个接口)")
            parts.append("")
            parts.append("| 方法 | 路径 | 说明 |")
            parts.append("|------|------|------|")
            for item in items:
                summary = item.get("enriched_summary", item.get("summary", ""))
                parts.append(f"| {item['method']} | `{item['path']}` | {summary} |")
            parts.append("")

    return "\n".join(parts)
