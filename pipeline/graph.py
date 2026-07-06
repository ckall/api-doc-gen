"""
LangGraph Pipeline：Graph 定义和编排
"""

import json

from langgraph.graph import StateGraph, END

from .state import PipelineState, PipelineConfig
from .nodes import analyze_batch
from .review import human_review, render_batch, render_overview_node


# ============================================================
# 辅助节点
# ============================================================

def load_manifest(state: PipelineState) -> dict:
    """加载 manifest 文件"""
    config = PipelineConfig(**state["config"]) if isinstance(state["config"], dict) else state["config"]

    import os
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    manifest_path = os.path.join(script_dir, config.manifest_path)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    return {
        "manifest": manifest,
        "current_batch_index": 0,
        "enriched_entries": [],
        "approved_entries": [],
        "generated_files": [],
        "errors": [],
        "status": f"加载完成，共 {len(manifest)} 个接口",
    }


def prepare_batch(state: PipelineState) -> dict:
    """准备下一批次的接口"""
    config = PipelineConfig(**state["config"]) if isinstance(state["config"], dict) else state["config"]
    manifest = state["manifest"]
    batch_idx = state.get("current_batch_index", 0)
    batch_size = config.batch_size

    start = batch_idx * batch_size
    end = start + batch_size
    batch = manifest[start:end]

    return {
        "current_batch": batch,
        "status": f"准备批次 {batch_idx + 1}，接口 {start + 1}-{min(end, len(manifest))}/{len(manifest)}",
    }


def advance_batch(state: PipelineState) -> dict:
    """推进到下一批次"""
    return {
        "current_batch_index": state.get("current_batch_index", 0) + 1,
    }


# ============================================================
# 条件路由
# ============================================================

def should_continue(state: PipelineState) -> str:
    """判断是否还有下一批次"""
    # 人工中止
    if state.get("human_decision") == "abort":
        return "finish"

    config = PipelineConfig(**state["config"]) if isinstance(state["config"], dict) else state["config"]
    manifest = state["manifest"]
    batch_idx = state.get("current_batch_index", 0)
    batch_size = config.batch_size

    # 还有更多
    if (batch_idx + 1) * batch_size < len(manifest):
        return "next_batch"
    else:
        return "finish"


# ============================================================
# 构建 Graph
# ============================================================

def build_graph() -> StateGraph:
    """构建 LangGraph Pipeline"""
    graph = StateGraph(PipelineState)

    # 添加节点
    graph.add_node("load_manifest", load_manifest)
    graph.add_node("prepare_batch", prepare_batch)
    graph.add_node("analyze_batch", analyze_batch)
    graph.add_node("human_review", human_review)
    graph.add_node("render_batch", render_batch)
    graph.add_node("advance_batch", advance_batch)
    graph.add_node("render_overview", render_overview_node)

    # 设置入口
    graph.set_entry_point("load_manifest")

    # 边
    graph.add_edge("load_manifest", "prepare_batch")
    graph.add_edge("prepare_batch", "analyze_batch")
    graph.add_edge("analyze_batch", "human_review")
    graph.add_edge("human_review", "render_batch")
    graph.add_edge("render_batch", "advance_batch")

    # 条件边：advance_batch 后决定继续还是生成 overview
    graph.add_conditional_edges(
        "advance_batch",
        should_continue,
        {
            "next_batch": "prepare_batch",
            "finish": "render_overview",
        },
    )

    graph.add_edge("render_overview", END)

    return graph
