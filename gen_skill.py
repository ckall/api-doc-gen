#!/usr/bin/env python3
"""
gen_skill.py - 从 manifest + flow 文档 + tools.json 生成 SKILL.md

按流程组织 skill 内容，让 AI agent 知道：
- 这个 MCP server 能完成什么业务操作
- 什么场景下应该触发
- 每个流程的调用顺序是什么
"""

import json
import os
import re
from datetime import date
from pathlib import Path

import yaml


def generate_skill(config: dict, output_path: str, manifest: list, flows: list[dict], tools: list[dict]) -> str:
    """生成 SKILL.md 文件"""
    project = config.get("project", "")
    system = config.get("system", "")
    server_name = config.get("mcp", {}).get("server", {}).get("name", project)

    # 构建模块统计
    modules = _build_module_stats(manifest)

    # 构建触发关键词
    triggers = _build_trigger_keywords(manifest, flows)

    # 构建流程摘要
    flow_sections = _build_flow_sections(flows)

    # 构建非流程的独立操作（不在任何流程中的接口）
    flow_apis = set()
    for flow in flows:
        for step in flow.get("steps", []):
            api_key = f"{step.get('api_method', '')} {step.get('api_path', '')}"
            flow_apis.add(api_key)

    standalone_ops = _build_standalone_operations(manifest, flow_apis)

    # 渲染
    parts = []

    # 标题和简介
    desc = f"{system}的 API 操作：{', '.join(m['name'] for m in modules[:5])}"
    if len(modules) > 5:
        desc += f"等 {len(modules)} 个模块"
    parts.append(f'"{desc}"')
    parts.append("")

    parts.append(f"# {server_name} MCP Skill")
    parts.append("")
    parts.append(f"**项目**: {project}  ")
    parts.append(f"**系统**: {system}  ")
    parts.append(f"**接口数**: {len(manifest)}  ")
    parts.append(f"**操作流程**: {len(flows)} 个  ")
    parts.append("")

    # 触发场景
    parts.append("## 什么时候使用")
    parts.append("")
    parts.append("当用户需要：")
    parts.append("")
    for trigger in triggers:
        parts.append(f"- {trigger}")
    parts.append("")

    # 不适用场景
    parts.append("## 不负责")
    parts.append("")
    parts.append("- 前端 UI 渲染和交互逻辑")
    parts.append("- 不在本系统中的业务（需确认系统边界）")
    parts.append("")

    # 操作流程（核心）
    if flow_sections:
        parts.append("## 操作流程")
        parts.append("")
        for section in flow_sections:
            parts.append(f"### {section['title']}")
            parts.append("")
            parts.append(f"**角色**: {section['role']} | **难度**: {section['difficulty']}")
            parts.append("")
            if section.get("description"):
                parts.append(f"{section['description']}")
                parts.append("")
            parts.append("**步骤**:")
            parts.append("")
            for step in section["steps"]:
                parts.append(f"{step['order']}. {step['title']} → `{step['api_method']} {step['api_path']}`")
            parts.append("")
            if section.get("cautions"):
                parts.append(f"⚠️ {'; '.join(section['cautions'][:3])}")
                parts.append("")

    # 独立操作（查询类、不成流程的）
    if standalone_ops:
        parts.append("## 独立操作")
        parts.append("")
        parts.append("以下接口可独立调用，不属于特定流程：")
        parts.append("")
        for module_name, ops in standalone_ops.items():
            parts.append(f"### {module_name}")
            parts.append("")
            for op in ops[:10]:  # 每模块最多列 10 个
                parts.append(f"- `{op['method']} {op['path']}` — {op['summary']}")
            if len(ops) > 10:
                parts.append(f"- ... 共 {len(ops)} 个接口")
            parts.append("")

    # 模块概览
    parts.append("## 模块概览")
    parts.append("")
    parts.append("| 模块 | 接口数 | 说明 |")
    parts.append("|------|--------|------|")
    for m in modules:
        parts.append(f"| {m['name']} | {m['count']} | {m['summary']} |")
    parts.append("")

    # 认证说明
    parts.append("## 认证")
    parts.append("")
    auth_type = config.get("mcp", {}).get("auth", {}).get("type", "bearer")
    parts.append(f"认证方式: **{auth_type}**  ")
    parts.append("启动时通过 `--token` 参数或环境变量 `API_TOKEN` 传入。")
    parts.append("")

    content = "\n".join(parts)

    # 写文件
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    return output_path


def _build_module_stats(manifest: list) -> list[dict]:
    """统计各模块信息"""
    module_map: dict[str, list] = {}
    for entry in manifest:
        module = entry.get("module", "未分类")
        if module not in module_map:
            module_map[module] = []
        module_map[module].append(entry)

    modules = []
    for name, entries in sorted(module_map.items(), key=lambda x: -len(x[1])):
        # 用前几个接口的 summary 拼成模块说明
        summaries = [e.get("summary", "") for e in entries[:3] if e.get("summary")]
        summary = "、".join(summaries) if summaries else ""
        modules.append({
            "name": name,
            "count": len(entries),
            "summary": summary,
        })

    return modules


def _build_trigger_keywords(manifest: list, flows: list[dict]) -> list[str]:
    """构建触发场景列表"""
    triggers = []

    # 从流程标题提取
    for flow in flows:
        title = flow.get("title", "")
        if title:
            triggers.append(title)

    # 从模块名提取补充
    modules = set()
    for entry in manifest:
        module = entry.get("module", "")
        if module and module != "未分类":
            modules.add(module)

    # 补充一些通用场景描述
    if modules:
        module_str = "、".join(list(modules)[:6])
        triggers.append(f"查询或管理{module_str}相关数据")

    return triggers


def _build_flow_sections(flows: list[dict]) -> list[dict]:
    """从 flow 数据构建流程摘要"""
    sections = []
    for flow in flows:
        steps = []
        for step in flow.get("steps", []):
            steps.append({
                "order": step.get("order", 0),
                "title": step.get("title", ""),
                "api_method": step.get("api_method", ""),
                "api_path": step.get("api_path", ""),
            })

        sections.append({
            "title": flow.get("title", flow.get("flow_name", "")),
            "role": flow.get("role", ""),
            "difficulty": flow.get("difficulty", "中等"),
            "description": flow.get("description", ""),
            "steps": steps,
            "cautions": flow.get("cautions", []),
        })

    return sections


def _build_standalone_operations(manifest: list, flow_apis: set) -> dict[str, list]:
    """找出不在任何流程中的独立接口，按模块分组"""
    standalone: dict[str, list] = {}

    for entry in manifest:
        api_key = f"{entry['method']} {entry['path']}"
        if api_key in flow_apis:
            continue

        module = entry.get("module", "未分类")
        if module not in standalone:
            standalone[module] = []
        standalone[module].append({
            "method": entry["method"],
            "path": entry["path"],
            "summary": entry.get("summary", ""),
        })

    return standalone


def load_flows_from_docs(flows_dir: str) -> list[dict]:
    """从 _flows/ 目录读取流程文档，解析 frontmatter 和步骤"""
    flows = []

    if not os.path.isdir(flows_dir):
        return flows

    for root, _dirs, files in os.walk(flows_dir):
        for filename in files:
            if not filename.endswith(".md") or filename.startswith("_"):
                continue

            filepath = os.path.join(root, filename)
            flow = _parse_flow_doc(filepath)
            if flow:
                flows.append(flow)

    return flows


def _parse_flow_doc(filepath: str) -> dict | None:
    """解析单个 flow 文档"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None

    # 解析 frontmatter
    match = re.match(r"\s*---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        return None

    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None

    if not meta or meta.get("type") != "flow":
        return None

    # 解析步骤
    steps = []
    step_pattern = re.compile(
        r"### 第 (\d+) 步：(.+?)\n.*?"
        r"\*\*调用接口\*\*：`(\w+) (.+?)`",
        re.DOTALL,
    )
    for m in step_pattern.finditer(content):
        steps.append({
            "order": int(m.group(1)),
            "title": m.group(2).strip(),
            "api_method": m.group(3),
            "api_path": m.group(4),
        })

    # 解析注意事项
    cautions = []
    caution_match = re.search(r"## 注意事项\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if caution_match:
        for line in caution_match.group(1).strip().split("\n"):
            line = line.strip().lstrip("- ")
            if line:
                cautions.append(line)

    # 提取描述（标题后第一段）
    desc = ""
    desc_match = re.search(r"^# .+\n\n(.+?)(?=\n\n|\n## )", content, re.MULTILINE)
    if desc_match:
        desc = desc_match.group(1).strip()

    return {
        "flow_name": meta.get("flow_name", ""),
        "title": meta.get("title", "") or _extract_title(content),
        "category": meta.get("category", ""),
        "role": meta.get("role", ""),
        "difficulty": meta.get("difficulty", "中等"),
        "description": desc,
        "steps": steps,
        "cautions": cautions,
        "tags": meta.get("tags", []),
        "aliases": meta.get("aliases", []),
    }


def _extract_title(content: str) -> str:
    """从 markdown 内容中提取 # 标题"""
    m = re.search(r"^# (.+)$", content, re.MULTILINE)
    return m.group(1).strip() if m else ""
