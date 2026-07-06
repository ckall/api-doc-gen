"""
Phase 2: 根据 api-manifest.json 生成带 metadata 的 Markdown 文件

用法:
    python gen_docs.py [--config config.yaml] [--manifest output/api-manifest.json]

输出:
    output/docs/{module}/{METHOD}_{path_normalized}.md
    output/docs/_overview.md
"""

import json
import os
import re
from datetime import date
from typing import Any

import yaml


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_manifest(manifest_path: str) -> list[dict]:
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_path_for_filename(method: str, path: str) -> str:
    """将 method + path 转为文件名友好格式"""
    # /admin/book/list → admin_book_list
    normalized = path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    # 去掉 : 冒号（gin 的路径参数格式）
    normalized = normalized.replace(":", "")
    return f"{method.upper()}_{normalized}"


def generate_metadata_yaml(entry: dict, config: dict) -> str:
    """生成 YAML frontmatter"""
    # 生成 tags：从 summary 和 path 中提取关键词
    tags = generate_tags(entry)

    # 生成 aliases：用户可能怎么问这个接口
    aliases = generate_aliases(entry)

    metadata = {
        "id": entry["id"],
        "project": config["project"],
        "system": config["system"],
        "service": config["service"],
        "domain": config["domain"],
        "module": entry["module"],
        "group": entry["group"],
        "method": entry["method"],
        "path": entry["path"],
        "summary": entry["summary"],
        "version": config["version"],
        "handler": entry.get("handler", ""),
        "handler_file": entry.get("handler_file", ""),
        "tags": tags,
        "aliases": aliases,
        "updated_at": date.today().isoformat(),
    }

    # 去掉空值
    metadata = {k: v for k, v in metadata.items() if v}

    lines = ["---"]
    for key, value in metadata.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        else:
            # 如果值包含特殊字符，加引号
            str_value = str(value)
            if any(c in str_value for c in ":#{}[]|>&*!%@`"):
                lines.append(f'{key}: "{str_value}"')
            else:
                lines.append(f"{key}: {str_value}")
    lines.append("---")
    return "\n".join(lines)


def generate_tags(entry: dict) -> list[str]:
    """从接口信息中自动生成语义标签"""
    tags = set()

    # 从 tag 名称
    if entry.get("tag"):
        tags.add(entry["tag"])

    # 从 path 中提取资源名
    path_parts = entry["path"].strip("/").split("/")
    # 跳过 admin/author 等前缀和路径参数
    for part in path_parts:
        if part in ("admin", "author", "x", "internal", "api", "v1", "v2"):
            continue
        if part.startswith("{") or part.startswith(":"):
            continue
        if len(part) > 1:
            tags.add(part)

    # 从 summary 中提取动词
    summary = entry.get("summary", "")
    action_map = {
        "列表": "查询",
        "查询": "查询",
        "详情": "查询",
        "添加": "创建",
        "创建": "创建",
        "新增": "创建",
        "编辑": "修改",
        "修改": "修改",
        "更新": "修改",
        "删除": "删除",
        "导出": "导出",
        "导入": "导入",
        "审核": "审核",
    }
    for keyword, action in action_map.items():
        if keyword in summary:
            tags.add(action)
            break

    return sorted(tags)


def generate_aliases(entry: dict) -> list[str]:
    """生成用户可能怎么问这个接口的别名"""
    aliases = []
    summary = entry.get("summary", "")
    path = entry["path"]

    if summary:
        aliases.append(f"{summary}接口")
        # 去掉"接口"后缀如果 summary 已经有了
        if not summary.endswith("接口"):
            aliases.append(summary)

    # 生成路径风格的别名
    # /admin/book/list → "admin book list"
    path_alias = path.strip("/").replace("/", " ").replace("{", "").replace("}", "")
    aliases.append(path_alias)

    return aliases[:4]  # 最多4个别名


def render_parameters_table(parameters: list[dict]) -> str:
    """渲染参数表格"""
    if not parameters:
        return "_无参数_\n"

    # 按 in 分组
    groups: dict[str, list[dict]] = {}
    for param in parameters:
        location = param.get("in", "body")
        if location not in groups:
            groups[location] = []
        groups[location].append(param)

    lines = []
    location_names = {
        "path": "路径参数",
        "query": "Query 参数",
        "header": "Header 参数",
        "body": "请求体字段",
    }

    for location, params in groups.items():
        if len(groups) > 1:
            lines.append(f"\n**{location_names.get(location, location)}**\n")

        lines.append("| 参数 | 类型 | 必填 | 说明 |")
        lines.append("|------|------|------|------|")
        for param in params:
            required = "是" if param.get("required") else "否"
            desc = param.get("description", "").replace("\n", " ").replace("|", "\\|")
            name = param.get("name", "")
            type_str = param.get("type", "")
            lines.append(f"| {name} | {type_str} | {required} | {desc} |")

    return "\n".join(lines) + "\n"


def render_response_table(fields: list[dict]) -> str:
    """渲染响应字段表格"""
    if not fields:
        return "_参考通用响应格式_\n"

    lines = ["| 字段 | 类型 | 说明 |", "|------|------|------|"]
    for field in fields:
        desc = field.get("description", "").replace("\n", " ").replace("|", "\\|")
        name = field.get("name", "")
        type_str = field.get("type", "")
        lines.append(f"| {name} | {type_str} | {desc} |")

    return "\n".join(lines) + "\n"


def render_api_doc(entry: dict, config: dict) -> str:
    """渲染单个接口的完整 Markdown 文档"""
    parts = []

    # 1. Frontmatter
    parts.append(generate_metadata_yaml(entry, config))
    parts.append("")

    # 2. 标题
    handler_name = entry.get("handler", "").split(".")[-1] if entry.get("handler") else ""
    title = handler_name if handler_name else f"{entry['method']} {entry['path']}"
    summary = entry.get("summary", "")
    parts.append(f"# {title}")
    parts.append("")
    if summary:
        parts.append(summary)
        parts.append("")

    # 3. 请求信息
    parts.append(f"## 请求")
    parts.append("")
    parts.append(f"```")
    parts.append(f"{entry['method']} {entry['path']}")
    parts.append(f"```")
    parts.append("")

    # 4. 参数
    parts.append("## 参数")
    parts.append("")
    parts.append(render_parameters_table(entry.get("parameters", [])))

    # 5. 响应
    parts.append("## 响应")
    parts.append("")
    parts.append(render_response_table(entry.get("response_fields", [])))

    # 6. 源码位置（供 AI 深入查看时参考）
    if entry.get("handler_file"):
        parts.append("## 源码")
        parts.append("")
        parts.append(f"- Handler: `{entry.get('handler', '')}` → `{entry['handler_file']}`")
        if entry.get("route_file"):
            parts.append(f"- 路由: `{entry['route_file']}`")
        parts.append("")

    return "\n".join(parts)


def render_overview(manifest: list[dict], config: dict) -> str:
    """生成总览文档"""
    parts = []

    # Frontmatter
    parts.append("---")
    parts.append(f"project: {config['project']}")
    parts.append(f"system: {config['system']}")
    parts.append(f"service: {config['service']}")
    parts.append(f"type: overview")
    parts.append(f"updated_at: {date.today().isoformat()}")
    parts.append("---")
    parts.append("")
    parts.append(f"# {config['system']} - API 接口总览")
    parts.append("")
    parts.append(f"服务: `{config['service']}`  ")
    parts.append(f"接口总数: {len(manifest)}")
    parts.append("")

    # 按 group → module 分组
    grouped: dict[str, dict[str, list[dict]]] = {}
    for entry in manifest:
        group = entry.get("group", "未分类")
        module = entry.get("module", "未分类")
        if group not in grouped:
            grouped[group] = {}
        if module not in grouped[group]:
            grouped[group][module] = []
        grouped[group][module].append(entry)

    for group, modules in sorted(grouped.items()):
        parts.append(f"## {group}")
        parts.append("")
        for module, entries in sorted(modules.items()):
            parts.append(f"### {module} ({len(entries)} 个接口)")
            parts.append("")
            parts.append("| 方法 | 路径 | 说明 |")
            parts.append("|------|------|------|")
            for entry in entries:
                parts.append(f"| {entry['method']} | `{entry['path']}` | {entry.get('summary', '')} |")
            parts.append("")

    return "\n".join(parts)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Phase 2: 生成 API 知识库文档")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--manifest", default="output/api-manifest.json", help="manifest 文件路径")
    args = parser.parse_args()

    # 切换工作目录到脚本所在目录
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    config = load_config(args.config)
    manifest = load_manifest(args.manifest)

    # 输出目录
    docs_dir = "output/docs"
    os.makedirs(docs_dir, exist_ok=True)

    # 生成每个接口的文档
    file_count = 0
    for entry in manifest:
        module_dir = os.path.join(docs_dir, entry["group"], entry["module"])
        os.makedirs(module_dir, exist_ok=True)

        filename = normalize_path_for_filename(entry["method"], entry["path"]) + ".md"
        file_path = os.path.join(module_dir, filename)

        content = render_api_doc(entry, config)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        file_count += 1

    # 生成总览
    overview_content = render_overview(manifest, config)
    overview_path = os.path.join(docs_dir, "_overview.md")
    with open(overview_path, "w", encoding="utf-8") as f:
        f.write(overview_content)

    print(f"✅ 文档生成完成")
    print(f"   生成 {file_count} 个接口文档")
    print(f"   总览: {overview_path}")
    print(f"   文档目录: {docs_dir}/")


if __name__ == "__main__":
    main()
