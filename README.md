# api-doc-gen

从 Swagger/OpenAPI + 源码自动生成 AI 知识库友好的 API 文档，并可一键转为 MCP Server。

## 特点

- **AI 驱动**：调用 LLM 分析源码，自动提取业务逻辑、约束条件、错误码、数据依赖
- **RAG 友好**：输出带 YAML frontmatter 的 Markdown，metadata 支持过滤和语义检索
- **MCP 集成**：从 manifest 一键生成 MCP Server，让 AI Agent 直接调用你的 API
- **流程文档**：AI 分析接口关系，自动识别业务流程并生成操作指南
- **增量更新**：支持只处理指定接口或变更的接口，不用每次全量跑
- **人工确认**：关键步骤暂停让你 review，不满意可以跳过或重跑
- **语言无关**：不绑定特定编程语言，AI 自行读取源码理解逻辑
- **多项目通用**：CLI 工具，在任何项目目录下 init 即可使用

## 安装

```bash
# 从 GitHub 直接安装（推荐）
pip install git+https://github.com/ckall/api-doc-gen.git

# 或者克隆后本地开发安装
git clone https://github.com/ckall/api-doc-gen.git
cd api-doc-gen
pip install -e .
```

## 快速开始

```bash
# 进入你的项目目录
cd /path/to/your/project

# 1. 初始化（交互式，自动探测 swagger 文件和路由）
api-doc-gen init

# 2. 生成接口清单
api-doc-gen manifest

# 3. AI 分析生成文档
api-doc-gen run

# 4. 生成业务流程文档
api-doc-gen flow

# 5. 生成 MCP Server（让 AI Agent 调用你的 API）
api-doc-gen mcp --base-url http://localhost:8080

# 6. 生成 SKILL.md（MCP Server 使用说明）
api-doc-gen skill
```

## 命令

### `api-doc-gen init`

初始化项目配置，生成 `.api-doc-gen/config.yaml`。

```bash
# 交互式
api-doc-gen init

# 非交互式
api-doc-gen init \
  --source . \
  --swagger ./docs/swagger.json \
  --project myapp \
  --system "我的系统" \
  --model claude-opus-4-8 \
  --base-url http://localhost:3012/v1 \
  --api-key sk-xxx
```

### `api-doc-gen manifest`

解析 Swagger + 路由文件，生成接口清单 `api-manifest.json`。

这是整个工具链的**核心中间格式**，后续的 `run`、`flow`、`mcp` 都基于 manifest 工作。

提取内容：
- 接口基本信息（method、path、参数、响应）
- 中间件信息（JWT、限流等）
- Handler 源码片段

### `api-doc-gen run`

运行 AI 增强 pipeline，逐批分析接口并生成文档。

```bash
# 全量运行
api-doc-gen run

# 只跑某些接口
api-doc-gen run --path "/admin/book/*"
api-doc-gen run --module "书籍管理"
api-doc-gen run --handler "admin.BookList,admin.BookAdd"
api-doc-gen run --group "管理后台"

# 增量：只跑新增/未处理的
api-doc-gen run --changed

# 重跑失败的
api-doc-gen run --retry-failed

# 全自动（跳过人工确认）
api-doc-gen run --auto

# 调整批次和并发
api-doc-gen run --batch 10 -c 2
```

### `api-doc-gen flow`

AI 分析已生成的接口文档，识别业务流程，生成面向终端用户的操作指南。

采用两步式生成：
1. **识别流程列表**（轻量）：AI 从接口文档中分析出所有用户操作流程
2. **逐个生成详情**：对每个流程单独生成完整的操作指南 `.md` 文件

流程列表会持久化到 `flow-manifest.json`，后续增量更新不需要再调 AI 识别。

```bash
# 生成流程文档（会先展示识别结果让你确认）
api-doc-gen flow

# 全自动（跳过确认）
api-doc-gen flow --auto

# 增量：只更新涉及某些路径的流程
api-doc-gen flow --path "/admin/book/royalty/*"

# 增量：只更新某个模块的流程
api-doc-gen flow --module "财务管理"

# 强制重新识别流程列表（重新调 AI）
api-doc-gen flow --force
```

生成内容包括：
- 每个流程的完整操作步骤
- 每步关联的接口文档链接（流程 → 接口闭环引用）
- 前置条件、注意事项、常见问题 (FAQ)
- 流程总览文档

增量更新原理：
- 首次运行 AI 识别流程列表，保存到 `flow-manifest.json`（含每个流程的 related_apis）
- 后续 `--path`/`--module` 参数会查 `flow-manifest.json` 定位受影响的流程
- 只重新生成这些流程的文档，不影响其他

适用场景：知识库让不懂系统的人也能学会操作；配合 git hook 自动增量更新。

### `api-doc-gen mcp`

将 manifest 转换为可运行的 MCP Server，让 AI Agent 直接调用你的 API。

**不需要 AI 参与**，纯规则转换，秒出结果。

```bash
# 生成完整 MCP Server（server.py + tools.json + requirements.txt）
api-doc-gen mcp --base-url http://localhost:8080

# 只生成某些模块
api-doc-gen mcp --module "书籍管理,用户管理"

# 按路径过滤
api-doc-gen mcp --path "/admin/*"

# 只生成 tool 定义 JSON（给别的 MCP server 用）
api-doc-gen mcp --mode schema -o tools.json

# 自定义输出
api-doc-gen mcp -o ./my-mcp-server --name "author-api" --transport stdio
```

生成的 MCP Server：
- `server.py` — 可直接运行的 MCP server，自动转发请求到目标 API
- `tools.json` — MCP tool 定义（name、description、inputSchema）
- `requirements.txt` — 依赖（mcp、httpx）

如果已经跑过 `api-doc-gen run` 生成了接口文档，tool 的 description 会自动从文档中提取业务逻辑作为增强。

### `api-doc-gen skill`

从 manifest + 流程文档生成 MCP Server 的 SKILL.md，让 AI agent 知道这个 server 能干什么、什么场景触发。

**不需要 AI 参与**，纯规则从已有数据中提取。以操作流程为核心组织内容。

```bash
# 生成 SKILL.md（默认输出到 mcp-server/SKILL.md）
api-doc-gen skill

# 指定输出路径
api-doc-gen skill -o ./my-mcp-server/SKILL.md

# AI 增强触发场景描述（可选）
api-doc-gen skill --ai
```

生成内容包括：
- 触发场景（什么时候使用这个 MCP server）
- 操作流程（每个流程的步骤和调用顺序）
- 独立操作（不属于流程的接口列表）
- 模块概览（各模块接口数和说明）
- 认证说明

前置条件：需要先跑过 `manifest`，如果有 `flow` 文档效果更好。

### `api-doc-gen status`

查看任务进度（已完成、失败、未处理）。

### `api-doc-gen reset`

重置任务状态，下次 run 从头开始。

## 核心流程

```
Swagger/OpenAPI + 路由源码
        │
        ▼
    manifest (api-manifest.json)  ← 标准中间格式
        │
        ├──→ run   → AI 分析 → 接口文档 (.md)
        │                          │
        │                          ▼
        │                     flow → 流程文档 (.md)
        │
        └──→ mcp   → MCP Server (server.py + tools.json)
```

所有下游命令（`run`、`flow`、`mcp`）都以 manifest 为输入，不再直接依赖 Swagger 文件。

## 生成的文档格式

每个接口一个 `.md` 文件：

```markdown
---
id: "myapp::GET::/api/users"
project: myapp
system: 用户系统
module: 用户管理
method: GET
path: /api/users
summary: 查询用户列表
tags: [用户, 查询, 列表]
aliases: [用户列表接口, 查询用户]
---

# GetUsers

查询用户列表（分页，支持按姓名/状态筛选）

## 请求

GET /api/users

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| name | string | 否 | 用户姓名 |
| page | integer | 否 | 页码 |

## 业务逻辑

1. 验证 JWT token，获取当前操作员身份
2. 组合查询条件，调用 UserService.List()
3. 按创建时间倒序，分页返回

## 数据依赖

### 数据库
- read: `user_db.users` — 查询用户列表

### MQ 发送
- → `user_events` / `user.queried` — 记录查询审计日志

### HTTP 外部调用
- GET `auth-service/api/v1/permissions` — 校验数据权限

## 约束

- 需要 JWT 认证
- page_size 最大 100

## 错误码

| code | 说明 | 处理建议 |
|------|------|----------|
| 401 | 未认证 | 重新登录 |

## 关联接口

- GET /api/users/:id - 用户详情
- POST /api/users - 创建用户
```

## 配置

`init` 后生成的 `.api-doc-gen/config.yaml`：

```yaml
project: myapp
system: 我的系统
source_root: /path/to/project
swagger_path: /path/to/swagger.json
router_patterns:
  - "router/*.go"
model: claude-opus-4-8
base_url: http://localhost:3012/v1
api_key: sk-xxx
batch_size: 5
concurrency: 1

# MCP 配置（可选，api-doc-gen mcp 使用）
mcp:
  server:
    name: "myapp-api"
    base_url: http://localhost:8080
  auth:
    type: bearer              # bearer / api_key / basic / custom_header
    token_env: "API_TOKEN"    # 从环境变量读 token（不要把 token 写进配置）
  filter:
    modules: []               # 空=全部，或 ["书籍管理", "用户管理"]
    paths: []                 # 按路径 ["/admin/*"]
    exclude_paths: ["/health", "/metrics"]
  output:
    mode: server              # server / schema
    path: ./mcp-server
    transport: stdio          # stdio / sse
```

## 环境变量

优先级：环境变量 > config.yaml

| 变量 | 说明 |
|------|------|
| `OPENAI_API_KEY` | API Key |
| `OPENAI_BASE_URL` | API 地址 |
| `OPENAI_MODEL` | 模型名 |
| `MCP_BASE_URL` | MCP Server 转发的目标 API 地址（运行时覆盖） |

## 自定义模板

`init` 后会在 `.api-doc-gen/templates/` 生成默认模板，你可以直接修改：

- `api.md.j2` — 接口文档模板
- `flow.md.j2` — 流程文档模板
- `VARIABLES.md` — 所有可用变量清单

模板语法使用 [Jinja2](https://jinja.palletsprojects.com/)，示例：

```jinja2
# {{ title }}

{{ summary }}

## 参数

{% for p in parameters %}
| {{ p.name }} | {{ p.type }} | {{ "是" if p.required else "否" }} | {{ p.description }} |
{% endfor %}

## 业务逻辑

{{ business_logic }}

{% if sub_calls %}
## 调用链

{% for sc in sub_calls %}
### `{{ sc.method }}`
- 入参: `{{ sc.input }}`
- 返回: `{{ sc.output }}`
{% endfor %}
{% endif %}
```

修改模板后直接 `api-doc-gen run`，不需要重新 init。重新 init 不会覆盖已修改的模板。

## 工作目录

所有产物在 `.api-doc-gen/` 下：

```
.api-doc-gen/
├── config.yaml          # 配置
├── api-manifest.json    # 接口清单（核心中间格式）
├── task_state.json      # 任务进度（run 命令使用）
├── flow_error.log       # 流程生成错误日志（如有）
├── templates/           # 文档模板（可自定义）
│   ├── api.md.j2
│   ├── flow.md.j2
│   └── VARIABLES.md
├── docs/                # 生成的文档
│   ├── _overview.md
│   ├── _flows/          # 流程文档（flow 命令生成）
│   │   ├── _flow_overview.md
│   │   └── 内容管理/
│   ├── 管理后台/书籍管理/
│   └── 作者端/用户管理/
└── mcp-server/          # MCP Server（mcp 命令生成）
    ├── server.py
    ├── tools.json
    └── requirements.txt
```

## 适合谁

- 企业内多项目想统一建 AI 知识库
- 用 RAG 做内部 API 问答机器人
- 让 AI Agent 通过 MCP 直接操作你的系统
- 新人入职快速了解系统接口和业务逻辑
- 接口变更后自动更新文档

## License

MIT
