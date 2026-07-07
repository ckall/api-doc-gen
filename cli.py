#!/usr/bin/env python3
"""
api-doc-gen - 从 Swagger + 源码生成 AI 知识库文档的 CLI 工具

用法:
    # 初始化项目配置（交互式）
    api-doc-gen init

    # 初始化（非交互式）
    api-doc-gen init --project myapp --source /path/to/project --swagger /path/to/swagger.json

    # 生成 manifest
    api-doc-gen manifest

    # 运行 AI 增强 pipeline
    api-doc-gen run
    api-doc-gen run --path "/admin/book/*"
    api-doc-gen run --module "书籍管理"
    api-doc-gen run --changed
    api-doc-gen run --retry-failed

    # 查看任务状态
    api-doc-gen status

    # 重置
    api-doc-gen reset
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

console = Console()

# 工作目录：当前目录下的 .api-doc-gen/
WORK_DIR = os.path.join(os.getcwd(), ".api-doc-gen")
CONFIG_FILE = os.path.join(WORK_DIR, "config.yaml")
MANIFEST_FILE = os.path.join(WORK_DIR, "api-manifest.json")
TASK_FILE = os.path.join(WORK_DIR, "task_state.json")
OUTPUT_DIR = os.path.join(WORK_DIR, "docs")


# ============================================================
# init 命令
# ============================================================

def cmd_init(args):
    """初始化项目配置"""
    import yaml

    os.makedirs(WORK_DIR, exist_ok=True)

    if os.path.isfile(CONFIG_FILE) and not args.force:
        if not Confirm.ask(f"[yellow]{CONFIG_FILE} 已存在，覆盖？[/yellow]", default=False):
            return

    # --- 项目基本信息 ---
    if args.project:
        project = args.project
    else:
        project = Prompt.ask("项目标识（唯一标识，用于区分不同项目）", default=Path(os.getcwd()).name)

    if args.source:
        source_root = args.source
    else:
        source_root = Prompt.ask("项目源码根目录", default=os.getcwd())

    # --- Swagger 文件 ---
    if args.swagger:
        swagger_path = args.swagger
    else:
        swagger_candidates = _find_swagger_files(source_root)
        if swagger_candidates:
            console.print(f"[green]发现 swagger 文件:[/green]")
            for i, p in enumerate(swagger_candidates):
                console.print(f"  {i + 1}. {p}")
            choice = Prompt.ask("选择序号", default="1")
            try:
                swagger_path = swagger_candidates[int(choice) - 1]
            except (ValueError, IndexError):
                swagger_path = swagger_candidates[0]
        else:
            swagger_path = Prompt.ask("Swagger 文件路径")

    # --- LLM 配置 ---
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "")
    base_url = args.base_url or os.environ.get("OPENAI_BASE_URL", "")
    model = args.model or os.environ.get("OPENAI_MODEL", "claude-opus-4-8")

    if not api_key:
        api_key = Prompt.ask("OpenAI API Key", password=True)
    if not base_url:
        base_url = Prompt.ask("API Base URL（直连 OpenAI 留空）", default="")

    # --- 路由文件（自动探测）---
    router_patterns = _detect_router_patterns(source_root)
    if not router_patterns:
        pattern_input = Prompt.ask("路由文件 glob 模式", default="router/*.go")
        router_patterns = [pattern_input]

    # --- 项目描述和别名（用于知识库检索）---
    console.print("\n[cyan]以下信息用于知识库检索，AI 会自动为每个接口生成别名，这里只填项目级别的：[/cyan]")
    system = args.system or Prompt.ask("项目名称（一句话说明这是什么系统）", default=project)

    # 项目别名：用户可能怎么称呼这个项目
    aliases_input = Prompt.ask(
        "项目别名（用户可能怎么称呼，逗号分隔，可留空）",
        default="",
    )
    project_aliases = [a.strip() for a in aliases_input.split(",") if a.strip()]

    config = {
        "config_version": 2,
        "project": project,
        "system": system,
        "project_aliases": project_aliases,
        "version": "v1",
        "source_root": source_root,
        # 入口路径配置（各类型的代码位置）
        "entrypoints": {
            "http": {
                "swagger_path": swagger_path,
                "router_patterns": router_patterns,
            },
            "cron": {
                "patterns": _detect_cron_patterns(source_root),
            },
            "mq": {
                "patterns": _detect_mq_patterns(source_root),
            },
        },
        "output_dir": OUTPUT_DIR,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "batch_size": 5,
        "concurrency": 1,
        "auto_approve": False,
        "max_tool_rounds": 4,
        # 以下按需手动编辑
        "route_groups": {},
        "tag_module_map": {},
    }

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # 复制默认模板到 .api-doc-gen/templates/
    _init_templates()

    console.print(f"\n[green]✅ 配置已生成: {CONFIG_FILE}[/green]")
    console.print(f"[dim]   可手动编辑 route_groups（路由分组）和 tag_module_map（模块命名）[/dim]")
    console.print(f"[dim]   可自定义模板: .api-doc-gen/templates/*.j2（变量说明见 VARIABLES.md）[/dim]")
    console.print(f"\n下一步:")
    console.print(f"   api-doc-gen manifest   # 生成接口清单")
    console.print(f"   api-doc-gen gen        # 快速生成文档（不用AI）")
    console.print(f"   api-doc-gen run        # AI 增强生成文档")


def _init_templates():
    """把默认模板复制到 .api-doc-gen/templates/，用户可自定义修改"""
    import shutil

    templates_dir = os.path.join(WORK_DIR, "templates")
    os.makedirs(templates_dir, exist_ok=True)

    # 默认模板来源
    script_dir = os.path.dirname(os.path.abspath(__file__))
    defaults_dir = os.path.join(script_dir, "templates", "defaults")

    if not os.path.isdir(defaults_dir):
        return

    for filename in os.listdir(defaults_dir):
        src = os.path.join(defaults_dir, filename)
        dst = os.path.join(templates_dir, filename)
        # 不覆盖用户已修改的模板
        if not os.path.exists(dst):
            shutil.copy2(src, dst)
            console.print(f"[dim]   模板: {dst}[/dim]")


def _find_swagger_files(source_root: str) -> list[str]:
    """在项目中探测 swagger 文件"""
    candidates = []
    for pattern in ["**/swagger.json", "**/swagger.yaml", "**/swagger.yml",
                    "**/openapi.json", "**/openapi.yaml", "**/openapi.yml",
                    "docs/swagger.*", "api/swagger.*"]:
        for p in Path(source_root).glob(pattern):
            # 排除 node_modules、vendor 等
            parts = p.parts
            skip = {"node_modules", "vendor", ".git", "dist", "build"}
            if not any(s in parts for s in skip):
                candidates.append(str(p))
    return candidates[:5]


def _detect_router_patterns(source_root: str) -> list[str]:
    """自动探测路由文件模式"""
    patterns = []
    checks = [
        ("router/*.go", "Go Gin"),
        ("routes/*.go", "Go"),
        ("routes/*.ts", "TypeScript"),
        ("routes/*.js", "Node.js"),
        ("src/routes/*.ts", "NestJS/Express"),
        ("app/routes/*.py", "Python"),
        ("src/main/java/**/controller/*.java", "Java Spring"),
    ]
    for pattern, lang in checks:
        found = list(Path(source_root).glob(pattern))
        if found:
            patterns.append(pattern)
            console.print(f"[green]   探测到路由文件: {pattern} ({lang})[/green]")
    return patterns


def _detect_cron_patterns(source_root: str) -> list[str]:
    """自动探测定时任务文件"""
    patterns = []
    checks = [
        ("task/*.go", "Go cron"),
        ("cron/*.go", "Go cron"),
        ("jobs/*.go", "Go jobs"),
        ("scheduler/*.go", "Go scheduler"),
        ("cron/*.py", "Python cron"),
        ("tasks/*.py", "Python/Celery"),
        ("src/cron/*.ts", "TypeScript cron"),
        ("src/jobs/*.ts", "TypeScript jobs"),
    ]
    for pattern, _ in checks:
        found = list(Path(source_root).glob(pattern))
        if found:
            patterns.append(pattern)
            console.print(f"[green]   探测到定时任务: {pattern}[/green]")
    return patterns


def _detect_mq_patterns(source_root: str) -> list[str]:
    """自动探测 MQ 消费者文件"""
    patterns = []
    checks = [
        ("consumer/*.go", "Go consumer"),
        ("consumers/*.go", "Go consumers"),
        ("subscriber/*.go", "Go subscriber"),
        ("mq/*.go", "Go MQ"),
        ("pkg/mq/*.go", "Go MQ pkg"),
        ("pkg/queueredis/*.go", "Go Redis Queue"),
        ("consumer/*.py", "Python consumer"),
        ("consumers/*.py", "Python consumers"),
        ("src/consumer/*.ts", "TypeScript consumer"),
        ("src/subscribers/*.ts", "TypeScript subscriber"),
    ]
    for pattern, _ in checks:
        found = list(Path(source_root).glob(pattern))
        if found:
            patterns.append(pattern)
            console.print(f"[green]   探测到MQ消费者: {pattern}[/green]")
    return patterns


# ============================================================
# manifest 命令
# ============================================================

def cmd_manifest(args):
    """生成 api-manifest.json"""
    config = _load_config()
    if not config:
        return

    # 复用 gen_manifest 的逻辑
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, script_dir)
    from gen_manifest import build_manifest

    manifest = build_manifest(config)

    os.makedirs(WORK_DIR, exist_ok=True)
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    console.print(f"[green]✅ 生成完成: {MANIFEST_FILE}[/green]")
    console.print(f"   共解析 {len(manifest)} 个接口")

    # 模块统计
    module_count: dict[str, int] = {}
    for entry in manifest:
        key = f"{entry['group']} / {entry['module']}"
        module_count[key] = module_count.get(key, 0) + 1
    console.print("\n[cyan]模块统计:[/cyan]")
    for module, count in sorted(module_count.items()):
        console.print(f"   {module}: {count} 个接口")


# ============================================================
# gen 命令（不用 AI）
# ============================================================

def cmd_gen(args):
    """直接从 manifest 生成骨架文档（不用 AI，速度快）"""
    config = _load_config()
    if not config:
        return

    if not os.path.isfile(MANIFEST_FILE):
        console.print("[red]❌ 未找到 manifest，请先运行: api-doc-gen manifest[/red]")
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, script_dir)
    from gen_docs import load_manifest, render_api_doc, render_overview, normalize_path_for_filename

    manifest = load_manifest(MANIFEST_FILE)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    file_count = 0
    for entry in manifest:
        module_dir = os.path.join(OUTPUT_DIR, entry.get("group", "未分类"), entry.get("module", "未分类"))
        os.makedirs(module_dir, exist_ok=True)

        filename = normalize_path_for_filename(entry["method"], entry["path"]) + ".md"
        file_path = os.path.join(module_dir, filename)

        content = render_api_doc(entry, config)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        file_count += 1

    # overview
    overview_content = render_overview(manifest, config)
    overview_path = os.path.join(OUTPUT_DIR, "_overview.md")
    with open(overview_path, "w", encoding="utf-8") as f:
        f.write(overview_content)

    console.print(f"[green]✅ 生成完成（无 AI 模式）[/green]")
    console.print(f"   {file_count} 个接口文档 + _overview.md")
    console.print(f"   输出目录: {OUTPUT_DIR}/")


# ============================================================
# run 命令
# ============================================================

def cmd_run(args):
    """运行 AI 增强 pipeline"""
    config = _load_config()
    if not config:
        return

    if not os.path.isfile(MANIFEST_FILE):
        console.print("[red]❌ 未找到 manifest，请先运行: api-doc-gen manifest[/red]")
        return

    # 覆盖 config 中的路径
    config["manifest_file"] = MANIFEST_FILE
    config["docs_dir"] = OUTPUT_DIR

    if not config.get("api_key"):
        console.print("[red]❌ 未配置 API Key，请运行 api-doc-gen init 或设置 OPENAI_API_KEY[/red]")
        return

    # 加载 manifest 并筛选
    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        full_manifest = json.load(f)

    filtered_manifest = _filter_manifest(full_manifest, args)
    if not filtered_manifest:
        return

    # 构建 pipeline config
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, script_dir)
    from dataclasses import asdict
    from pipeline import build_graph, PipelineConfig

    pipeline_config = PipelineConfig(
        project=config.get("project", ""),
        system=config.get("system", ""),
        service=config.get("service", "") or config.get("project", ""),
        version=config.get("version", "v1"),
        source_root=config.get("source_root", "."),
        manifest_path=MANIFEST_FILE,
        output_dir=OUTPUT_DIR,
        model=config.get("model") or os.environ.get("OPENAI_MODEL") or "claude-opus-4-8",
        base_url=config.get("base_url") or os.environ.get("OPENAI_BASE_URL") or "",
        api_key=config.get("api_key") or os.environ.get("OPENAI_API_KEY") or "",
        batch_size=args.batch or config.get("batch_size", 5),
        auto_approve=args.auto,
        max_tool_rounds=config.get("max_tool_rounds", 4),
        concurrency=args.concurrency or config.get("concurrency", 1),
    )

    # 显示配置
    console.print(Panel(
        f"项目: {pipeline_config.project}\n"
        f"模型: {pipeline_config.model}\n"
        f"源码: {pipeline_config.source_root}\n"
        f"待处理: {len(filtered_manifest)} / {len(full_manifest)} 个接口\n"
        f"批次: {pipeline_config.batch_size} | 并发: {pipeline_config.concurrency}\n"
        f"自动模式: {'是' if pipeline_config.auto_approve else '否'}",
        title="🚀 API 文档生成",
        border_style="cyan",
    ))

    if not args.auto:
        console.print("[dim]   a=通过, d=查看详情, s=跳过, q=中止[/dim]\n")

    # 运行
    graph = build_graph()
    app = graph.compile()

    initial_state = {
        "config": asdict(pipeline_config),
        "manifest": filtered_manifest,
        "current_batch": [],
        "current_batch_index": 0,
        "enriched_entries": [],
        "pending_review": [],
        "approved_entries": [],
        "generated_files": [],
        "human_decision": "",
        "human_feedback": "",
        "errors": [],
        "status": "初始化",
    }

    try:
        final_state = app.invoke(initial_state)
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️ 用户中断[/yellow]")
        sys.exit(1)

    # 更新任务状态
    _update_task_state(final_state, full_manifest)

    # 结果
    generated = final_state.get("generated_files", [])
    errors = final_state.get("errors", [])
    approved_count = len(final_state.get("approved_entries", []))

    console.print(Panel(
        f"✅ 已确认: {approved_count}\n"
        f"📄 已生成: {len(generated)} 个文件\n"
        f"⚠️  错误: {len(errors)}",
        title="📊 结果",
        border_style="green" if not errors else "yellow",
    ))

    if errors:
        for err in errors[:10]:
            console.print(f"  • {err}")

    if generated:
        console.print(f"\n[green]输出目录: {OUTPUT_DIR}/[/green]")


# ============================================================
# flow 命令
# ============================================================

def cmd_flow(args):
    """AI 分析接口文档，生成用户操作流程指南"""
    from gen_flows import run_flow_generation
    run_flow_generation(args, auto=getattr(args, "auto", False))


# ============================================================
# status / reset 命令
# ============================================================

def cmd_status(args):
    """查看任务状态"""
    state = _load_task_state()
    if not state.get("last_run"):
        console.print("[dim]还没有运行记录。先运行 api-doc-gen run[/dim]")
        return

    table = Table(title="任务状态")
    table.add_column("项目", style="cyan")
    table.add_column("数量", style="green")
    table.add_row("总接口数", str(state["total"]))
    table.add_row("已完成", str(len(state["completed"])))
    table.add_row("失败", str(len(state["failed"])))
    table.add_row("跳过", str(len(state["skipped"])))
    remaining = state["total"] - len(state["completed"]) - len(state["failed"]) - len(state["skipped"])
    table.add_row("未处理", str(remaining))
    table.add_row("上次运行", state["last_run"])
    console.print(table)

    if state["failed"]:
        console.print(f"\n[yellow]失败的接口:[/yellow]")
        for api_id in state["failed"][:10]:
            console.print(f"  • {api_id}")


def cmd_reset(args):
    """重置任务状态"""
    if os.path.isfile(TASK_FILE):
        os.remove(TASK_FILE)
    console.print("[green]✅ 任务状态已重置[/green]")


# ============================================================
# mcp 命令
# ============================================================

def cmd_mcp(args):
    """将 manifest 转换为 MCP server"""
    config = _load_config()
    if not config:
        return

    # 检查 manifest
    if not os.path.isfile(MANIFEST_FILE):
        console.print("[red]❌ 未找到 manifest，请先运行: api-doc-gen manifest[/red]")
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, script_dir)
    from gen_mcp import (
        build_tools_from_manifest,
        generate_mcp_server,
        generate_schema_only,
    )

    # CLI 参数覆盖 config
    mcp_config = config.setdefault("mcp", {})
    server_conf = mcp_config.setdefault("server", {})
    filter_conf = mcp_config.setdefault("filter", {})
    output_conf = mcp_config.setdefault("output", {})

    if args.base_url:
        server_conf["base_url"] = args.base_url
    if args.name:
        server_conf["name"] = args.name
    if args.module:
        filter_conf["modules"] = [m.strip() for m in args.module.split(",")]
    if args.path:
        filter_conf["paths"] = [p.strip() for p in args.path.split(",")]
    if args.transport:
        output_conf["transport"] = args.transport

    # 确定输出路径
    output_path = args.output or output_conf.get("path", os.path.join(WORK_DIR, "mcp-server"))

    # 确定输出模式
    mode = args.mode or output_conf.get("mode", "server")

    # 加载 manifest
    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # 构建 tool 列表
    tools = build_tools_from_manifest(manifest, config)
    console.print(f"[cyan]从 manifest 构建 MCP tools...[/cyan]")

    if not tools:
        console.print("[yellow]⚠️  未找到符合过滤条件的接口[/yellow]")
        return

    console.print(f"   共 {len(tools)} 个 tool")

    # 生成输出
    if mode == "schema":
        # 只输出 tools.json
        schema_path = output_path if output_path.endswith(".json") else os.path.join(output_path, "tools.json")
        result_path = generate_schema_only(tools, schema_path)
        console.print(f"[green]✅ 生成完成: {result_path}[/green]")
    else:
        # 生成完整 MCP server
        server_file, tools_file = generate_mcp_server(tools, config, output_path)
        console.print(f"[green]✅ MCP Server 生成完成[/green]")
        console.print(f"   server.py:     {server_file}")
        console.print(f"   tools.json:    {tools_file}")
        console.print(f"   requirements:  {os.path.join(output_path, 'requirements.txt')}")
        console.print()
        console.print("[cyan]运行方式:[/cyan]")
        console.print(f"   cd {output_path}")
        console.print(f"   pip install -r requirements.txt")
        console.print(f"   python server.py")


# ============================================================
# skill 命令
# ============================================================

def cmd_skill(args):
    """生成 MCP Server 的 SKILL.md 使用说明"""
    config = _load_config()
    if not config:
        return

    # 检查 manifest
    if not os.path.isfile(MANIFEST_FILE):
        console.print("[red]❌ 未找到 manifest，请先运行: api-doc-gen manifest[/red]")
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, script_dir)
    from gen_skill import generate_skill, load_flows_from_docs

    # 加载 manifest
    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # 加载 flow 文档
    flows_dir = os.path.join(WORK_DIR, "docs", "_flows")
    flows = load_flows_from_docs(flows_dir)

    # 加载 tools.json（如果有）
    tools_json_path = os.path.join(WORK_DIR, "mcp-server", "tools.json")
    tools = []
    if os.path.isfile(tools_json_path):
        with open(tools_json_path, "r", encoding="utf-8") as f:
            tools = json.load(f)

    # 确定输出路径
    output_path = args.output or os.path.join(WORK_DIR, "mcp-server", "SKILL.md")

    console.print(f"[cyan]生成 SKILL.md...[/cyan]")
    console.print(f"   manifest: {len(manifest)} 个接口")
    console.print(f"   流程文档: {len(flows)} 个")
    console.print(f"   tools: {len(tools)} 个")

    result_path = generate_skill(config, output_path, manifest, flows, tools)

    console.print(f"\n[green]✅ SKILL.md 生成完成: {result_path}[/green]")


# ============================================================
# 工具函数
# ============================================================

def _load_config() -> dict | None:
    """加载配置"""
    import yaml
    if not os.path.isfile(CONFIG_FILE):
        console.print(f"[red]❌ 未找到配置文件，请先运行: api-doc-gen init[/red]")
        return None
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_task_state() -> dict:
    if os.path.isfile(TASK_FILE):
        with open(TASK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_run": None, "total": 0, "completed": [], "failed": [], "skipped": [], "history": []}


def _save_task_state(state: dict):
    os.makedirs(os.path.dirname(TASK_FILE), exist_ok=True)
    with open(TASK_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _update_task_state(final_state: dict, full_manifest: list):
    task_state = _load_task_state()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    task_state["last_run"] = now
    task_state["total"] = len(full_manifest)

    approved = final_state.get("approved_entries", [])
    approved_ids = [e["id"] for e in approved]
    task_state["completed"] = list(set(task_state["completed"] + approved_ids))

    errors = final_state.get("errors", [])
    failed_ids = []
    for err in errors:
        match = re.search(r'\[([^\]]+::[^\]]+)\]', err)
        if match:
            failed_ids.append(match.group(1))
    task_state["failed"] = list(set(task_state.get("failed", []) + failed_ids) - set(approved_ids))

    task_state["history"].append({"time": now, "action": "run", "count": len(approved_ids), "errors": len(failed_ids)})
    task_state["history"] = task_state["history"][-20:]

    _save_task_state(task_state)


def _filter_manifest(manifest: list[dict], args) -> list[dict]:
    """根据参数筛选接口"""
    task_state = _load_task_state()

    if getattr(args, "retry_failed", False):
        failed_ids = set(task_state.get("failed", []))
        if not failed_ids:
            console.print("[green]没有失败的接口[/green]")
            return []
        return [e for e in manifest if e["id"] in failed_ids]

    if getattr(args, "changed", False):
        completed_ids = set(task_state.get("completed", []))
        filtered = [e for e in manifest if e["id"] not in completed_ids]
        if not filtered:
            console.print("[green]所有接口都已处理[/green]")
            return []
        console.print(f"[cyan]{len(filtered)} 个未处理的接口[/cyan]")
        return filtered

    if getattr(args, "path", None):
        patterns = [p.strip() for p in args.path.split(",")]
        filtered = []
        for entry in manifest:
            for pattern in patterns:
                if pattern.endswith("*"):
                    if entry["path"].startswith(pattern[:-1]):
                        filtered.append(entry)
                        break
                elif entry["path"] == pattern:
                    filtered.append(entry)
                    break
        console.print(f"[cyan]路径匹配: {len(filtered)} 个接口[/cyan]")
        return filtered

    if getattr(args, "module", None):
        modules = [m.strip() for m in args.module.split(",")]
        filtered = [e for e in manifest if e.get("module") in modules]
        console.print(f"[cyan]模块匹配: {len(filtered)} 个接口[/cyan]")
        return filtered

    if getattr(args, "handler", None):
        handlers = [h.strip() for h in args.handler.split(",")]
        filtered = [e for e in manifest if e.get("handler") in handlers]
        console.print(f"[cyan]Handler 匹配: {len(filtered)} 个接口[/cyan]")
        return filtered

    if getattr(args, "group", None):
        groups = [g.strip() for g in args.group.split(",")]
        filtered = [e for e in manifest if e.get("group") in groups]
        console.print(f"[cyan]分组匹配: {len(filtered)} 个接口[/cyan]")
        return filtered

    return manifest


# ============================================================
# 主入口
# ============================================================

def main():
    from importlib.metadata import version as pkg_version
    try:
        __version__ = pkg_version("api-doc-gen")
    except Exception:
        __version__ = "dev"

    parser = argparse.ArgumentParser(
        prog="api-doc-gen",
        description="从 Swagger + 源码生成 AI 知识库文档",
    )
    parser.add_argument("--version", "-V", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # init
    p_init = subparsers.add_parser("init", help="初始化项目配置")
    p_init.add_argument("--project", type=str, help="项目标识")
    p_init.add_argument("--source", type=str, help="项目源码根目录")
    p_init.add_argument("--swagger", type=str, help="Swagger 文件路径")
    p_init.add_argument("--system", type=str, help="项目名称/描述")
    p_init.add_argument("--model", type=str, help="LLM 模型名")
    p_init.add_argument("--base-url", type=str, help="API Base URL")
    p_init.add_argument("--api-key", type=str, help="API Key")
    p_init.add_argument("--force", action="store_true", help="覆盖已有配置")

    # manifest
    p_manifest = subparsers.add_parser("manifest", help="生成接口清单 (api-manifest.json)")

    # run
    p_run = subparsers.add_parser("run", help="运行 AI 增强 pipeline")
    p_run.add_argument("--auto", action="store_true", help="跳过人工确认")
    p_run.add_argument("--batch", type=int, default=0, help="批次大小")
    p_run.add_argument("--concurrency", "-c", type=int, default=0, help="并发数")
    p_run.add_argument("--path", type=str, help="按路径筛选（支持通配符，逗号分隔）")
    p_run.add_argument("--module", type=str, help="按模块筛选（逗号分隔）")
    p_run.add_argument("--handler", type=str, help="按 handler 筛选（逗号分隔）")
    p_run.add_argument("--group", type=str, help="按分组筛选（逗号分隔）")
    p_run.add_argument("--changed", action="store_true", help="只处理未处理过的")
    p_run.add_argument("--retry-failed", action="store_true", help="重跑失败的")

    # gen（不用 AI，直接从 manifest 生成骨架文档）
    p_gen = subparsers.add_parser("gen", help="直接生成文档（不用 AI，只用 swagger 信息）")

    # flow（AI 分析接口关系，生成操作流程文档）
    p_flow = subparsers.add_parser("flow", help="AI 分析接口文档，生成用户操作流程指南")
    p_flow.add_argument("--auto", action="store_true", help="跳过人工确认")
    p_flow.add_argument("--path", type=str, help="增量：只更新涉及这些路径的流程（逗号分隔，支持通配符）")
    p_flow.add_argument("--module", type=str, help="增量：只更新涉及这些模块的流程（逗号分隔）")
    p_flow.add_argument("--force", action="store_true", help="强制重新识别流程列表（忽略已有的 flow-manifest.json）")

    # status
    p_status = subparsers.add_parser("status", help="查看任务状态")

    # reset
    p_reset = subparsers.add_parser("reset", help="重置任务状态")

    # mcp
    p_mcp = subparsers.add_parser("mcp", help="将 Swagger/manifest 转换为 MCP Server")
    p_mcp.add_argument("--base-url", type=str, help="目标 API 基地址")
    p_mcp.add_argument("--name", type=str, help="MCP Server 名称")
    p_mcp.add_argument("--module", type=str, help="按模块筛选（逗号分隔）")
    p_mcp.add_argument("--path", type=str, help="按路径筛选（支持通配符，逗号分隔）")
    p_mcp.add_argument("--transport", type=str, choices=["stdio", "sse"], help="传输方式")
    p_mcp.add_argument("--mode", type=str, choices=["server", "schema"], help="输出模式: server(完整MCP服务) / schema(仅tool定义)")
    p_mcp.add_argument("--output", "-o", type=str, help="输出目录/文件路径")

    # skill
    p_skill = subparsers.add_parser("skill", help="生成 MCP Server 的 SKILL.md 使用说明")
    p_skill.add_argument("--output", "-o", type=str, help="输出文件路径（默认: mcp-server/SKILL.md）")
    p_skill.add_argument("--ai", action="store_true", help="使用 AI 增强触发场景描述")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "init": cmd_init,
        "manifest": cmd_manifest,
        "gen": cmd_gen,
        "run": cmd_run,
        "flow": cmd_flow,
        "status": cmd_status,
        "reset": cmd_reset,
        "mcp": cmd_mcp,
        "skill": cmd_skill,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
