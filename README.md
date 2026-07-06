# api-doc-gen

从 Swagger/OpenAPI + 源码自动生成 AI 知识库友好的 API 文档。

## 特点

- **AI 驱动**：调用 LLM 分析源码，自动提取业务逻辑、约束条件、错误码、数据依赖
- **RAG 友好**：输出带 YAML frontmatter 的 Markdown，metadata 支持过滤和语义检索
- **增量更新**：支持只处理指定接口或变更的接口，不用每次全量跑
- **人工确认**：关键步骤暂停让你 review，不满意可以跳过或重跑
- **语言无关**：不绑定特定编程语言，AI 自行读取源码理解逻辑
- **多项目通用**：CLI 工具，在任何项目目录下 init 即可使用

## 安装

```bash
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

### `api-doc-gen status`

查看任务进度（已完成、失败、未处理）。

### `api-doc-gen reset`

重置任务状态，下次 run 从头开始。

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
service: my-service
source_root: /path/to/project
swagger_path: /path/to/swagger.json
router_patterns:
  - "router/*.go"
model: claude-opus-4-8
base_url: http://localhost:3012/v1
api_key: sk-xxx
batch_size: 5
concurrency: 1
```

## 环境变量

优先级：环境变量 > config.yaml

| 变量 | 说明 |
|------|------|
| `OPENAI_API_KEY` | API Key |
| `OPENAI_BASE_URL` | API 地址 |
| `OPENAI_MODEL` | 模型名 |

## 工作目录

所有产物在 `.api-doc-gen/` 下：

```
.api-doc-gen/
├── config.yaml          # 配置
├── api-manifest.json    # 接口清单
├── task_state.json      # 任务进度
└── docs/                # 生成的文档
    ├── _overview.md
    ├── 管理后台/书籍管理/
    └── 作者端/用户管理/
```

## 适合谁

- 企业内多项目想统一建 AI 知识库
- 用 RAG 做内部 API 问答机器人
- 新人入职快速了解系统接口和业务逻辑
- 接口变更后自动更新文档

## License

MIT
