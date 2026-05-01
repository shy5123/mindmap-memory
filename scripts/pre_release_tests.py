#!/usr/bin/env python3
"""
发布前补充测试：事务回滚 + API错误处理 + 同步防抖
用法: python3 scripts/pre_release_tests.py
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_DIR))

from mindmap_memory import MindMapStore

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def _open_db(path):
    """打开数据库并确保表结构。"""
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE IF NOT EXISTS nodes (
        id TEXT PRIMARY KEY, topic TEXT, content TEXT, score INTEGER DEFAULT 1,
        last_access TEXT, parent_id TEXT, children_ids TEXT, is_core INTEGER DEFAULT 0,
        deleted INTEGER DEFAULT 0, deleted_at TEXT DEFAULT ''
    )""")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    return conn


# ============================================================
# 测试 1: SQLite 事务与回滚
# ============================================================
def test_transaction_rollback():
    print("\n" + "=" * 60)
    print(" 测试 1: SQLite 事务与回滚 — 模拟写入中途崩溃")
    print("=" * 60)

    tmpdir = Path(tempfile.mkdtemp(prefix="txn_test_"))
    db_path = tmpdir / "test.db"

    # 1a: 正常写入节点A
    store = MindMapStore(data_path=db_path)
    store.load(auto_decay=False, auto_consolidate=False)
    nid_a = store.add_memory("节点A：事务测试基准数据")
    store.save()
    check("正常写入节点A", bool(nid_a))

    store2 = MindMapStore(data_path=db_path)
    store2.load(auto_decay=False, auto_consolidate=False)
    check("重新加载节点数=1", len(store2.nodes) == 1, f"实际={len(store2.nodes)}")

    # 1b: 直接操作 SQLite — 开始事务 → 写入节点B → 不commit关闭
    conn = _open_db(db_path)
    conn.execute("BEGIN")
    conn.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("b-node-id", "节点B话题", "节点B：模拟崩溃中写入的内容",
                  1, "2026-01-01T00:00:00", None, "[]", 0, 0, "", 0, ""))
    # 模拟崩溃：关闭连接但不 commit
    conn.close()  # 无 commit → 自动回滚

    # 重新加载 — 节点B不应存在
    store3 = MindMapStore(data_path=db_path)
    store3.load(auto_decay=False, auto_consolidate=False)
    has_b = any("节点B" in n.content for n in store3.nodes.values())
    has_a = any("节点A" in n.content for n in store3.nodes.values())
    check("崩溃后节点B不存在（事务回滚）", not has_b, f"节点数={len(store3.nodes)}")
    check("节点A仍然存在", has_a)

    # 1c: 更极端的崩溃 — 写入一半数据后关闭
    conn2 = _open_db(db_path)
    conn2.execute("BEGIN")
    # 先删再写一半
    conn2.execute("DELETE FROM nodes")
    conn2.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("c-node-id", "节点C", "节点C：半途崩溃", 1,
                   "2026-01-01T00:00:00", None, "[]", 0, 0, "", 0, ""))
    # 节点D 还来不及写就崩溃了
    conn2.close()  # 回滚 → 节点A 应该还活着

    store4 = MindMapStore(data_path=db_path)
    store4.load(auto_decay=False, auto_consolidate=False)
    has_a2 = any("节点A" in n.content for n in store4.nodes.values())
    has_c = any("节点C" in n.content for n in store4.nodes.values())
    check("半途崩溃后节点A存活", has_a2, f"节点数={len(store4.nodes)}")
    check("半途崩溃后节点C不存在", not has_c)

    # 1d: 数据库本身未损坏
    try:
        conn3 = _open_db(db_path)
        conn3.execute("SELECT count(*) FROM nodes")
        conn3.close()
        check("数据库文件未损坏", True)
    except sqlite3.DatabaseError:
        check("数据库文件未损坏", False)

    # 1e: 崩溃后可正常写入
    store4.add_memory("节点D：崩溃恢复后正常写入")
    store4.save()
    store5 = MindMapStore(data_path=db_path)
    store5.load(auto_decay=False, auto_consolidate=False)
    has_d = any("节点D" in n.content for n in store5.nodes.values())
    check("崩溃恢复后可正常写入", has_d)

    shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# 测试 2: API 封装错误处理
# ============================================================
def test_api_error_handling():
    print("\n" + "=" * 60)
    print(" 测试 2: API 封装错误处理")
    print("=" * 60)

    tmpdir = Path(tempfile.mkdtemp(prefix="api_err_"))

    # 2a: 数据库不存在时 search
    db_a = tmpdir / "nonexistent.db"
    store_a = MindMapStore(data_path=db_a)
    store_a.load(auto_decay=False, auto_consolidate=False)
    result_a = store_a.search("测试")
    check("缺DB时search不崩溃", isinstance(result_a, list),
          f"type={type(result_a).__name__}")

    # 2b: 只读数据库 add_memory → save 失败
    db_b = tmpdir / "readonly.db"
    store_b = MindMapStore(data_path=db_b)
    store_b.load(auto_decay=False, auto_consolidate=False)
    store_b.add_memory("只读测试前置数据")
    store_b.save()
    os.chmod(str(db_b), 0o444)

    store_b2 = MindMapStore(data_path=db_b)
    store_b2.load(auto_decay=False, auto_consolidate=False)
    store_b2.add_memory("尝试写入只读DB")
    # save() 内部捕获只读异常 → 优雅降级，不向外抛
    save_ok = store_b2.save()  # 返回 False 而非抛异常
    check("只读DB save()返回False", save_ok is False,
          f"返回={save_ok}, 节点数={len(store_b2.nodes)}")
    # 确认回退：add 的节点被 rollback
    has_try = any("尝试写入只读DB" in n.content for n in store_b2.nodes.values())
    check("只读DB写入失败后节点回退", not has_try,
          "优雅降级：节点已从内存中移除")

    os.chmod(str(db_b), 0o644)

    # 2c: 空 content
    store_c = MindMapStore()
    store_c.load(auto_decay=False, auto_consolidate=False)
    result_c = store_c.add_memory("")
    check("空content返回空串", result_c == "")

    # 2d: 超长 content
    long_text = "长记忆内容" * 5000
    result_d = store_c.add_memory(long_text)
    check("超长content不崩溃", bool(result_d), f"ID: {result_d[:12] if result_d else 'None'}")

    # 2e: 空查询
    result_e = store_c.search("")
    check("空查询不崩溃", isinstance(result_e, (list, dict)))

    # 2f: remove 不存在的记忆
    result_f = store_c.remove_memory("这段内容绝对不存在xyz123")
    check("remove不存在记忆友好报错", not result_f.get("success"),
          f"error={result_f.get('error', '')[:60]}")

    shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# 测试 3: 守护同步防抖
# ============================================================
def test_sync_debounce():
    print("\n" + "=" * 60)
    print(" 测试 3: 守护同步防抖 — 2秒内5次sync不重复")
    print("=" * 60)

    tmpdir = Path(tempfile.mkdtemp(prefix="sync_deb_"))
    db_path = tmpdir / "test.db"
    md_dir = tmpdir / "memories"
    md_dir.mkdir(parents=True, exist_ok=True)
    md_path = md_dir / "MEMORY.md"

    md_path.write_text("""══════════════════════════════════════════════
MEMORY (your personal notes)
══════════════════════════════════════════════
防抖测试记忆1：偏好冰美式咖啡
§
防抖测试记忆2：使用 macOS 系统开发
§
防抖测试记忆3：代码风格偏好 TypeScript
""", encoding="utf-8")

    old_home = os.environ.get("HERMES_HOME", "")
    os.environ["HERMES_HOME"] = str(tmpdir)

    try:
        store = MindMapStore(data_path=db_path)
        store.load(auto_decay=False, auto_consolidate=False)

        counts = []
        for i in range(5):
            before = len(store.nodes)
            imported = store.sync_from_native()
            after = len(store.nodes)
            counts.append((before, after, imported))
            store.save()
            time.sleep(0.02)

        check("首次sync有导入", counts[0][2] > 0, f"导入={counts[0][2]}")
        later = [c[2] for c in counts[1:]]
        check("后续4次sync导入为0（去重）", all(x == 0 for x in later),
              f"导入序列={[c[2] for c in counts]}")

        contents = [n.content for n in store.nodes.values() if "防抖测试" in n.content]
        check("无重复节点", len(contents) == len(set(contents)),
              f"总数={len(contents)}, 去重={len(set(contents))}")

    finally:
        if old_home:
            os.environ["HERMES_HOME"] = old_home
        else:
            os.environ.pop("HERMES_HOME", None)

    shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
if __name__ == "__main__":
    print("🧪 发布前补充测试")
    print(f"   时间: {time.strftime('%Y-%m-%dT%H:%M:%S')}")

    test_transaction_rollback()
    test_api_error_handling()
    test_sync_debounce()

    print("\n" + "=" * 60)
    print(f"  📊 总结果: {PASS} 通过, {FAIL} 失败")
    print("=" * 60)

    sys.exit(0 if FAIL == 0 else 1)
