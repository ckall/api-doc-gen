"""
LangGraph 节点：AI 多轮分析源码，增强接口文档

设计思路：
- 不硬编码任何语言的解析逻辑
- 给 AI 提供"读文件"和"列目录"两个工具
- AI 自己决定要读什么，读几轮
- 适用于任何语言的项目
"""

import os
import json
from pathlib import Path
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import tool

from .state import PipelineState, PipelineConfig


# ============================================================
# Prompts
# ============================================================

SYSTEM_PROMPT = """你是一个 API 文档分析助手。你的任务是分析项目源码，为接口生成**代码级精确**的知识库文档。

你有两个工具可用：
1. read_file: 读取项目中的文件内容（可指定行范围）
2. list_directory: 列出目录下的文件

工作流程：
1. 我会给你接口的基本信息、中间件、handler 源码和项目目录结构
2. 如果 handler 源码已经够用，直接分析输出
3. 如果需要看 service 层逻辑，**必须用工具读取**，不要猜

输出要求（最后一轮直接返回 JSON，不要用 markdown 代码块）：
{
  "enriched_summary": "比 swagger 更准确的一句话描述",
  "business_logic": "完整逻辑描述，格式见下方",
  "sub_calls": [
    {
      "method": "包名.方法名 (如 BookService.Create)",
      "file": "文件路径 (如 service/book/create.go)",
      "input": "入参说明 (如 ctx, bookName string, authorID int64)",
      "output": "返回值说明 (如 *Book, error)",
      "logic": "这个方法内部做了什么，精确到字段级别",
      "db_operations": [
        {"table": "库名.表名", "action": "insert/update/delete/select", "fields": "操作了哪些字段", "condition": "where 条件"}
      ],
      "mq_operations": [
        {"action": "produce/consume", "topic_source": "配置key或写死值", "topic_config_key": "配置文件中的key路径(如 kafka.topics.book_created)", "event": "事件名", "payload_fields": "消息体包含哪些字段"}
      ],
      "http_calls": [
        {"method": "GET/POST", "url_source": "配置key或写死值", "url_config_key": "配置中的key路径", "description": "调用目的"}
      ],
      "config_reads": [
        {"key": "配置key路径(如 book.max_title_length)", "config_file": "配置文件路径", "usage": "用来干什么"}
      ],
      "hardcoded_values": [
        {"value": "写死的具体值", "field": "用在哪个字段", "context": "上下文说明"}
      ]
    }
  ],
  "data_dependencies": {
    "database": [{"table": "库名.表名", "operation": "read/write/update/delete", "fields": "涉及字段", "description": "做了什么"}],
    "mq_produce": [{"topic_config_key": "配置key路径", "event": "事件名", "trigger": "什么条件下发送", "payload_fields": "消息体关键字段"}],
    "mq_consume": [{"topic_config_key": "配置key路径", "event": "事件名", "action": "收到后做什么"}],
    "http_calls": [{"method": "GET/POST", "url_config_key": "配置key路径或写死的URL", "description": "调用目的"}],
    "config_keys": [{"key": "完整配置key路径", "config_file": "配置文件相对路径", "usage": "用途说明"}]
  },
  "business_constraints": ["约束条件，标注值来源：写死/配置/入参"],
  "error_codes": [{"code": "xxx", "message": "xxx", "suggestion": "xxx"}],
  "related_apis": ["关联接口路径"],
  "tags": ["3-5个语义标签"],
  "aliases": ["2-4个用户可能的问法"],
  "notes": "其他值得注意的信息"
}

## business_logic 写作要求（代码级精确）

拆解到每个子方法调用，每个方法内精确到字段级操作：

好的示例：
```
1. 从 JWT middleware 获取管理员信息 (admin_id, wechat_uuid)
2. 参数绑定 & 校验
   - book_name: string, 必填, 长度 ≤ 50（写死在 validator tag）
   - author_id: int64, 必填
   - category_id: int64, 必填
3. 调用 BookService.CheckDuplicate(ctx, bookName string) → (bool, error)
   - SELECT COUNT(*) FROM book_db.books WHERE book_name = ? AND deleted_at IS NULL
   - 如果存在返回错误 "书名已存在"
4. 调用 BookService.Create(ctx, req *CreateBookReq) → (*Book, error)
   - 入参: CreateBookReq{BookName, AuthorID, CategoryID, CreatedBy}
   - INSERT INTO book_db.books: book_name, author_id, category_id, status=1(写死,待审核), created_by=admin_id
   - INSERT INTO book_db.book_authors: book_id, author_id, bindtype=1(写死,主作者)
   - 返回: Book{ID, BookName, Status, CreatedAt}
5. 调用 MQProducer.Send(ctx, topic, event, payload)
   - topic 来自配置: kafka.topics.book_created (配置文件: configs/kafka.yaml)
   - event: "book.created"
   - payload: {book_id, author_id, book_name, created_by, created_at}
6. 判断是否推送内容库
   - 读取配置: push.content_library.enabled (配置文件: configs/application.yaml)
   - 如果 enabled=true: POST content-service/api/v1/books/sync
     - URL 来自配置: services.content_library.base_url (配置文件: configs/application.yaml)
     - body: {book_id, book_name, author_name, category}
7. 返回 {code: 0, data: {book_id: xxx}}
```

## 关键原则

1. **字段级精确**: 不说"修改状态"，说"UPDATE books SET status=2 WHERE book_uid=?"
2. **值来源标注**: 每个值都标注来源——写死(hardcoded)/配置(config key)/入参(param)/计算(computed)
3. **配置只记key不记值**: 配置是分环境的，文档里只记录 key 路径和配置文件位置
4. **子方法完整签名**: 写清入参类型和返回值类型
5. **条件分支写清楚**: if/else 的条件和各分支走向

## 注意

- 必须从源码中实际看到才写，不要猜测
- 如果 handler 调用了 service 方法但你没看到实现，用工具 read_file 去读
- data_dependencies.database 中的 table 必须用「库名.表名」格式
- config_keys 中记录所有被读取的配置，标注配置文件路径
- 不要超过 3 轮工具调用，信息够了就直接输出结果"""


ANALYSIS_PROMPT_TEMPLATE = """## 待分析接口

- 方法: {method}
- 路径: {path}
- 描述: {summary}
- 模块: {module}
- Handler: {handler}
- Handler 文件: {handler_file}
- 认证/中间件: {middlewares}

## 已知参数

{parameters_text}

## 已知响应字段

{response_text}

## Handler 源码

```
{handler_source}
```

## 项目目录结构

```
{directory_tree}
```

请分析这个接口。如果 handler 源码已经提供且信息充分，可以直接输出 JSON 结果。
如果需要更多上下文（如 service 层逻辑），可以使用工具读取相关文件。"""


# ============================================================
# 工具定义
# ============================================================

def make_tools(source_root: str):
    """创建绑定了项目根目录的工具函数"""

    @tool
    def read_file(file_path: str, start_line: int = 0, end_line: int = 0) -> str:
        """读取项目中的文件内容。
        
        Args:
            file_path: 相对于项目根目录的文件路径
            start_line: 起始行号（0表示从头开始）
            end_line: 结束行号（0表示读到文件末尾，最多200行）
        """
        full_path = os.path.join(source_root, file_path)
        
        if not os.path.isfile(full_path):
            return f"文件不存在: {file_path}"
        
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            
            total = len(lines)
            start = max(0, start_line)
            end = end_line if end_line > 0 else min(total, start + 200)
            end = min(end, start + 200)  # 单次最多200行
            
            content = "".join(lines[start:end])
            
            if end < total:
                content += f"\n// ... 文件共 {total} 行，已截取 {start+1}-{end} 行"
            
            return content
        except Exception as e:
            return f"读取失败: {e}"

    @tool
    def list_directory(dir_path: str) -> str:
        """列出项目中某个目录下的文件和子目录。
        
        Args:
            dir_path: 相对于项目根目录的目录路径
        """
        full_path = os.path.join(source_root, dir_path)
        
        if not os.path.isdir(full_path):
            return f"目录不存在: {dir_path}"
        
        try:
            items = []
            for item in sorted(os.listdir(full_path)):
                if item.startswith("."):
                    continue
                item_path = os.path.join(full_path, item)
                if os.path.isdir(item_path):
                    items.append(f"📁 {item}/")
                else:
                    size = os.path.getsize(item_path)
                    items.append(f"📄 {item} ({size} bytes)")
            
            if not items:
                return "空目录"
            return "\n".join(items[:50])  # 最多50个条目
        except Exception as e:
            return f"列目录失败: {e}"

    return [read_file, list_directory]


# ============================================================
# 工具函数
# ============================================================

def get_directory_tree(source_root: str, max_depth: int = 2) -> str:
    """生成项目的简化目录树"""
    tree_lines = []
    root_path = Path(source_root)
    
    skip_dirs = {
        "node_modules", ".git", "vendor", "dist", "build",
        "__pycache__", ".idea", ".vscode", "target", "bin",
        "output", "uploads", "images", ".cache",
    }
    
    def walk(path: Path, prefix: str, depth: int):
        if depth > max_depth:
            return
        
        try:
            items = sorted(path.iterdir())
        except PermissionError:
            return
        
        dirs = [i for i in items if i.is_dir() and i.name not in skip_dirs and not i.name.startswith(".")]
        files = [i for i in items if i.is_file() and not i.name.startswith(".")]
        
        # 只显示目录和关键文件
        for d in dirs:
            tree_lines.append(f"{prefix}📁 {d.name}/")
            walk(d, prefix + "  ", depth + 1)
        
        # 文件只在第一层和第二层显示
        if depth <= 1:
            for f in files[:20]:
                tree_lines.append(f"{prefix}📄 {f.name}")
            if len(files) > 20:
                tree_lines.append(f"{prefix}... 还有 {len(files) - 20} 个文件")
    
    walk(root_path, "", 0)
    return "\n".join(tree_lines[:100])  # 限制总行数


def format_parameters(parameters: list[dict]) -> str:
    """格式化参数列表"""
    if not parameters:
        return "无参数"
    lines = []
    for p in parameters:
        required = "必填" if p.get("required") else "可选"
        lines.append(f"- {p.get('name', '')} ({p.get('type', 'any')}, {required}): {p.get('description', '')}")
    return "\n".join(lines)


def format_response(fields: list[dict]) -> str:
    """格式化响应字段"""
    if not fields:
        return "通用响应格式"
    lines = []
    for f in fields[:15]:
        lines.append(f"- {f.get('name', '')} ({f.get('type', 'any')}): {f.get('description', '')}")
    if len(fields) > 15:
        lines.append(f"... 还有 {len(fields) - 15} 个字段")
    return "\n".join(lines)


# ============================================================
# 核心分析函数
# ============================================================

def analyze_single_api(entry: dict, llm: ChatOpenAI, tools: list, source_root: str) -> dict:
    """用多轮工具调用分析单个接口"""
    
    # 构建初始 prompt
    directory_tree = get_directory_tree(source_root)
    
    prompt_text = ANALYSIS_PROMPT_TEMPLATE.format(
        method=entry["method"],
        path=entry["path"],
        summary=entry.get("summary", ""),
        module=entry.get("module", ""),
        handler=entry.get("handler", ""),
        handler_file=entry.get("handler_file", ""),
        middlewares=", ".join(entry.get("middlewares", [])) or "无",
        parameters_text=format_parameters(entry.get("parameters", [])),
        response_text=format_response(entry.get("response_fields", [])),
        handler_source=entry.get("handler_source", "") or "// 未找到源码",
        directory_tree=directory_tree,
    )

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt_text),
    ]

    # 绑定工具
    llm_with_tools = llm.bind_tools(tools)
    
    # 多轮对话（最多 4 轮，防止无限循环）
    max_rounds = 4
    response = None
    for round_num in range(max_rounds):
        response = llm_with_tools.invoke(messages)
        messages.append(response)
        
        # 如果没有工具调用，说明 AI 准备输出最终结果了
        if not response.tool_calls:
            break
        
        # 执行工具调用
        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            
            # 找到对应的工具并执行
            tool_result = ""
            for t in tools:
                if t.name == tool_name:
                    tool_result = t.invoke(tool_args)
                    break
            
            messages.append(ToolMessage(
                content=str(tool_result),
                tool_call_id=tool_call["id"],
            ))
    
    # 如果最后一轮仍在 tool_calls 或 content 为空，强制让 AI 输出 JSON
    final_content = (response.content or "").strip() if response else ""
    
    if not final_content or final_content.startswith("I'll") or final_content.startswith("Let me"):
        # AI 还没输出最终结果，追加一条强制要求
        messages.append(HumanMessage(
            content="请直接输出 JSON 结果，不要再调用工具。基于你已经获取的信息生成最终的增强文档 JSON。"
        ))
        # 用不带 tools 的 llm 调用，避免再次 tool call
        response = llm.invoke(messages)
        final_content = (response.content or "").strip()
    
    # 清理 markdown 代码块标记
    if "```json" in final_content:
        final_content = final_content.split("```json", 1)[1]
        final_content = final_content.split("```", 1)[0]
    elif "```" in final_content:
        parts = final_content.split("```")
        if len(parts) >= 3:
            final_content = parts[1]
        elif len(parts) == 2:
            final_content = parts[1]
    
    final_content = final_content.strip()
    
    # 尝试从内容中找到 JSON 对象
    if not final_content.startswith("{"):
        # 可能前面有文字说明，找到第一个 { 
        brace_idx = final_content.find("{")
        if brace_idx >= 0:
            final_content = final_content[brace_idx:]
            # 找到最后一个 }
            last_brace = final_content.rfind("}")
            if last_brace >= 0:
                final_content = final_content[:last_brace + 1]
    
    if not final_content:
        raise json.JSONDecodeError("AI 返回内容为空", "", 0)
    
    # 解析 JSON
    # AI 返回的 JSON 字符串值中可能包含未转义的控制字符（换行、tab等）
    # strict=False 允许字符串中包含控制字符
    try:
        ai_result = json.loads(final_content, strict=False)
    except json.JSONDecodeError:
        # 兜底：替换所有控制字符后重试
        import re
        cleaned = re.sub(r'[\x00-\x1f\x7f]', lambda m: {
            '\n': '\\n', '\r': '\\r', '\t': '\\t'
        }.get(m.group(), ''), final_content)
        ai_result = json.loads(cleaned)
    return ai_result


# ============================================================
# 节点函数
# ============================================================

def analyze_batch(state: PipelineState) -> dict:
    """AI 分析当前批次的接口（支持并发）"""
    config = PipelineConfig(**state["config"]) if isinstance(state["config"], dict) else state["config"]
    batch = state["current_batch"]

    if not batch:
        return {"status": "批次为空，跳过", "pending_review": []}

    # 初始化 LLM
    llm_kwargs = {
        "model": config.model,
        "temperature": 0.1,
    }
    if config.base_url:
        llm_kwargs["base_url"] = config.base_url
    if config.api_key:
        llm_kwargs["api_key"] = config.api_key

    # 确定项目根目录
    source_root = config.source_root
    if not os.path.isabs(source_root):
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        source_root = os.path.normpath(os.path.join(script_dir, source_root))

    # 创建工具
    tools = make_tools(source_root)

    errors = state.get("errors", [])
    concurrency = getattr(config, "concurrency", 3)

    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    console = Console()
    errors_lock = threading.Lock()

    def process_single(entry: dict) -> dict:
        """单个接口的分析（线程安全）"""
        # 每个线程创建自己的 LLM 实例
        llm = ChatOpenAI(**llm_kwargs)
        try:
            ai_result = analyze_single_api(entry, llm, tools, source_root)
            return {**entry, **ai_result, "_status": "ok"}
        except json.JSONDecodeError as e:
            with errors_lock:
                errors.append(f"[{entry['id']}] JSON 解析失败: {e}")
            return {
                **entry,
                "enriched_summary": entry.get("summary", ""),
                "business_logic": "",
                "data_dependencies": {},
                "business_constraints": [],
                "error_codes": [],
                "related_apis": [],
                "tags": [entry.get("tag", "")],
                "aliases": [entry.get("summary", "") + "接口"],
                "notes": f"AI 分析失败: {e}",
                "_status": "failed",
            }
        except Exception as e:
            with errors_lock:
                errors.append(f"[{entry['id']}] 分析异常: {e}")
            return {
                **entry,
                "enriched_summary": entry.get("summary", ""),
                "business_logic": "",
                "data_dependencies": {},
                "business_constraints": [],
                "error_codes": [],
                "related_apis": [],
                "tags": [entry.get("tag", "")],
                "aliases": [entry.get("summary", "") + "接口"],
                "notes": f"AI 分析异常: {e}",
                "_status": "failed",
            }

    enriched = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(
            f"AI 分析中（{concurrency} 并发）...",
            total=len(batch),
        )

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_entry = {
                executor.submit(process_single, entry): entry
                for entry in batch
            }

            for future in as_completed(future_to_entry):
                result = future.result()
                enriched.append(result)
                entry = future_to_entry[future]
                status_icon = "✓" if result.get("_status") == "ok" else "✗"
                progress.update(
                    task,
                    description=f"{status_icon} {entry['method']} {entry['path']}",
                )
                progress.advance(task)

    # 清理内部状态字段
    for e in enriched:
        e.pop("_status", None)

    # 按原始顺序排列（线程完成顺序可能不同）
    id_order = {entry["id"]: i for i, entry in enumerate(batch)}
    enriched.sort(key=lambda x: id_order.get(x["id"], 0))

    return {
        "pending_review": enriched,
        "errors": errors,
        "status": f"AI 分析完成，{len(enriched)} 个接口待确认",
    }
