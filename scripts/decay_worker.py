#!/usr/bin/env python3
"""
衰减工作脚本 — 定时触发记忆衰减扫描 / 记忆守护
=====================================

用法:
    python3 scripts/decay_worker.py                  # 手动触发一次衰减
    python3 scripts/decay_worker.py --dry-run         # 预览将被删除的节点
    python3 scripts/decay_worker.py --consolidate     # 手动触发记忆守护（重分类当天记忆）

建议配置 cron 定时任务（如 cronjob 工具）：
    每周日凌晨 2:00 执行: python3 decay_worker.py
    每日凌晨 3:00 执行: python3 decay_worker.py --consolidate

或在 Hermes 对话中使用:
    /mindmap-memory
    然后执行: python3 scripts/decay_worker.py
"""

import sys
import os
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from mindmap_memory import MindMapStore, _get_memories_dir


def main():
    dry_run = "--dry-run" in sys.argv
    consolidate_mode = "--consolidate" in sys.argv

    store = MindMapStore()
    if not store.data_path.exists():
        print("📭 记忆数据文件不存在。")
        return 0

    store.load(auto_decay=not consolidate_mode, auto_consolidate=False)

    if consolidate_mode:
        count = store.consolidate_today()
        if count > 0:
            print(f"🧠 记忆守护完成：{count} 个节点重新分类")
        else:
            print("🧠 记忆守护：无需重新分类")
        return 0

    if not store.nodes:
        print("📭 记忆树为空，无需衰减。")
        return 0

    print(f"📊 当前状态: {len(store.nodes)} 个节点")
    print(f"   上次衰减: {store.last_decay or '从未执行'}")

    if dry_run:
        from datetime import timedelta
        now = datetime.now()
        cutoff = now - timedelta(days=7)

        would_decay = []
        would_delete = []

        for node_id, node in store.nodes.items():
            try:
                last_access = datetime.fromisoformat(
                    node.last_access.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except (ValueError, TypeError):
                continue

            days_since = (now - last_access).days
            if days_since >= 7:
                if node.is_core:
                    if node.score > 1:
                        would_decay.append((node, days_since, node.score - 1))
                else:
                    new_score = node.score - 1
                    if new_score <= 0:
                        would_delete.append((node, days_since))
                    else:
                        would_decay.append((node, days_since, new_score))

        print(f"\n🔍 预览模式（不实际修改）:")
        print(f"   将被衰减: {len(would_decay)} 个节点")
        for node, days, new_score in would_decay[:10]:
            print(f"     - '{node.topic}' (score {node.score}→{new_score}, {days}天未访问)")
        if len(would_decay) > 10:
            print(f"     ... 及另外 {len(would_decay) - 10} 个节点")

        print(f"   将被删除: {len(would_delete)} 个节点")
        for node, days in would_delete[:10]:
            subtree_size = len(store.get_subtree(node.id))
            print(f"     - '{node.topic}' (score {node.score}, {days}天未访问, 含{subtree_size}个子节点)")
        if len(would_delete) > 10:
            print(f"     ... 及另外 {len(would_delete) - 10} 个节点")

        return 0

    print("\n🔄 执行衰减扫描...")
    removed = store.decay_if_needed()

    if removed:
        print(f"\n🗑️  已遗忘 {len(removed)} 个节点")
        decay_log_dir = store.decay_log_dir
        today = datetime.now().strftime("%Y-%m-%d")
        print(f"   遗忘日志: {decay_log_dir / f'{today}.json'}")
    else:
        print("\n✅ 无需删除任何节点")

    store.write_index_to_md()

    stats = store.stats()
    print(f"\n📊 衰减后状态:")
    print(f"   节点总数: {stats['节点总数']}")
    print(f"   短期记忆: {stats['短期记忆(1-20)']}")
    print(f"   长期记忆: {stats['长期记忆(21-40)']}")
    print(f"   永久记忆: {stats['永久记忆(41+)']}")
    print(f"   核心记忆: {stats['核心记忆']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
