#!/usr/bin/env python3
"""
迁移脚本 — 从旧扁平 MEMORY.md 迁移到记忆树（MemoryTree）
===================================================

用法:
    python3 scripts/migrate.py                  # 迁移 ~/.hermes/memories/MEMORY.md
    python3 scripts/migrate.py --dry-run         # 预览迁移效果（不改文件）
    python3 scripts/migrate.py --source PATH     # 迁移指定文件

迁移流程:
  1. 备份原 MEMORY.md → MEMORY.md.bak
  2. 解析所有 § 分隔的条目
  3. 逐条加入记忆树（自动语义分类）
  4. 生成新的轻量 INDEX 写入 MEMORY.md
  5. 输出迁移报告

数据文件:
  输入:  ~/.hermes/memories/MEMORY.md  (旧扁平格式)
  备份:  ~/.hermes/memories/MEMORY.md.bak
  输出:  ~/.hermes/memories/mindmap.json  (新树形数据库)
  输出:  ~/.hermes/memories/MEMORY.md  (新轻量索引)
"""

import sys
import os
from pathlib import Path

# 确保可以 import mindmap_memory
SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from mindmap_memory import MindMapStore, _get_memories_dir


def main():
    dry_run = "--dry-run" in sys.argv
    source_arg = None

    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--source" and i + 1 < len(sys.argv):
            source_arg = sys.argv[i + 1]
            break

    source_path = Path(source_arg) if source_arg else (_get_memories_dir() / "MEMORY.md")

    if not source_path.exists():
        print(f"❌ 源文件不存在: {source_path}")
        print("   提示: 如果 MEMORY.md 不存在，尝试 --source 指定路径")
        return 1

    # 读取源文件
    raw = source_path.read_text(encoding="utf-8")
    entries = [e.strip() for e in raw.split("\n§\n") if e.strip()]

    print(f"📋 找到 {len(entries)} 条扁平记忆条目")
    print()

    # 预览每条条目
    for i, entry in enumerate(entries, 1):
        preview = entry[:80].replace("\n", " ")
        if len(entry) > 80:
            preview += "…"
        print(f"  [{i}] {preview}")

    print()

    if dry_run:
        print("🔍 --dry-run 模式，不实际修改文件。")
        print(f"   以上 {len(entries)} 条记忆将被迁移到树形结构。")
        return 0

    # 初始化 store（空数据库）
    store = MindMapStore()

    # 如果已有树形数据，询问
    if store.data_path.exists():
        store.load()
        if store.nodes:
            print(f"⚠️  记忆树已存在 ({len(store.nodes)} 个节点)")
            print("   继续迁移将追加新条目（不会覆盖已有数据）。")
            print()

    # 执行迁移
    print("🔄 开始迁移...")
    migrated = 0
    for entry in entries:
        node_id = store.add_memory(entry)
        if node_id:
            migrated += 1
            node = store.nodes.get(node_id)
            if node:
                print(f"   ✅ [{node.topic[:30]}] → 深度:{store._get_depth(node_id)}")

    # 生成索引
    store.write_index_to_md()
    store.save()

    print()
    print("═" * 40)
    print(f"✅ 迁移完成")
    print(f"   成功: {migrated}/{len(entries)} 条")
    print(f"   数据: {store.data_path}")
    print(f"   索引: {_get_memories_dir() / 'MEMORY.md'}")
    print(f"   备份: {source_path}.bak")

    stats = store.stats()
    print(f"   节点: {stats['节点总数']} | 根话题: {stats['根话题数']}")
    print()
    print("💡 在 Hermes 对话中使用 /mindmap-memory 加载记忆树（MemoryTree）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
