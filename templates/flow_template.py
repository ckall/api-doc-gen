# 流程文档模板
#
# 本模板定义了用户操作流程文档的标准格式。
# gen_flows.py 按此模板生成。
#
# 目标：让一个啥都不懂的人，通过知识库能学会如何使用系统完成某个目标。
#
# 板块顺序（固定）：
#   1. YAML frontmatter — RAG metadata 过滤
#   2. 标题 + 场景描述 — 快速定位"我要做什么"
#   3. 前置条件 — 需要先具备什么
#   4. 操作步骤 — 具体怎么做，每步调什么接口
#   5. 注意事项 — 容易踩坑的地方
#   6. 常见问题 — FAQ
#   7. 涉及接口 — 流程中用到的所有接口汇总

TEMPLATE = """---
id: "{id}"
project: {project}
system: {system}
type: flow
flow_name: {flow_name}
category: {category}
role: {role}
difficulty: {difficulty}
tags:
{tags}
aliases:
{aliases}
related_flows:
{related_flows}
updated_at: {updated_at}
---

# {title}

{description}

## 适用角色

{role_description}

## 前置条件

{prerequisites}

## 操作步骤

{steps}

## 注意事项

{cautions}

## 常见问题

{faq}

## 涉及接口

{involved_apis}
"""

# ====================================================================
# 字段说明
# ====================================================================

FIELD_SPEC = {
    # --- frontmatter ---
    "id":            "唯一标识，格式: {project}::flow::{flow_name}",
    "project":       "项目标识",
    "system":        "所属系统",
    "type":          "固定为 flow，区别于 api 类型文档",
    "flow_name":     "流程标识，如 create_book、publish_chapter",
    "category":      "流程分类，如 内容管理、用户管理、运营活动",
    "role":          "适用角色，如 管理员、作者、普通用户",
    "difficulty":    "操作难度：简单/中等/复杂",
    "tags":          "语义标签",
    "aliases":       "用户可能的问法，如 '怎么发布书籍'、'上架流程'",
    "related_flows": "关联流程",
    "updated_at":    "最后更新日期",

    # --- 正文 ---
    "title":            "流程标题，如 '发布新书籍'",
    "description":      "一段话说明这个流程是干什么的、适用什么场景",
    "role_description": "什么角色在什么场景下会用到这个流程",
    "prerequisites":    "前置条件：需要什么权限、要先完成什么操作",
    "steps":            "操作步骤，每步包含：做什么 → 调什么接口 → 预期结果",
    "cautions":         "注意事项：容易踩坑的点、限制条件",
    "faq":              "常见问题与解答",
    "involved_apis":    "涉及接口列表",
}

# ====================================================================
# 示例
# ====================================================================

EXAMPLE = """---
id: "authorplatform::flow::create_and_publish_book"
project: authorplatform
system: 作家后台
type: flow
flow_name: create_and_publish_book
category: 内容管理
role: 管理员
difficulty: 中等
tags:
  - 书籍
  - 发布
  - 上架
  - 新书
aliases:
  - 怎么发布一本新书
  - 新书上架流程
  - 添加书籍并发布
  - 创建书籍的步骤
related_flows:
  - 审核书籍
  - 绑定作者
updated_at: 2026-07-06
---

# 创建并发布新书籍

从零开始在系统中创建一本新书，完成信息填写、审核、签约直到正式上架的完整流程。

## 适用角色

管理后台管理员。当收到新书入库需求时，需要通过管理后台完成从创建到上架的全部操作。

## 前置条件

- 已登录管理后台（拥有有效的 JWT token）
- 拥有「书籍管理」权限
- 已确认作者信息存在于系统中（如果是新作者，需先走「添加作者」流程）

## 操作步骤

### 第 1 步：创建书籍基本信息

**操作**：进入书籍管理 → 点击「添加书籍」→ 填写书籍信息表单

**调用接口**：`POST /admin/book/add`

**需要填写**：
- 书名（必填）
- 作者（必填，从已有作者中选择）
- 分类（必填）
- 简介
- 封面图

**结果**：书籍创建成功，状态为「待审核」

---

### 第 2 步：提交审核

**操作**：在书籍列表找到刚创建的书 → 点击「提交审核」

**调用接口**：`POST /admin/book/submit_audit`

**结果**：书籍状态变为「审核中」，审核人员会收到通知

---

### 第 3 步：审核通过

**操作**：审核人员在审核列表中 → 查看书籍详情 → 点击「通过」

**调用接口**：`POST /admin/book/audit`

**结果**：书籍状态变为「待作者签约」

---

### 第 4 步：作者签约确认

**操作**：作者在作者端收到签约通知 → 确认签约

**调用接口**：`POST /author/book/sign`

**结果**：书籍状态变为「已签约」，可以正式上架

---

### 第 5 步：上架书籍

**操作**：回到管理后台 → 书籍列表 → 点击「上架」

**调用接口**：`POST /admin/book/publish`

**结果**：书籍正式上架，用户可以在前台看到

## 注意事项

- 书名不能重复，创建前建议先搜索确认
- 审核被拒后需要修改信息重新提交，不能直接上架
- 作者签约有 7 天有效期，超期需要重新发起
- 上架前确保封面图和简介已完善，上架后修改需要重新审核

## 常见问题

**Q: 书籍创建后发现信息填错了怎么办？**
A: 在「待审核」状态下可以直接编辑（`POST /admin/book/edit`），提交审核后则需要先撤回再修改。

**Q: 审核被拒后怎么处理？**
A: 查看拒绝原因（`GET /admin/book/audit/list`），修改对应信息后重新提交审核。

**Q: 作者迟迟不签约怎么办？**
A: 可以通过系统发送催签通知，或联系作者线下确认后由管理员代操作。

## 涉及接口

| 步骤 | 接口 | 说明 |
|------|------|------|
| 创建书籍 | POST /admin/book/add | 填写基本信息创建书籍 |
| 提交审核 | POST /admin/book/submit_audit | 将书籍提交审核 |
| 审核操作 | POST /admin/book/audit | 审核通过/拒绝 |
| 作者签约 | POST /author/book/sign | 作者确认签约 |
| 上架 | POST /admin/book/publish | 正式上架书籍 |
| 编辑书籍 | POST /admin/book/edit | 修改书籍信息 |
| 查看审核记录 | GET /admin/book/audit/list | 查看审核历史和拒绝原因 |
"""
