"""
LangGraph Pipeline 状态定义和配置（通用版，不限定语言）
"""

import os
from dataclasses import dataclass, field
from typing import TypedDict

import yaml


# ============================================================
# 配置
# ============================================================

@dataclass
class PipelineConfig:
    """Pipeline 运行配置"""
    # 项目信息
    project: str = ""
    system: str = ""
    service: str = ""       # 默认等于 project
    version: str = "v1"

    # 路径配置
    source_root: str = "."            # 项目源码根目录（绝对或相对路径）
    manifest_path: str = "output/api-manifest.json"
    output_dir: str = "output/docs"

    # LLM 配置
    model: str = "claude-opus-4-8"
    base_url: str = ""
    api_key: str = ""

    # 运行配置
    batch_size: int = 5               # 每批处理几个接口后暂停确认
    auto_approve: bool = False        # 是否跳过人工确认
    max_tool_rounds: int = 4          # AI 最多几轮工具调用
    concurrency: int = 1              # 批次内并发线程数

    @classmethod
    def from_yaml(cls, path: str = "config.yaml") -> "PipelineConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        return cls(
            project=data.get("project", ""),
            system=data.get("system", ""),
            service=data.get("service", "") or data.get("project", ""),
            version=data.get("version", "v1"),
            source_root=data.get("source_root", "."),
            manifest_path=data.get("manifest_file", "output/api-manifest.json"),
            output_dir=data.get("docs_dir", "output/docs"),
            model=data.get("model") or os.environ.get("OPENAI_MODEL") or "claude-opus-4-8",
            base_url=data.get("base_url") or os.environ.get("OPENAI_BASE_URL") or "",
            api_key=data.get("api_key") or os.environ.get("OPENAI_API_KEY") or "",
            batch_size=data.get("batch_size", 5),
            auto_approve=data.get("auto_approve", False),
            max_tool_rounds=data.get("max_tool_rounds", 4),
            concurrency=data.get("concurrency", 3),
        )


# ============================================================
# Graph State
# ============================================================

class PipelineState(TypedDict):
    """LangGraph 全局状态"""
    config: dict                       # PipelineConfig 序列化
    manifest: list[dict]               # 原始 manifest
    current_batch: list[dict]          # 当前批次的接口
    current_batch_index: int           # 当前批次序号
    enriched_entries: list[dict]       # AI 增强后的条目
    pending_review: list[dict]         # 待人工确认的条目
    approved_entries: list[dict]       # 已确认的条目
    generated_files: list[str]         # 已生成的文件路径
    human_decision: str                # 人工决策: approve / edit / skip / abort
    human_feedback: str                # 人工反馈内容
    errors: list[str]                  # 错误日志
    status: str                        # 当前状态描述
