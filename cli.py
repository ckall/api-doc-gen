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

    # 交互式 or 参数式
    if args.project:
        project = args.project
    else:
        project = Prompt.ask("项目标识", default=Path(os.getcwd()).name)

    if args.source:
        source_root = args.source
    else:
        source_root = Prompt.ask("项目源码根目录", default=os.getcwd())

    if args.swagger:
        swagger_path = args.swagger
    else:
        # 自动探测
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

    system = args.system or Prompt.ask("系统名称（如：作家后台）", default=project)
    service = args.service or Prompt.ask("服务名（如：goc-authorplatform）", default=project)
    domain = args.domain or Prompt.ask("业务域（如：内容运营）", default="")

    # LLM 配置
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "")
    base_url = args.base_url or os.environ.get("OPENAI_BASE_URL", "")
    model = args.model or os.environ.get("OPENAI_MODEL", "claude-opus-4-8")

    if not api_key:
        api_key = Prompt.ask("OpenAI API Key", password=True)
    if not base_url:
        base_url = Prompt.ask("API Base URL（直连OpenAI留空）", default="")

    # 路由文件模式（自动探测）
    router_patterns = _detect_router_patterns(source_root)
    if not router_patterns:
        pattern_input = Prompt.ask("路由文件 glob 模式", default="router/*.go")
        router_patterns = [pattern_input]

    config = {
        "project": project,
        "system": system,
        "service": service,
        "domain": domain,
        "version": "v1",
        "source_root": source_root,
        "swagger_path": swagger_path,
        "router_patterns": router_patterns,
        "output_dir": OUTPUT_DIR,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "batch_size": 5,
        "concurrency": 1,
        "auto_approve": False,
        "max_tool_rounds": 4,
        "route_groups": {},
        "tag_module_map": {},
    }

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    console.print(f"\n[green]✅ 配置已生成: {CONFIG_FILE}[/green]")
    console.print(f"[dim]   可手动编辑 route_groups 和 tag_module_map 来优化模块分组[/dim]")
    console.print(f"\n下一步:")
    console.print(f"   api-doc-gen manifest   # 生成接口清单")
    console.print(f"   api-doc-gen run        # AI 增强生成文档")


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
        service=config.get("service", ""),
        domain=config.get("domain", ""),
        version=config.get("version", "v1"),
        source_root=config.get("source_root", "."),
        manifest_path=MANIFEST_FILE,
        output_dir=OUTPUT_DIR,
        model=os.environ.get("OPENAI_MODEL") or config.get("model", "claude-opus-4-8"),
        base_url=os.environ.get("OPENAI_BASE_URL") or config.get("base_url", ""),
        api_key=os.environ.get("OPENAI_API_KEY") or config.get("api_key", ""),
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
    parser = argparse.ArgumentParser(
        prog="api-doc-gen",
        description="从 Swagger + 源码生成 AI 知识库文档",
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # init
    p_init = subparsers.add_parser("init", help="初始化项目配置")
    p_init.add_argument("--project", type=str, help="项目标识")
    p_init.add_argument("--source", type=str, help="项目源码根目录")
    p_init.add_argument("--swagger", type=str, help="Swagger 文件路径")
    p_init.add_argument("--system", type=str, help="系统名称")
    p_init.add_argument("--service", type=str, help="服务名")
    p_init.add_argument("--domain", type=str, help="业务域")
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

    # status
    p_status = subparsers.add_parser("status", help="查看任务状态")

    # reset
    p_reset = subparsers.add_parser("reset", help="重置任务状态")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "init": cmd_init,
        "manifest": cmd_manifest,
        "run": cmd_run,
        "status": cmd_status,
        "reset": cmd_reset,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
