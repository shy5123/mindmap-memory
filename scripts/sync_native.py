#!/usr/bin/env python3
"""从 MEMORY.md 增量导入原生记忆到记忆树。

便捷封装，等同于: python3 mindmap_memory.py sync
"""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from mindmap_memory import MindMapStore

store = MindMapStore()
store.load(auto_decay=False)
count = store.sync_from_native()
if count > 0:
    store.write_index_to_md()
    print(f"✅ 已从 MEMORY.md 导入 {count} 条新记忆")
else:
    print("✅ 无需同步（无新增条目）")
