#!/usr/bin/env python3
"""
记忆树（MemoryTree）原生工具 — Hermes Agent 一等公民 API

注册六个工具：memory_tree_add, memory_tree_search, memory_tree_sync, memory_tree_replace, memory_tree_remove, memory_tree_recover
不再需要通过 terminal() 执行脚本，直接函数调用。
"""

import json
import sys
from pathlib import Path

from tools.registry import registry

# 确保 mindmap_memory 模块可被导入
_SKILL_DIR = Path("~/.hermes/skills/custom/mindmap-memory").expanduser()
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))


def _get_store():
    """获取 MindMapStore 实例，自动 load。"""
    from mindmap_memory import MindMapStore
    store = MindMapStore()
    store.load(auto_decay=True)
    return store


def memory_tree_add(content: str) -> str:
    """添加一条记忆到记忆树，自动语义分类。

    Args:
        content: 要记住的内容

    Returns:
        JSON: {"success": true, "node_id": "...", "topic": "..."}
    """
    if not content or not content.strip():
        return json.dumps({"success": False, "error": "内容不能为空"}, ensure_ascii=False)

    try:
        store = _get_store()
        node_id = store.add_memory(content.strip())
        store.write_index_to_md()
        node = store.nodes.get(node_id)
        topic = node.topic if node else ""
        return json.dumps({
            "success": True,
            "node_id": node_id,
            "topic": topic,
            "message": f"已记入记忆树: {topic}",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


def memory_tree_search(query: str) -> str:
    """在记忆树中检索相关内容。

    Args:
        query: 检索关键词

    Returns:
        JSON: {"success": true, "results": [{"topic": "...", "content": "...", "score": N}, ...]}
    """
    if not query or not query.strip():
        return json.dumps({"success": False, "error": "查询不能为空"}, ensure_ascii=False)

    try:
        store = _get_store()
        results = store.search(query.strip())
        return json.dumps({
            "success": True,
            "count": len(results),
            "results": [
                {
                    "topic": n.topic,
                    "content": n.content[:200] if n.content else "",
                    "score": n.score,
                    "category": n.score_category(),
                    "is_core": n.is_core,
                }
                for n in results[:10]
            ],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


def memory_tree_sync() -> str:
    """从 MEMORY.md 增量导入原生 memory 工具新增的条目。

    Returns:
        JSON: {"success": true, "imported": N, "message": "..."}
    """
    try:
        store = _get_store()
        count = store.sync_from_native()
        if count > 0:
            store.write_index_to_md()
        return json.dumps({
            "success": True,
            "imported": count,
            "message": f"已从 MEMORY.md 导入 {count} 条新记忆" if count else "无需同步（无新增条目）",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


def memory_tree_replace(search_text: str, new_content: str) -> str:
    """按内容子串查找并替换已有记忆。

    在话题和内容中搜索 search_text，找到唯一匹配后替换为新内容。
    多个匹配时返回候选列表。

    Args:
        search_text: 用于查找的文本片段
        new_content: 替换后的新内容

    Returns:
        JSON: {"success": true/false, "replaced": N, "message": "...", ...}
    """
    if not search_text or not search_text.strip():
        return json.dumps({"success": False, "error": "search_text 不能为空"}, ensure_ascii=False)
    if not new_content or not new_content.strip():
        return json.dumps({"success": False, "error": "new_content 不能为空"}, ensure_ascii=False)

    try:
        store = _get_store()
        result = store.replace_memory(search_text.strip(), new_content.strip())
        if result.get("success"):
            store.write_index_to_md()
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


def memory_tree_remove(search_text: str, force: bool = False) -> str:
    """按内容子串查找并删除记忆。

    删除时会级联删除子节点，核心记忆需要 force=True。
    删除的记录写入遗忘日志，可后悔恢复。

    Args:
        search_text: 用于查找的文本片段
        force: 是否强制删除（跳过核心保护）

    Returns:
        JSON: {"success": true/false, "removed": N, "message": "...", ...}
    """
    if not search_text or not search_text.strip():
        return json.dumps({"success": False, "error": "search_text 不能为空"}, ensure_ascii=False)

    try:
        store = _get_store()
        result = store.remove_memory(search_text.strip(), force=force)
        if result.get("success"):
            store.write_index_to_md()
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


def memory_tree_recover(search_text: str = "") -> str:
    """恢复被软删除的记忆。

    不加参数时列出最近被删的节点。
    提供 search_text 时搜索并恢复匹配项。

    Args:
        search_text: 可选，匹配要恢复的节点内容

    Returns:
        JSON: {"success": true/false, "recovered": N, "message": "...", ...}
    """
    try:
        store = _get_store()
        result = store.recover_memory(search_text.strip())
        if result.get("success") and result.get("recovered", 0) > 0:
            store.write_index_to_md()
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


def check_requirements() -> bool:
    """检查 mindmap_memory 模块是否可用。"""
    try:
        from mindmap_memory import MindMapStore
        return True
    except ImportError:
        return False


# ── 注册三个工具 ──

registry.register(
    name="memory_tree_add",
    toolset="memorytree",
    schema={
        "name": "memory_tree_add",
        "description": "添加一条记忆到记忆树（MemoryTree）。记忆树会自动按话题分类、支持遗忘衰减、核心保护。内容会被自动语义匹配到已有话题树中。",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要记住的内容。会按 § 分隔符拆分为条目，自动分类到话题树中。"
                }
            },
            "required": ["content"]
        }
    },
    handler=lambda args, **kw: memory_tree_add(
        content=args.get("content", "")
    ),
    check_fn=check_requirements,
    requires_env=[],
)

registry.register(
    name="memory_tree_search",
    toolset="memorytree",
    schema={
        "name": "memory_tree_search",
        "description": "在记忆树中检索相关内容。逐层下钻检索，返回最匹配的节点及其内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词，支持中文和英文。"
                }
            },
            "required": ["query"]
        }
    },
    handler=lambda args, **kw: memory_tree_search(
        query=args.get("query", "")
    ),
    check_fn=check_requirements,
    requires_env=[],
)

registry.register(
    name="memory_tree_sync",
    toolset="memorytree",
    schema={
        "name": "memory_tree_sync",
        "description": "从 MEMORY.md 增量导入原生 memory 工具新增的条目到记忆树。解决两个记忆系统并存时的同步问题。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    handler=lambda args, **kw: memory_tree_sync(),
    check_fn=check_requirements,
    requires_env=[],
)

registry.register(
    name="memory_tree_replace",
    toolset="memorytree",
    schema={
        "name": "memory_tree_replace",
        "description": "替换记忆树中的已有记忆。按 search_text 在话题和内容中查找，找到唯一匹配后替换为新内容。多个匹配时返回候选列表以便细化。",
        "parameters": {
            "type": "object",
            "properties": {
                "search_text": {
                    "type": "string",
                    "description": "用于查找的文本片段，在节点话题和内容中进行子串匹配。"
                },
                "new_content": {
                    "type": "string",
                    "description": "替换后的新内容。"
                }
            },
            "required": ["search_text", "new_content"]
        }
    },
    handler=lambda args, **kw: memory_tree_replace(
        search_text=args.get("search_text", ""),
        new_content=args.get("new_content", "")
    ),
    check_fn=check_requirements,
    requires_env=[],
)

registry.register(
    name="memory_tree_remove",
    toolset="memorytree",
    schema={
        "name": "memory_tree_remove",
        "description": "删除记忆树中的记忆。按 search_text 匹配后删除节点及其子节点（核心子节点提升为根节点）。核心记忆需要 force=true。删除记录写入遗忘日志，可后悔恢复。",
        "parameters": {
            "type": "object",
            "properties": {
                "search_text": {
                    "type": "string",
                    "description": "用于查找的文本片段，在节点话题和内容中进行子串匹配。"
                },
                "force": {
                    "type": "boolean",
                    "description": "是否强制删除核心记忆。默认 false。"
                }
            },
            "required": ["search_text"]
        }
    },
    handler=lambda args, **kw: memory_tree_remove(
        search_text=args.get("search_text", ""),
        force=args.get("force", False)
    ),
    check_fn=check_requirements,
    requires_env=[],
)

registry.register(
    name="memory_tree_recover",
    toolset="memorytree",
    schema={
        "name": "memory_tree_recover",
        "description": "恢复记忆树中被软删除的记忆。不加参数列出最近删除的节点（最多20条），提供 search_text 则搜索并恢复匹配项。",
        "parameters": {
            "type": "object",
            "properties": {
                "search_text": {
                    "type": "string",
                    "description": "可选，匹配要恢复的节点话题或内容。不提供则列出最近删除的节点。"
                }
            },
            "required": []
        }
    },
    handler=lambda args, **kw: memory_tree_recover(
        search_text=args.get("search_text", "")
    ),
    check_fn=check_requirements,
    requires_env=[],
)
