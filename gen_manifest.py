"""
Phase 1: 解析 swagger.json + 路由文件，生成 api-manifest.json

用法:
    python gen_manifest.py [--config config.yaml]

输出:
    output/api-manifest.json - 接口清单，供 Phase 2 文档生成使用
"""

import json
import re
import os
import glob as glob_mod
from pathlib import Path
from typing import Any

import yaml


def load_config(config_path: str = "config.yaml") -> dict:
    """加载配置文件"""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_swagger(swagger_path: str) -> dict:
    """加载 swagger 文件"""
    with open(swagger_path, "r", encoding="utf-8") as f:
        if swagger_path.endswith(".yaml") or swagger_path.endswith(".yml"):
            return yaml.safe_load(f)
        return json.load(f)


def resolve_ref(swagger: dict, ref: str) -> dict:
    """解析 $ref 引用，返回实际的 schema 定义"""
    if not ref.startswith("#/"):
        return {}
    parts = ref.lstrip("#/").split("/")
    obj = swagger
    for part in parts:
        obj = obj.get(part, {})
    return obj


def flatten_schema(swagger: dict, schema: dict, prefix: str = "") -> list[dict]:
    """
    展平 schema 为字段列表。
    处理 $ref、allOf、嵌套 object、array 等情况。
    """
    if not schema:
        return []

    # 处理 $ref
    if "$ref" in schema:
        resolved = resolve_ref(swagger, schema["$ref"])
        return flatten_schema(swagger, resolved, prefix)

    # 处理 allOf（swagger 2.0 的组合模式）
    if "allOf" in schema:
        fields = []
        for sub_schema in schema["allOf"]:
            fields.extend(flatten_schema(swagger, sub_schema, prefix))
        return fields

    # 处理 object
    if schema.get("type") == "object" or "properties" in schema:
        fields = []
        properties = schema.get("properties", {})
        required_fields = schema.get("required", [])
        for field_name, field_schema in properties.items():
            full_name = f"{prefix}{field_name}" if not prefix else f"{prefix}.{field_name}"
            field_type = get_type_string(swagger, field_schema)
            description = field_schema.get("description", "")

            # 枚举值
            enum_values = field_schema.get("enum", [])
            if enum_values:
                description += f" 可选值: {', '.join(str(v) for v in enum_values)}"

            fields.append({
                "name": full_name if prefix else field_name,
                "type": field_type,
                "required": field_name in required_fields,
                "description": description,
            })

            # 递归展开嵌套结构
            if field_schema.get("type") == "object" or "properties" in field_schema:
                nested_prefix = full_name if prefix else field_name
                fields.extend(flatten_schema(swagger, field_schema, nested_prefix))
            elif "$ref" in field_schema:
                resolved = resolve_ref(swagger, field_schema["$ref"])
                if resolved.get("type") == "object" or "properties" in resolved:
                    nested_prefix = full_name if prefix else field_name
                    fields.extend(flatten_schema(swagger, resolved, nested_prefix))
            elif field_schema.get("type") == "array":
                # array 类型：展开 items 内的字段
                items = field_schema.get("items", {})
                nested_prefix = (full_name if prefix else field_name) + "[]"
                if "$ref" in items:
                    resolved = resolve_ref(swagger, items["$ref"])
                    fields.extend(flatten_schema(swagger, resolved, nested_prefix))
                elif items.get("type") == "object" or "properties" in items:
                    fields.extend(flatten_schema(swagger, items, nested_prefix))

        return fields

    # 处理 array
    if schema.get("type") == "array":
        items = schema.get("items", {})
        if "$ref" in items:
            resolved = resolve_ref(swagger, items["$ref"])
            return flatten_schema(swagger, resolved, prefix + "[]" if prefix else "items[]")
        return []

    return []


def get_type_string(swagger: dict, schema: dict) -> str:
    """获取字段的类型描述字符串"""
    if not schema:
        return "any"

    if "$ref" in schema:
        ref_name = schema["$ref"].split("/")[-1]
        # 去掉 types. 或 models. 前缀
        ref_name = re.sub(r"^(types|models)\.", "", ref_name)
        return ref_name

    field_type = schema.get("type", "any")

    if field_type == "array":
        items = schema.get("items", {})
        item_type = get_type_string(swagger, items)
        return f"array[{item_type}]"

    if field_type == "integer":
        fmt = schema.get("format", "")
        return f"integer({fmt})" if fmt else "integer"

    if field_type == "number":
        fmt = schema.get("format", "")
        return f"number({fmt})" if fmt else "number"

    if field_type == "string":
        fmt = schema.get("format", "")
        return f"string({fmt})" if fmt else "string"

    return field_type


def parse_routes(project_root: str, router_patterns: list[str]) -> dict:
    """
    解析路由文件，建立 path → handler 的映射。
    提取中间件信息（JWT、限流等）。
    返回: { "/admin/book/list": {"handler": "admin.BookList", "middlewares": [...], ...} }
    """
    route_map = {}

    for pattern in router_patterns:
        full_pattern = os.path.join(project_root, pattern)
        for file_path in glob_mod.glob(full_pattern):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 提取 Group 定义及其中间件
            # 例如：adminRouter := route.Group("/admin", middlewares.AuthorizeJWT())
            group_map = {}       # 变量名 → 前缀路径
            group_middlewares = {}  # 变量名 → [中间件列表]

            # 匹配 Group 定义（带或不带中间件）
            group_re = re.compile(
                r'(\w+)\s*:?=\s*(?:route|router|r)\.Group\(\s*["\']([^"\']*)["\']([^)]*)\)'
            )
            for match in group_re.finditer(content):
                var_name = match.group(1)
                prefix = match.group(2).strip("/")
                rest_args = match.group(3)
                group_map[var_name] = prefix
                # 提取 Group 级中间件
                mw_list = extract_middlewares(rest_args)
                if mw_list:
                    group_middlewares[var_name] = mw_list

            # 嵌套 Group：books := authors.Group("book/tag")
            nested_group_re = re.compile(
                r'(\w+)\s*:?=\s*(\w+)\.Group\(\s*["\']([^"\']*)["\']([^)]*)\)'
            )
            for match in nested_group_re.finditer(content):
                var_name = match.group(1)
                parent_var = match.group(2)
                sub_prefix = match.group(3).strip("/")
                rest_args = match.group(4)
                parent_prefix = group_map.get(parent_var, "")
                group_map[var_name] = f"{parent_prefix}/{sub_prefix}" if parent_prefix else sub_prefix
                # 继承父 Group 的中间件 + 自身的
                parent_mw = group_middlewares.get(parent_var, [])
                own_mw = extract_middlewares(rest_args)
                if parent_mw or own_mw:
                    group_middlewares[var_name] = parent_mw + own_mw

            # 提取路由注册（增强版：提取完整参数列表中的中间件）
            # 匹配模式：varName.METHOD("path", [middlewares...,] handler)
            route_re = re.compile(
                r'(?://\s*(.+)\n\s*)?'  # 可选的前置注释
                r'(\w+)\.(GET|POST|PUT|DELETE|PATCH)\(\s*["\']([^"\']*)["\']'
                r'\s*,\s*(.+?)\)',
                re.MULTILINE,
            )
            for match in route_re.finditer(content):
                comment = (match.group(1) or "").strip()
                var_name = match.group(2)
                method = match.group(3)
                sub_path = match.group(4).strip("/")
                args_str = match.group(5).strip()

                # 解析参数列表：最后一个非闭包参数是 handler，前面的是中间件
                handler, route_mws = parse_route_args(args_str)

                if not handler:
                    continue

                prefix = group_map.get(var_name, "")
                full_path = f"/{prefix}/{sub_path}" if prefix else f"/{sub_path}"
                full_path = re.sub(r"/+", "/", full_path)

                # 合并 Group 级和路由级中间件
                all_middlewares = group_middlewares.get(var_name, []) + route_mws

                route_map[full_path] = {
                    "handler": handler,
                    "method": method,
                    "comment": comment,
                    "route_file": os.path.relpath(file_path, project_root),
                    "middlewares": all_middlewares,
                }

    return route_map


def extract_middlewares(args_str: str) -> list[str]:
    """从参数字符串中提取中间件名称"""
    middlewares = []
    # 匹配 middlewares.Xxx(...) 或 middlewares.Xxx() 或 middlewares.Xxx
    mw_re = re.compile(r'middlewares\.(\w+)(?:\([^)]*\))?')
    for match in mw_re.finditer(args_str):
        middlewares.append(match.group(1))
    return middlewares


def parse_route_args(args_str: str) -> tuple[str, list[str]]:
    """
    解析路由注册的参数列表。
    最后一个 pkg.Func 格式的参数是 handler，之前的中间件参数。
    返回 (handler, [middlewares])
    """
    middlewares = []

    # 提取所有中间件
    mw_re = re.compile(r'middlewares\.(\w+)(?:\([^)]*\))?')
    for match in mw_re.finditer(args_str):
        mw_name = match.group(1)
        middlewares.append(mw_name)

    # 提取 handler（最后一个 pkg.Func 格式）
    # 排除 middlewares 包
    handler_re = re.compile(r'(?<!middlewares\.)(\b\w+\.\w+)\s*$')
    match = handler_re.search(args_str)
    if match:
        return match.group(1), middlewares

    # 备选：找所有 pkg.Func，取最后一个非 middlewares 的
    all_funcs = re.findall(r'\b(\w+\.\w+)', args_str)
    for func in reversed(all_funcs):
        if not func.startswith("middlewares.") and not func.startswith("fmt.") and not func.startswith("rate."):
            return func, middlewares

    return "", middlewares


def extract_handler_source(handler: str, project_root: str) -> str:
    """提取 handler 函数的源码"""
    if not handler or "." not in handler:
        return ""

    parts = handler.split(".")
    pkg = parts[0]
    func_name = parts[1]

    # 映射包名到目录（通用规则 + 项目特定映射）
    pkg_dirs = [
        f"controllers/{pkg}",
        f"controllers",
        f"handler/{pkg}",
        f"handlers/{pkg}",
        f"api/{pkg}",
        f"routes/{pkg}",
    ]

    for pkg_dir in pkg_dirs:
        full_dir = os.path.join(project_root, pkg_dir)
        if not os.path.isdir(full_dir):
            continue

        for file_path in glob_mod.glob(os.path.join(full_dir, "*")):
            if not file_path.endswith((".go", ".py", ".ts", ".js", ".java")):
                continue
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    file_content = f.read()

                # 通用函数提取：找 func FuncName 到下一个顶层 func
                func_source = extract_function(file_content, func_name)
                if func_source:
                    return func_source
            except Exception:
                continue

    return ""


def extract_function(content: str, func_name: str) -> str:
    """从文件内容中提取指定函数的代码（语言无关，基于缩进/大括号匹配）"""
    # Go 风格：func FuncName(
    pattern = rf'^(func\s+{func_name}\s*\(.*?)(?=\nfunc\s|\Z)'
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if match:
        source = match.group(1).strip()
        lines = source.split("\n")
        if len(lines) > 150:
            return "\n".join(lines[:150]) + f"\n// ... 截断，函数共 {len(lines)} 行"
        return source

    # Python 风格：def func_name(
    pattern = rf'^(def\s+{func_name}\s*\(.*?)(?=\ndef\s|\nclass\s|\Z)'
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if match:
        source = match.group(1).strip()
        lines = source.split("\n")
        if len(lines) > 150:
            return "\n".join(lines[:150]) + f"\n# ... 截断，函数共 {len(lines)} 行"
        return source

    return ""


def resolve_handler_file(handler: str, project_root: str) -> str:
    """根据 handler 名称推断 controller 文件路径"""
    # handler 格式：admin.BookList, author.WechatLogin, baseController.TagCreate
    parts = handler.split(".")
    if len(parts) != 2:
        return ""

    pkg = parts[0]
    # 通用搜索策略：在常见目录下找匹配的包
    search_dirs = [
        f"controllers/{pkg}",
        f"controller/{pkg}",
        f"handlers/{pkg}",
        f"handler/{pkg}",
        f"api/{pkg}",
        f"routes/{pkg}",
        "controllers",
        "handlers",
        "api",
    ]

    func_name = parts[1]
    for pkg_dir in search_dirs:
        full_dir = os.path.join(project_root, pkg_dir)
        if not os.path.isdir(full_dir):
            continue
        for file_path in glob_mod.glob(os.path.join(full_dir, "*.go")):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            if re.search(rf"func\s+{func_name}\s*\(", content):
                return os.path.relpath(file_path, project_root)

    return ""


def build_manifest(config: dict) -> list[dict]:
    """构建完整的 api-manifest"""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    swagger_path = os.path.join(project_root, config["swagger_path"])

    swagger = load_swagger(swagger_path)
    route_map = parse_routes(project_root, config["router_patterns"])

    # 从配置获取项目信息
    project = config["project"]
    route_groups = config.get("route_groups", {})
    tag_module_map = config.get("tag_module_map", {})

    manifest = []

    for path, methods in swagger.get("paths", {}).items():
        for method, operation in methods.items():
            if method in ("parameters",):  # 跳过全局参数
                continue

            method_upper = method.upper()
            summary = operation.get("summary", "")
            description = operation.get("description", "")
            tags = operation.get("tags", [])
            tag = tags[0] if tags else "未分类"

            # 确定模块名
            module = tag_module_map.get(tag, tag)

            # 确定角色/分组
            group = "未分类"
            for prefix, group_name in route_groups.items():
                if path.startswith(prefix):
                    group = group_name
                    break

            # 从路由映射中查找 handler
            route_info = route_map.get(path, {})
            handler = route_info.get("handler", "")
            route_comment = route_info.get("comment", "")
            route_file = route_info.get("route_file", "")

            # 推断 handler 文件
            handler_file = resolve_handler_file(handler, project_root) if handler else ""

            # 解析参数
            parameters = []
            for param in operation.get("parameters", []):
                if param.get("in") == "body":
                    # body 参数需要展开 schema
                    body_schema = param.get("schema", {})
                    body_fields = flatten_schema(swagger, body_schema)
                    for field in body_fields:
                        parameters.append({
                            "name": field["name"],
                            "in": "body",
                            "type": field["type"],
                            "required": field["required"],
                            "description": field["description"],
                        })
                else:
                    parameters.append({
                        "name": param.get("name", ""),
                        "in": param.get("in", ""),
                        "type": param.get("type", ""),
                        "required": param.get("required", False),
                        "description": param.get("description", ""),
                    })

            # 解析响应
            response_fields = []
            resp_200 = operation.get("responses", {}).get("200", {})
            resp_schema = resp_200.get("schema", {})
            if resp_schema:
                response_fields = flatten_schema(swagger, resp_schema)

            # 构建 manifest 条目
            # 提取中间件信息
            middlewares = route_info.get("middlewares", [])

            # 提取 handler 源码
            handler_source = extract_handler_source(handler, project_root) if handler else ""

            entry = {
                "id": f"{project}::{method_upper}::{path}",
                "method": method_upper,
                "path": path,
                "summary": summary or description or route_comment,
                "description": description,
                "tag": tag,
                "module": module,
                "group": group,
                "handler": handler,
                "handler_file": handler_file,
                "route_file": route_file,
                "middlewares": middlewares,
                "handler_source": handler_source,
                "parameters": parameters,
                "response_fields": response_fields,
            }
            manifest.append(entry)

    # 按 group → module → path 排序
    manifest.sort(key=lambda x: (x["group"], x["module"], x["path"]))
    return manifest


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Phase 1: 生成 api-manifest.json")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    args = parser.parse_args()

    # 切换工作目录到脚本所在目录
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    config = load_config(args.config)
    manifest = build_manifest(config)

    # 输出
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "api-manifest.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"✅ 生成完成: {output_path}")
    print(f"   共解析 {len(manifest)} 个接口")

    # 按模块统计
    module_count: dict[str, int] = {}
    for entry in manifest:
        key = f"{entry['group']} / {entry['module']}"
        module_count[key] = module_count.get(key, 0) + 1
    print("\n📊 模块统计:")
    for module, count in sorted(module_count.items()):
        print(f"   {module}: {count} 个接口")


if __name__ == "__main__":
    main()
