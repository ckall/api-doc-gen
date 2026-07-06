# API 知识库文档模板
#
# 本模板定义了 AI 知识库中每个 API 接口文档的标准格式。
# gen_docs.py 和 pipeline 都按此模板生成。
#
# 板块顺序（固定，不可调整）：
#   1. YAML frontmatter — RAG metadata 过滤
#   2. 标题 + 一句话描述 — 快速定位
#   3. 请求 — method + path
#   4. 参数表 — 入参说明
#   5. 业务逻辑 — 核心，AI 从源码翻译出来的流程描述
#   6. 响应表 — 返回字段
#   7. 约束 — 认证、限流、校验规则
#   8. 错误码 — 从代码中实际提取的
#   9. 关联接口 — 业务流程上下游
#  10. 源码位置 — 需要深入查看时的指引
#  11. 备注 — 其他补充
#
# 使用说明：
#   - frontmatter 中所有字段都参与 RAG 检索过滤
#   - 业务逻辑用自然语言按步骤描述，包含状态变更、外部调用、条件分支
#   - 错误码只写代码中实际存在的，不要推测
#   - 约束包括中间件（JWT/限流）和参数校验
#   - 关联接口带上 method + path + 说明

# ====================================================================
# 模板正文
# ====================================================================

TEMPLATE = """---
id: "{id}"
project: {project}
system: {system}
service: {service}
module: {module}
group: {group}
method: {method}
path: {path}
summary: {summary}
version: {version}
handler: {handler}
tags:
{tags}
aliases:
{aliases}
related:
{related}
updated_at: {updated_at}
---

# {title}

{summary}

## 请求

```
{method} {path}
```

## 参数

{parameters_table}

## 业务逻辑

{business_logic}

## 响应

{response_table}

## 约束

{constraints}

## 错误码

{error_codes_table}

## 关联接口

{related_apis}

## 源码

{source_info}

## 备注

{notes}
"""

# ====================================================================
# 字段说明
# ====================================================================

FIELD_SPEC = {
    # --- frontmatter（RAG metadata）---
    "id":        "唯一标识，格式: {project}::{METHOD}::{path}",
    "project":   "项目标识，如 authorplatform",
    "system":    "所属系统，如 作家后台",
    "service":   "服务/部署单元名，默认与 project 相同",
    "module":    "功能模块，如 书籍管理",
    "group":     "接口分组/角色，如 管理后台、作者端",
    "method":    "HTTP 方法",
    "path":      "完整请求路径",
    "summary":   "一句话描述（AI 增强后的版本）",
    "version":   "API 版本",
    "handler":   "代码中的 handler 函数名",
    "tags":      "语义标签，3-5个，用于辅助检索",
    "aliases":   "用户可能的问法，2-4个",
    "related":   "关联接口路径列表",
    "updated_at": "文档最后更新日期",

    # --- 正文板块 ---
    "title":             "标题，优先用 handler 函数名",
    "parameters_table":  "参数表格：参数 | 类型 | 必填 | 说明",
    "business_logic":    "业务逻辑，按步骤描述。最核心的板块",
    "response_table":    "响应字段表格：字段 | 类型 | 说明",
    "constraints":       "约束条件列表：认证要求、限流、参数校验规则",
    "error_codes_table": "错误码表格：code | 说明 | 处理建议",
    "related_apis":      "关联接口列表：METHOD /path - 说明",
    "source_info":       "源码位置：handler 文件 + 路由文件",
    "notes":             "其他补充信息",
}

# ====================================================================
# 示例
# ====================================================================

EXAMPLE = """---
id: "authorplatform::GET::/admin/book/list"
project: authorplatform
system: 作家后台
service: goc-authorplatform
module: 书籍管理
group: 管理后台
method: GET
path: /admin/book/list
summary: 查询书籍列表（分页，支持按作者/书名/状态筛选）
version: v1
handler: admin.BookList
tags:
  - 书籍
  - 查询
  - 列表
  - 管理后台
aliases:
  - 书籍列表接口
  - 查询书籍
  - admin book list
related:
  - GET /admin/book/detail
  - POST /admin/book/add
updated_at: 2026-07-06
---

# BookList

查询书籍列表（分页，支持按作者/书名/状态筛选）

## 请求

```
GET /admin/book/list
```

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| book_name | string | 否 | 书籍名称 |
| author_name | string | 否 | 作者名称 |
| book_status | integer | 否 | 书籍状态 1-已签约 2-待审核 3-审核中 4-审核拒绝 5-待作者签约 6-待确认签约 7-待绑定作者 8-下架 |
| editor_name | string | 否 | 编辑名称 |
| competition_id | integer | 否 | 活动id，筛选加入了对应活动的书籍 |
| page | integer | 否 | 分页 |
| page_size | integer | 否 | 每页数量 |

## 业务逻辑

1. 从 JWT token 中解析当前管理员身份（wechat_uuid）
2. 接收分页参数（page/page_size）和多个筛选条件
3. 调用 BookService.GetBookList() 组合查询：
   - book_name: 按书名模糊匹配
   - author_name: 按作者名模糊匹配
   - book_status: 精确匹配状态值
   - editor_name: 按责编名模糊匹配
4. 如果传了 competition_id，JOIN book_competition 表过滤参加该活动的书籍
5. 按创建时间倒序排列
6. 分页返回 list + total

## 响应

| 字段 | 类型 | 说明 |
|------|------|------|
| data.list | array[BookListRespList] | 书籍列表 |
| data.list[].id | integer | 书籍ID |
| data.list[].book_name | string | 书名 |
| data.list[].author_name | string | 作者名 |
| data.list[].book_status | integer | 状态 |
| data.list[].editor_name | string | 责编 |
| data.total | integer | 总数 |

## 约束

- 需要 JWT 认证（AuthorizeJWT 中间件）
- 仅管理后台角色可访问
- page 未传默认 1，page_size 未传默认 10，最大 100

## 错误码

| code | 说明 | 处理建议 |
|------|------|----------|
| 401 | token 无效或过期 | 重新登录获取 token |
| 400 | 参数格式错误 | 检查 page/page_size 为正整数 |

## 关联接口

- GET /admin/book/detail - 查看单本书籍详情
- POST /admin/book/add - 添加新书籍
- POST /admin/book/edit - 编辑书籍信息
- GET /admin/book/audit/list - 查看审核记录

## 源码

- Handler: `admin.BookList` → `controllers/admin/bookController.go`
- 路由: `router/admin.go`

## 备注

管理后台书籍管理的核心列表接口，前端表格数据源。支持多条件组合筛选。
"""
