# 模板变量清单

自定义模板时可用的所有变量。模板语法为 [Jinja2](https://jinja.palletsprojects.com/)。

---

## 接口文档模板 (api.md.j2)

### 基础信息

| 变量 | 类型 | 说明 |
|------|------|------|
| `id` | string | 唯一标识，格式: `{project}::{METHOD}::{path}` |
| `project` | string | 项目标识 |
| `system` | string | 系统名称 |
| `service` | string | 服务名（默认等于 project） |
| `module` | string | 功能模块 |
| `group` | string | 接口分组 |
| `method` | string | HTTP 方法 (GET/POST/PUT/DELETE) |
| `path` | string | 请求路径 |
| `summary` | string | 一句话描述 |
| `version` | string | API 版本 |
| `handler` | string | Handler 函数名 |
| `handler_file` | string | Handler 文件路径 |
| `route_file` | string | 路由文件路径 |
| `title` | string | 文档标题（默认用 handler 名） |
| `updated_at` | string | 最后更新日期 |

### 列表类

| 变量 | 类型 | 说明 |
|------|------|------|
| `tags` | list[string] | 语义标签 |
| `aliases` | list[string] | 用户可能的问法 |
| `related_apis` | list[string] | 关联接口路径 |
| `business_constraints` | list[string] | 业务约束条件 |

### 参数 (parameters)

`parameters` 是一个列表，每项包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| `.name` | string | 参数名 |
| `.type` | string | 参数类型 |
| `.required` | bool | 是否必填 |
| `.description` | string | 参数说明 |
| `.in` | string | 位置：query/path/body/header |

### 业务逻辑

| 变量 | 类型 | 说明 |
|------|------|------|
| `business_logic` | string | 完整的业务逻辑描述（自然语言，按步骤） |

### 响应字段 (response_fields)

`response_fields` 是一个列表，每项包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| `.name` | string | 字段名 |
| `.type` | string | 字段类型 |
| `.description` | string | 字段说明 |

### 数据依赖 (data_dependencies)

`data_dependencies` 是一个对象，包含以下子列表：

#### data_dependencies.database[]

| 字段 | 类型 | 说明 |
|------|------|------|
| `.table` | string | 库名.表名 |
| `.operation` | string | read/write/update/delete |
| `.fields` | string | 涉及字段 |
| `.description` | string | 操作说明 |

#### data_dependencies.mq_produce[]

| 字段 | 类型 | 说明 |
|------|------|------|
| `.topic_config_key` | string | topic 的配置 key 路径 |
| `.event` | string | 事件名 |
| `.trigger` | string | 什么条件下发送 |
| `.payload_fields` | string | 消息体关键字段 |

#### data_dependencies.mq_consume[]

| 字段 | 类型 | 说明 |
|------|------|------|
| `.topic_config_key` | string | topic 的配置 key 路径 |
| `.event` | string | 事件名 |
| `.action` | string | 收到后做什么 |

#### data_dependencies.http_calls[]

| 字段 | 类型 | 说明 |
|------|------|------|
| `.method` | string | HTTP 方法 |
| `.url_config_key` | string | URL 的配置 key（或写死的 URL） |
| `.description` | string | 调用目的 |

#### data_dependencies.config_keys[]

| 字段 | 类型 | 说明 |
|------|------|------|
| `.key` | string | 配置 key 路径 |
| `.config_file` | string | 配置文件路径 |
| `.usage` | string | 用途说明 |

### 调用链 (sub_calls)

`sub_calls` 是一个列表，每项是一个子方法调用：

| 字段 | 类型 | 说明 |
|------|------|------|
| `.method` | string | 包名.方法名 |
| `.file` | string | 文件路径 |
| `.input` | string | 入参签名 |
| `.output` | string | 返回值类型 |
| `.logic` | string | 方法内部逻辑说明 |
| `.db_operations` | list | DB 操作列表 |
| `.db_operations[].table` | string | 库名.表名 |
| `.db_operations[].action` | string | insert/update/delete/select |
| `.db_operations[].fields` | string | 涉及字段 |
| `.db_operations[].condition` | string | WHERE 条件 |
| `.mq_operations` | list | MQ 操作列表 |
| `.mq_operations[].action` | string | produce/consume |
| `.mq_operations[].topic_config_key` | string | topic 配置 key |
| `.mq_operations[].event` | string | 事件名 |
| `.mq_operations[].payload_fields` | string | 消息体字段 |
| `.http_calls` | list | HTTP 外部调用 |
| `.http_calls[].method` | string | HTTP 方法 |
| `.http_calls[].url_config_key` | string | URL 配置 key |
| `.http_calls[].description` | string | 调用目的 |
| `.config_reads` | list | 配置读取 |
| `.config_reads[].key` | string | 配置 key |
| `.config_reads[].config_file` | string | 配置文件路径 |
| `.config_reads[].usage` | string | 用途 |
| `.hardcoded_values` | list | 硬编码值 |
| `.hardcoded_values[].value` | string | 写死的值 |
| `.hardcoded_values[].field` | string | 用在哪个字段 |
| `.hardcoded_values[].context` | string | 上下文说明 |

### 错误码 (error_codes)

| 字段 | 类型 | 说明 |
|------|------|------|
| `.code` | string | 错误码 |
| `.message` | string | 错误说明 |
| `.suggestion` | string | 处理建议 |

### 其他

| 变量 | 类型 | 说明 |
|------|------|------|
| `notes` | string | 备注信息 |

---

## 流程文档模板 (flow.md.j2)

### 基础信息

| 变量 | 类型 | 说明 |
|------|------|------|
| `id` | string | 唯一标识，格式: `{project}::flow::{flow_name}` |
| `project` | string | 项目标识 |
| `system` | string | 系统名称 |
| `flow_name` | string | 流程标识 |
| `title` | string | 流程标题 |
| `category` | string | 流程分类 |
| `role` | string | 适用角色 |
| `difficulty` | string | 难度：简单/中等/复杂 |
| `description` | string | 流程描述 |
| `role_description` | string | 角色和场景说明 |
| `updated_at` | string | 最后更新日期 |

### 列表类

| 变量 | 类型 | 说明 |
|------|------|------|
| `tags` | list[string] | 语义标签 |
| `aliases` | list[string] | 用户可能的问法 |
| `related_flows` | list[string] | 关联流程 |
| `prerequisites` | list[string] | 前置条件 |
| `cautions` | list[string] | 注意事项 |

### 操作步骤 (steps)

`steps` 是一个列表，每项是一个操作步骤：

| 字段 | 类型 | 说明 |
|------|------|------|
| `.order` | int | 步骤序号 |
| `.title` | string | 步骤标题 |
| `.action` | string | 用户操作描述 |
| `.api_method` | string | 调用的接口 HTTP 方法 |
| `.api_path` | string | 调用的接口路径 |
| `.doc_link` | string | 接口文档相对链接路径 |
| `.key_params` | list[string] | 关键参数 |
| `.result` | string | 预期结果 |
| `.notes` | string | 补充说明 |

### 常见问题 (faq)

`faq` 是一个列表，每项包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| `.question` | string | 问题 |
| `.answer` | string | 回答 |

---

## Jinja2 常用语法

```jinja2
{# 注释 #}
{{ variable }}                     {# 输出变量 #}
{% if variable %}...{% endif %}    {# 条件判断 #}
{% for item in list %}...{% endfor %}  {# 循环 #}
{{ value | default("默认值") }}    {# 默认值过滤器 #}
{{ text | replace("\n", " ") }}    {# 替换 #}
```

完整语法参考：https://jinja.palletsprojects.com/en/3.1.x/templates/
