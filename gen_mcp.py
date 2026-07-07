#!/usr/bin/env python3
"""
gen_mcp.py - 将 api-manifest 转换为 MCP Server 代码

特点：
- auth 插件化：生成独立的 auth.py，用户可自行修改认证逻辑
- tool 定义完整：参数带 type/description/required，返回结构有说明
- 支持启动参数透传：MCP 启动时可传 token 等参数给 auth
"""

import json
import os
import re
from pathlib import Path
from string import Template


# ============================================================
# Tool 构建
# ============================================================

def build_tools_from_manifest(manifest: list, config: dict) -> list[dict]:
    """从 api-manifest.json 构建 MCP tool 定义列表"""
    mcp_config = config.get("mcp", {})
    filter_conf = mcp_config.get("filter", {})
    filter_modules = filter_conf.get("modules", [])
    filter_paths = filter_conf.get("paths", [])
    exclude_paths = filter_conf.get("exclude_paths", [])

    docs_dir = os.path.join(os.getcwd(), ".api-doc-gen", "docs")
    tools = []

    for entry in manifest:
        path = entry["path"]
        method = entry["method"]

        if _should_exclude(path, exclude_paths):
            continue
        if filter_paths and not _matches_any_pattern(path, filter_paths):
            continue
        if filter_modules and entry.get("module") not in filter_modules:
            continue

        tool_name = _make_tool_name(method, path)

        # description：基础 + 文档增强
        base_desc = entry.get("summary", f"{method} {path}")
        doc_desc = _load_doc_description(docs_dir, entry)
        full_desc = f"{base_desc}\n\n{doc_desc}" if doc_desc else base_desc

        # inputSchema：完整参数定义
        input_schema = _build_input_schema(entry)

        # 响应结构摘要
        response_hint = _build_response_hint(entry)

        tool = {
            "name": tool_name,
            "description": full_desc,
            "method": method,
            "path": path,
            "module": entry.get("module", ""),
            "group": entry.get("group", ""),
            "inputSchema": input_schema,
            "response_hint": response_hint,
            "middlewares": entry.get("middlewares", []),
        }
        tools.append(tool)

    return tools


def _build_input_schema(entry: dict) -> dict:
    """从 manifest entry 构建完整的 inputSchema"""
    properties = {}
    required = []

    # 路径参数
    path_params = re.findall(r"\{(\w+)\}", entry.get("path", ""))
    for param_name in path_params:
        properties[param_name] = {
            "type": "string",
            "description": f"路径参数: {param_name}",
        }
        required.append(param_name)

    for param in entry.get("parameters", []):
        name = param.get("name", "")
        if not name:
            continue

        param_type = param.get("type", "string")
        param_in = param.get("in", "body")

        # 类型映射
        json_type = _map_type(param_type)

        prop: dict = {"type": json_type}

        # description 拼接位置信息
        desc_parts = []
        if param.get("description"):
            desc_parts.append(param["description"])
        if param_in and param_in != "body":
            desc_parts.append(f"[{param_in}]")
        if desc_parts:
            prop["description"] = " ".join(desc_parts)

        # 枚举值
        if param.get("enum"):
            prop["enum"] = param["enum"]

        properties[name] = prop

        if param.get("required") and name not in required:
            required.append(name)

    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _build_response_hint(entry: dict) -> str:
    """构建响应结构摘要，写入 tool description"""
    resp_fields = entry.get("response_fields", [])
    if not resp_fields:
        return ""

    lines = ["返回字段:"]
    for field in resp_fields[:15]:  # 最多列 15 个
        name = field.get("name", "")
        ftype = field.get("type", "")
        desc = field.get("description", "")
        line = f"  - {name}: {ftype}"
        if desc:
            line += f" ({desc})"
        lines.append(line)

    if len(resp_fields) > 15:
        lines.append(f"  ... 共 {len(resp_fields)} 个字段")

    return "\n".join(lines)


def _map_type(param_type: str) -> str:
    """Swagger 类型 → JSON Schema 类型"""
    if param_type in ("integer", "int", "int64", "int32"):
        return "integer"
    elif param_type in ("number", "float", "double"):
        return "number"
    elif param_type in ("boolean", "bool"):
        return "boolean"
    elif param_type.startswith("array"):
        return "array"
    return "string"


# ============================================================
# 文档增强
# ============================================================

def _load_doc_description(docs_dir: str, entry: dict) -> str:
    """从已生成的 .md 文档中提取增强描述"""
    group = entry.get("group", "未分类")
    module = entry.get("module", "未分类")
    method = entry.get("method", "")
    path = entry.get("path", "")

    filename = f"{method}_{path.replace('/', '_').strip('_')}.md"
    doc_path = os.path.join(docs_dir, group, module, filename)

    if not os.path.isfile(doc_path):
        return ""

    try:
        with open(doc_path, "r", encoding="utf-8") as f:
            content = f.read()

        sections = []

        # 业务逻辑
        m = re.search(r"## 业务逻辑\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
        if m:
            sections.append(m.group(1).strip())

        # 约束
        m = re.search(r"## 约束\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
        if m:
            sections.append("约束: " + m.group(1).strip())

        # 错误码
        m = re.search(r"## 错误码\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
        if m:
            sections.append("错误码:\n" + m.group(1).strip())

        return "\n\n".join(sections)
    except Exception:
        return ""


# ============================================================
# 工具函数
# ============================================================

def _make_tool_name(method: str, path: str) -> str:
    """生成 tool name: GET /api/v1/books/{id} → get_api_v1_books_id"""
    clean_path = re.sub(r"\{(\w+)\}", r"\1", path)
    clean_path = re.sub(r"[^a-zA-Z0-9]", "_", clean_path)
    clean_path = re.sub(r"_+", "_", clean_path).strip("_")
    return f"{method.lower()}_{clean_path}"


def _should_exclude(path: str, exclude_patterns: list) -> bool:
    for pattern in exclude_patterns:
        if path == pattern or path.startswith(pattern.rstrip("*")):
            return True
    return False


def _matches_any_pattern(path: str, patterns: list) -> bool:
    import fnmatch
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern):
            return True
    return False


# ============================================================
# 生成 MCP Server
# ============================================================

def generate_mcp_server(tools: list[dict], config: dict, output_path: str, auth_type: str = ""):
    """生成完整 MCP Server 目录"""
    mcp_config = config.get("mcp", {})
    server_conf = mcp_config.get("server", {})
    auth_conf = mcp_config.get("auth", {})
    output_conf = mcp_config.get("output", {})

    server_name = server_conf.get("name", config.get("project", "api-mcp-server"))
    base_url = server_conf.get("base_url", "http://localhost:8080")

    # auth_type 优先用参数传入的，其次用配置
    if not auth_type:
        auth_type = auth_conf.get("type", "bearer")

    os.makedirs(output_path, exist_ok=True)

    # 1. 生成 tools.json
    tools_json = _build_tools_json(tools)
    tools_json_path = os.path.join(output_path, "tools.json")
    with open(tools_json_path, "w", encoding="utf-8") as f:
        json.dump(tools_json, f, ensure_ascii=False, indent=2)

    # 2. 生成 auth.py
    auth_file = os.path.join(output_path, "auth.py")
    # 如果用户已经写过 auth.py，不覆盖
    if not os.path.isfile(auth_file):
        auth_code = _render_auth_file(auth_type, auth_conf)
        with open(auth_file, "w", encoding="utf-8") as f:
            f.write(auth_code)

    # 3. 生成 server.py
    tool_routes = _build_tool_routes(tools)
    server_code = _render_server_code(server_name, base_url, tool_routes)
    server_file = os.path.join(output_path, "server.py")
    with open(server_file, "w", encoding="utf-8") as f:
        f.write(server_code)

    # 4. 生成 requirements.txt
    req_file = os.path.join(output_path, "requirements.txt")
    with open(req_file, "w", encoding="utf-8") as f:
        f.write("mcp>=1.0.0\nhttpx>=0.27.0\n")

    # 5. 生成 README
    readme_file = os.path.join(output_path, "README.md")
    with open(readme_file, "w", encoding="utf-8") as f:
        f.write(_render_readme(server_name, base_url, auth_type, len(tools)))

    return server_file, tools_json_path


def _build_tools_json(tools: list[dict]) -> list[dict]:
    """构建 tools.json 内容（完整的 MCP tool 定义）"""
    result = []
    for tool in tools:
        # 拼接完整 description
        desc_parts = [tool["description"]]
        if tool.get("response_hint"):
            desc_parts.append(tool["response_hint"])
        if tool.get("middlewares"):
            desc_parts.append(f"认证: {', '.join(tool['middlewares'])}")

        entry = {
            "name": tool["name"],
            "description": "\n\n".join(desc_parts),
            "inputSchema": tool["inputSchema"],
            "metadata": {
                "method": tool["method"],
                "path": tool["path"],
                "module": tool.get("module", ""),
                "group": tool.get("group", ""),
            },
        }
        result.append(entry)
    return result


def _build_tool_routes(tools: list[dict]) -> str:
    """构建 tool 路由表 JSON"""
    routes = []
    for tool in tools:
        path_params = re.findall(r"\{(\w+)\}", tool["path"])
        routes.append({
            "name": tool["name"],
            "method": tool["method"],
            "path": tool["path"],
            "path_params": path_params,
        })
    return json.dumps(routes, ensure_ascii=False, indent=4)


# ============================================================
# auth.py 模板
# ============================================================

def _render_auth_file(auth_type: str, auth_conf: dict) -> str:
    """生成 auth.py 文件内容"""

    header = '''"""
认证插件 - MCP Server 请求认证

server.py 每次请求前会调用 get_auth_headers(context) 获取认证 headers。
context 包含 MCP 启动时传入的参数（如 token、env 等），你可以按需使用。

自定义认证：直接修改本文件的 get_auth_headers 函数即可。
重新运行 api-doc-gen mcp 不会覆盖已存在的 auth.py。
"""
import os

'''

    if auth_type == "bearer":
        token_env = auth_conf.get("token_env", "API_TOKEN")
        return header + f'''
def get_auth_headers(context: dict = None) -> dict:
    """
    Bearer Token 认证

    优先从 context 中获取 token（MCP 启动时传入），
    其次从环境变量 {token_env} 获取。

    Args:
        context: MCP 启动参数，可能包含 {{"token": "xxx"}}
    """
    context = context or {{}}

    token = context.get("token") or os.environ.get("{token_env}", "")
    if not token:
        raise ValueError(
            "未提供认证 token。"
            "请通过 MCP 参数传入 token，或设置环境变量 {token_env}"
        )
    return {{"Authorization": f"Bearer {{token}}"}}
'''

    elif auth_type == "api_key":
        header_name = auth_conf.get("header", "X-API-Key")
        key_env = auth_conf.get("key_env", "API_KEY")
        return header + f'''
def get_auth_headers(context: dict = None) -> dict:
    """
    API Key 认证

    优先从 context 中获取 api_key（MCP 启动时传入），
    其次从环境变量 {key_env} 获取。

    Args:
        context: MCP 启动参数，可能包含 {{"api_key": "xxx"}}
    """
    context = context or {{}}

    key = context.get("api_key") or os.environ.get("{key_env}", "")
    if not key:
        raise ValueError(
            "未提供 API Key。"
            "请通过 MCP 参数传入 api_key，或设置环境变量 {key_env}"
        )
    return {{"{header_name}": key}}
'''

    else:
        # 自定义模板
        return header + '''
def get_auth_headers(context: dict = None) -> dict:
    """
    自定义认证 - 请根据你的项目需求修改

    Args:
        context: MCP 启动参数，结构由调用方决定。
                 例如: {"token": "xxx", "tenant_id": "yyy"}

    Returns:
        dict: 需要附加到每个 HTTP 请求的 headers

    示例：

        # Bearer Token
        token = context.get("token") or os.environ.get("API_TOKEN", "")
        return {"Authorization": f"Bearer {token}"}

        # 自定义签名
        import hashlib, time
        ts = str(int(time.time()))
        sign = hashlib.md5(f"{secret}{ts}".encode()).hexdigest()
        return {"X-Timestamp": ts, "X-Sign": sign}

        # OAuth2（从 context 拿 refresh token 自动刷新）
        access_token = refresh_oauth_token(context.get("refresh_token"))
        return {"Authorization": f"Bearer {access_token}"}
    """
    context = context or {}

    token = context.get("token") or os.environ.get("API_TOKEN", "")
    if token:
        return {"Authorization": f"Bearer {token}"}

    return {}
'''


# ============================================================
# server.py 模板
# ============================================================

def _render_server_code(server_name: str, base_url: str, tool_routes: str) -> str:
    """渲染 server.py"""
    tmpl = Template(r'''#!/usr/bin/env python3
"""
MCP Server: $server_name
自动生成 by api-doc-gen mcp

运行方式:
    python server.py

环境变量:
    MCP_BASE_URL  - 覆盖目标 API 地址（默认: $base_url）
    API_TOKEN     - 认证 token（如果 auth.py 中使用）
"""

import json
import os
import sys
import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from auth import get_auth_headers


# ============================================================
# 配置
# ============================================================

BASE_URL = os.environ.get("MCP_BASE_URL", "$base_url").rstrip("/")

# MCP 启动参数（调用方可通过 initialization_options 传入）
AUTH_CONTEXT: dict = {}


# ============================================================
# Tool 路由表
# ============================================================

TOOL_ROUTES = $tool_routes


# ============================================================
# Tool 定义
# ============================================================

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_TOOLS_FILE = os.path.join(_SCRIPT_DIR, "tools.json")

with open(_TOOLS_FILE, "r", encoding="utf-8") as _f:
    TOOL_DEFINITIONS = json.load(_f)


# ============================================================
# Server
# ============================================================

app = Server("$server_name")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """列出所有可用的 tool"""
    return [
        Tool(
            name=t["name"],
            description=t["description"],
            inputSchema=t["inputSchema"],
        )
        for t in TOOL_DEFINITIONS
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """调用 tool，转发请求到目标 API"""
    # 查找路由
    route = None
    for r in TOOL_ROUTES:
        if r["name"] == name:
            route = r
            break

    if not route:
        return [TextContent(type="text", text=json.dumps({"error": f"未知 tool: {name}"}, ensure_ascii=False))]

    method = route["method"]
    path = route["path"]
    path_params = route.get("path_params", [])

    # 替换路径参数
    for param in path_params:
        value = arguments.pop(param, "")
        placeholder = "{" + param + "}"
        path = path.replace(placeholder, str(value))

    url = f"{BASE_URL}{path}"

    # 认证
    try:
        headers = get_auth_headers(AUTH_CONTEXT)
    except ValueError as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}, ensure_ascii=False))]

    headers["Content-Type"] = "application/json"

    # 发送请求
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if method in ("GET", "DELETE"):
                resp = await client.request(method, url, params=arguments, headers=headers)
            else:
                resp = await client.request(method, url, json=arguments, headers=headers)

            # 尝试解析 JSON 响应
            try:
                body = resp.json()
            except Exception:
                body = resp.text

            result = {
                "status_code": resp.status_code,
                "body": body,
            }
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    except httpx.TimeoutException:
        return [TextContent(type="text", text=json.dumps({"error": "请求超时"}, ensure_ascii=False))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": f"请求失败: {str(e)}"}, ensure_ascii=False))]


async def main():
    """启动 MCP Server"""
    global AUTH_CONTEXT

    # 支持命令行传参：python server.py --token xxx --tenant-id yyy
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i].startswith("--") and i + 1 < len(args):
            key = args[i][2:].replace("-", "_")
            AUTH_CONTEXT[key] = args[i + 1]
            i += 2
        else:
            i += 1

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
''')

    return tmpl.substitute(
        server_name=server_name,
        base_url=base_url,
        tool_routes=tool_routes,
    )


# ============================================================
# README 模板
# ============================================================

def _render_readme(server_name: str, base_url: str, auth_type: str, tool_count: int) -> str:
    """生成 MCP Server 的 README"""
    return f"""# {server_name} MCP Server

自动生成 by `api-doc-gen mcp`，共 {tool_count} 个 tool。

## 快速使用

```bash
# 安装依赖
pip install -r requirements.txt

# 设置认证（方式: {auth_type}）
export API_TOKEN="your-token-here"

# 启动
python server.py

# 或通过命令行传参
python server.py --token your-token-here
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MCP_BASE_URL` | 目标 API 地址 | `{base_url}` |
| `API_TOKEN` | 认证 token | - |

## 文件说明

| 文件 | 说明 | 可否修改 |
|------|------|----------|
| `server.py` | MCP Server 主入口 | 一般不需要改 |
| `auth.py` | 认证逻辑（插件） | ✅ 自由修改 |
| `tools.json` | Tool 定义 | 重新生成会覆盖 |
| `requirements.txt` | Python 依赖 | 可追加 |

## 自定义认证

编辑 `auth.py` 中的 `get_auth_headers(context)` 函数：

```python
def get_auth_headers(context: dict = None) -> dict:
    # context 来自 MCP 启动参数或命令行 --key value
    token = context.get("token") or os.environ.get("API_TOKEN", "")
    return {{"Authorization": f"Bearer {{token}}"}}
```

重新运行 `api-doc-gen mcp` **不会覆盖**已存在的 `auth.py`。

## MCP 客户端配置

```json
{{
  "mcpServers": {{
    "{server_name}": {{
      "command": "python",
      "args": ["server.py", "--token", "your-token"],
      "cwd": "/path/to/mcp-server"
    }}
  }}
}}
```
"""


# ============================================================
# Schema-only 模式
# ============================================================

def generate_schema_only(tools: list[dict], output_path: str) -> str:
    """只生成 tools.json"""
    tools_json = _build_tools_json(tools)

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(tools_json, f, ensure_ascii=False, indent=2)

    return output_path
