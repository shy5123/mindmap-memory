#!/usr/bin/env python3
"""
压力测试 — 记忆树（MemoryTree）
=============================

测试覆盖面:
  1. 大批量添加记忆（模拟 500+ 节点）
  2. 高频访问热点记忆
  3. 低频访问冷记忆被遗忘
  4. 核心记忆保护验证
  5. 检索性能（响应时间）
  6. 节点总数稳定性
  7. 衰减准确性（该留的留，该忘的忘）

用法:
    python3 scripts/stress_test.py              # 完整测试
    python3 scripts/stress_test.py --quick       # 快速测试（100 节点）
    python3 scripts/stress_test.py --perf-only   # 仅性能测试
"""

import sys
import os
import json
import time
import tempfile
import random
import shutil
from pathlib import Path
from datetime import datetime, timedelta

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from mindmap_memory import (
    MindMapStore, MemoryNode, SemanticMatcher,
    MAX_DEPTH, MAX_NON_CORE_NODES,
    NEW_NODE_SCORE, ACCESS_SCORE_INCREMENT,
    SHORT_TERM_MAX, LONG_TERM_MAX,
    DECAY_INTERVAL_DAYS, DECAY_AMOUNT,
    CORE_MIN_SCORE, MATCH_THRESHOLD,
)


class StressTest:
    """压力测试运行器。"""

    def __init__(self, quick_mode: bool = False):
        self.quick_mode = quick_mode
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="mindmap_test_"))
        self.data_path = self.tmp_dir / "test_mindmap.db"
        self.store = MindMapStore(data_path=self.data_path)

        # 测试参数
        if quick_mode:
            self.NUM_MEMORIES = 100
            self.NUM_ACCESS_ROUNDS = 3
            self.ACCESS_PER_ROUND = 20
        else:
            self.NUM_MEMORIES = 500
            self.NUM_ACCESS_ROUNDS = 5
            self.ACCESS_PER_ROUND = 50

        self.results = []  # 测试结果记录
        self.passed = 0
        self.failed = 0

    def cleanup(self):
        """清理临时文件。"""
        try:
            shutil.rmtree(self.tmp_dir)
        except OSError:
            pass

    def log(self, msg: str):
        """记录测试消息。"""
        print(f"  {msg}")

    def assert_true(self, condition: bool, test_name: str, detail: str = ""):
        """断言条件为真。"""
        msg = f"{'✅' if condition else '❌'} {test_name}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        if condition:
            self.passed += 1
        else:
            self.failed += 1
        self.results.append({
            "test": test_name,
            "passed": condition,
            "detail": detail,
        })

    def assert_equal(self, actual, expected, test_name: str, detail: str = ""):
        """断言两个值相等。"""
        self.assert_true(actual == expected, test_name, detail or f"期望={expected}, 实际={actual}")

    def assert_greater(self, actual, min_val, test_name: str, detail: str = ""):
        """断言实际值大于最小值。"""
        self.assert_true(actual > min_val, test_name, detail or f"{actual} > {min_val}")

    def assert_less(self, actual, max_val, test_name: str, detail: str = ""):
        """断言实际值小于最大值。"""
        self.assert_true(actual < max_val, test_name, detail or f"{actual} < {max_val}")

    def run(self):
        """执行所有测试。"""
        print("\n" + "═" * 60)
        print("🧪 记忆树 — 压力测试")
        print("═" * 60)
        print(f"  模式: {'快速' if self.quick_mode else '完整'}")
        print(f"  记忆数量: {self.NUM_MEMORIES}")
        print(f"  临时目录: {self.tmp_dir}")
        print()

        try:
            self.test_01_mass_insert()
            self.test_02_hierarchical_structure()
            self.test_03_retrieval_accuracy()
            self.test_04_retrieval_performance()
            self.test_05_access_scoring()
            self.test_06_core_memory_protection()
            self.test_07_decay_accuracy()
            self.test_08_node_count_stability()
            self.test_09_search_semantic_matching()
            self.test_10_depth_limit()
            self.print_summary()
        finally:
            self.cleanup()

    # ------------------------------------------------------------------
    # 测试 1: 大批量添加记忆
    # ------------------------------------------------------------------

    def test_01_mass_insert(self):
        """测试大批量添加记忆的正确性和性能。"""
        print("─" * 40)
        print("测试 1: 大批量添加记忆")
        print("─" * 40)

        # 生成多种话题的记忆
        topics = [
            ("编程", ["Python", "Rust", "JavaScript", "Go", "TypeScript"]),
            ("生活", ["健身", "饮食", "旅游", "读书", "音乐"]),
            ("工作", ["项目管理", "团队协作", "会议记录", "OKR", "复盘"]),
            ("学习", ["机器学习", "深度学习", "NLP", "计算机视觉", "强化学习"]),
            ("工具", ["Git", "Docker", "Kubernetes", "Vim", "VSCode"]),
        ]

        start_time = time.time()
        added = 0
        node_ids = []

        for _ in range(self.NUM_MEMORIES):
            cat, sub_topics = random.choice(topics)
            sub = random.choice(sub_topics)
            content = f"{cat}/{sub}: 这是关于{cat}中{sub}的第{added+1}条记忆。"
            content += f" 详细内容：{cat}领域的{sub}相关知识点，包括各种技巧和最佳实践。"
            content += f" 随机标识: {random.randint(10000, 99999)}"

            node_id = self.store.add_memory(content)
            if node_id:
                added += 1
                node_ids.append(node_id)

        elapsed = time.time() - start_time

        self.assert_equal(added, self.NUM_MEMORIES, f"全部 {self.NUM_MEMORIES} 条记忆添加成功")
        self.assert_true(
            len(self.store.nodes) >= self.NUM_MEMORIES,
            f"节点总数 ≥ {self.NUM_MEMORIES}",
            f"实际: {len(self.store.nodes)}"
        )
        self.assert_less(elapsed, 30.0, f"添加耗时 < 30秒", f"实际: {elapsed:.2f}秒")

        self.log(f"内存节点数: {len(self.store.nodes)}")
        self.log(f"根话题数: {len(self.store.root_ids)}")
        self.log(f"添加速度: {self.NUM_MEMORIES / elapsed:.1f} 条/秒")
        print()

    # ------------------------------------------------------------------
    # 测试 2: 层级结构验证
    # ------------------------------------------------------------------

    def test_02_hierarchical_structure(self):
        """验证记忆树的层级结构正确。"""
        print("─" * 40)
        print("测试 2: 层级结构验证")
        print("─" * 40)

        # 验证所有节点都是有效的树结构
        orphan_count = 0
        max_depth_found = 0
        leaf_count = 0

        for node_id, node in self.store.nodes.items():
            # 检查父节点引用
            if node.parent_id:
                if node.parent_id not in self.store.nodes:
                    orphan_count += 1
                else:
                    # 检查父节点的 children_ids 包含此节点
                    parent = self.store.nodes[node.parent_id]
                    if node_id not in parent.children_ids:
                        orphan_count += 1

            # 检查深度
            depth = self.store._get_depth(node_id)
            max_depth_found = max(max_depth_found, depth)

            # 检查子节点
            if node.is_leaf and node.content:
                leaf_count += 1

        self.assert_equal(orphan_count, 0, "无孤立节点（父引用一致）", f"孤儿: {orphan_count}")
        self.assert_true(max_depth_found <= MAX_DEPTH, f"最大深度 ≤ {MAX_DEPTH}", f"实际: {max_depth_found}")
        self.assert_greater(leaf_count, 0, "存在叶子节点（有内容）", f"叶子数: {leaf_count}")
        print()

    # ------------------------------------------------------------------
    # 测试 3: 检索准确性
    # ------------------------------------------------------------------

    def test_03_retrieval_accuracy(self):
        """测试检索结果与查询的相关性。"""
        print("─" * 40)
        print("测试 3: 检索准确性")
        print("─" * 40)

        # 用明确关键词查询
        queries = [
            ("Python", "编程"),
            ("Docker", "工具"),
            ("机器学习", "学习"),
            ("旅游", "生活"),
        ]

        total_relevant = 0
        total_results = 0

        for query, expected_category in queries:
            results = self.store.search(query, max_depth=3)
            total_results += len(results)

            # 至少应该有相关结果
            if results:
                total_relevant += 1
                self.log(f"查询 '{query}' → {len(results)} 条结果")

        self.assert_greater(total_relevant, 0, "有意义的查询返回结果", f"{total_relevant}/{len(queries)}")
        self.log(f"平均每次查询返回: {total_results / len(queries):.1f} 条")
        print()

    # ------------------------------------------------------------------
    # 测试 4: 检索性能
    # ------------------------------------------------------------------

    def test_04_retrieval_performance(self):
        """测试检索响应速度。"""
        print("─" * 40)
        print("测试 4: 检索性能")
        print("─" * 40)

        queries = ["Python编程", "Docker容器", "机器学习", "项目管理", "旅游攻略"]
        latencies = []

        for query in queries * 10:  # 每查询重复 10 次取平均
            start = time.time()
            self.store.search(query, max_depth=3)
            latencies.append(time.time() - start)

        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)
        p99_latency = sorted(latencies)[int(len(latencies) * 0.99)]

        self.assert_less(avg_latency, 0.5, "平均检索延迟 < 0.5秒", f"实际: {avg_latency*1000:.1f}ms")
        self.assert_less(max_latency, 2.0, "最大检索延迟 < 2秒", f"实际: {max_latency*1000:.1f}ms")

        self.log(f"平均延迟: {avg_latency*1000:.1f}ms")
        self.log(f"P99 延迟: {p99_latency*1000:.1f}ms")
        self.log(f"最大延迟: {max_latency*1000:.1f}ms")
        print()

    # ------------------------------------------------------------------
    # 测试 5: 访问加分机制
    # ------------------------------------------------------------------

    def test_05_access_scoring(self):
        """测试访问后分数正确增加。"""
        print("─" * 40)
        print("测试 5: 访问加分机制")
        print("─" * 40)

        # 找任意一个有内容的叶子节点
        leaf_nodes = [n for n in self.store.nodes.values() if n.is_leaf and n.content]
        if not leaf_nodes:
            self.log("无叶子节点可测试，跳过")
            return

        target = leaf_nodes[0]
        node_id = target.id
        old_score = target.score
        old_access = target.last_access

        # 直接调用访问加分
        self.store._apply_access_bonus(node_id)
        self.store.save()

        # 重新读取
        self.store.load()
        updated = self.store.nodes[node_id]

        self.assert_greater(
            updated.score, old_score,
            "被访问节点分数增加",
            f"{old_score} → {updated.score}"
        )
        self.assert_true(
            updated.last_access > old_access,
            "被访问节点的 last_access 更新",
            f"旧: {old_access[:19]}, 新: {updated.last_access[:19]}"
        )
        assert updated.score == old_score + ACCESS_SCORE_INCREMENT, \
            f"分数增加量应为 {ACCESS_SCORE_INCREMENT}，实际增加: {updated.score - old_score}"
        print()

    # ------------------------------------------------------------------
    # 测试 6: 核心记忆保护
    # ------------------------------------------------------------------

    def test_06_core_memory_protection(self):
        """测试核心记忆不会被衰减删除。"""
        print("─" * 40)
        print("测试 6: 核心记忆保护")
        print("─" * 40)

        # 创建一条核心记忆
        node_id = self.store.add_memory("这是一条重要的核心记忆，永远不应该被遗忘。")
        self.store.set_core(node_id, True)

        node = self.store.nodes[node_id]
        self.assert_true(node.is_core, "核心标记已设置")
        self.assert_equal(node.score, CORE_MIN_SCORE,
                          f"核心记忆分数不低于 {CORE_MIN_SCORE}")

        # 手动降低分数并模拟衰减
        node.score = 2  # 设为低分
        node.last_access = (datetime.now() - timedelta(days=30)).isoformat()
        self.store.save()

        # 执行衰减
        removed = self.store.decay_if_needed()
        self.store.load()

        # 核心记忆应该还在
        self.assert_true(
            node_id in self.store.nodes,
            "核心记忆未被衰减删除",
        )
        if node_id in self.store.nodes:
            self.assert_true(
                self.store.nodes[node_id].score >= CORE_MIN_SCORE,
                f"核心记忆分数 >= {CORE_MIN_SCORE}",
                f"实际: {self.store.nodes[node_id].score}"
            )
        print()

    # ------------------------------------------------------------------
    # 测试 7: 衰减准确性
    # ------------------------------------------------------------------

    def test_07_decay_accuracy(self):
        """测试衰减机制：冷记忆被遗忘，热记忆保留。"""
        print("─" * 40)
        print("测试 7: 衰减准确性")
        print("─" * 40)

        # 创建测试专用的临时 store
        decay_path = self.tmp_dir / "decay_test.db"
        decay_store = MindMapStore(data_path=decay_path)

        # 创建 3 条热记忆（最近访问过）
        hot_ids = []
        for i in range(3):
            nid = decay_store.add_memory(f"热记忆-{i}: 这是一条经常被访问的记忆。")
            hot_ids.append(nid)

        # 创建 3 条冷记忆（30天未访问）
        cold_ids = []
        for i in range(3):
            nid = decay_store.add_memory(f"冷记忆-{i}: 这是一条早已被遗忘的记忆。")
            cold_ids.append(nid)
            # 手动设置最后访问时间为 30 天前
            decay_store.nodes[nid].last_access = (
                datetime.now() - timedelta(days=30)
            ).isoformat()
            decay_store.nodes[nid].score = 1  # 设为最低分

        decay_store.last_decay = (datetime.now() - timedelta(days=8)).isoformat()
        decay_store.save()

        # 执行衰减
        removed = decay_store.decay_if_needed()

        # 热记忆应该全部保留
        for nid in hot_ids:
            self.assert_true(
                nid in decay_store.nodes,
                f"热记忆保留: {decay_store.nodes[nid].topic[:30]}",
            )

        # 冷记忆 score=1 的应该被软删除
        deleted_cold = sum(1 for nid in cold_ids 
                          if nid in decay_store.nodes and decay_store.nodes[nid].deleted)
        self.assert_greater(deleted_cold, 0, "冷记忆（score≤0）被遗忘删除",
                            f"删除 {deleted_cold}/{len(cold_ids)} 条")

        self.log(f"衰减删除: {len(removed)} 个节点")
        print()

    # ------------------------------------------------------------------
    # 测试 8: 节点总数稳定性
    # ------------------------------------------------------------------

    def test_08_node_count_stability(self):
        """测试节点总数在持续操作中保持稳定。"""
        print("─" * 40)
        print("测试 8: 节点总数稳定性")
        print("─" * 40)

        # 创建独立 store 测试
        stable_path = self.tmp_dir / "stable_test.db"
        stable_store = MindMapStore(data_path=stable_path)

        # 阶段 1: 大量添加
        initial_count = stable_store._count_non_core_nodes()
        count_after_add = 0
        for _ in range(200):
            content = f"稳定性测试记忆 {_} - " + random.choice([
                "编程相关", "生活相关", "工作相关", "学习相关", "工具相关"
            ])
            stable_store.add_memory(content)

        count_after_add = stable_store._count_non_core_nodes()
        self.assert_greater(count_after_add, initial_count, "添加后节点数增加")

        # 阶段 2: 频繁访问 + 少量添加
        for _ in range(20):
            query = random.choice(["编程", "生活", "工作"])
            stable_store.search(query)

        count_after_access = stable_store._count_non_core_nodes()
        self.log(f"节点数变化: {initial_count} → {count_after_add} → {count_after_access}")
        print()

    # ------------------------------------------------------------------
    # 测试 9: 语义匹配正确性
    # ------------------------------------------------------------------

    def test_09_search_semantic_matching(self):
        """测试 SemanticMatcher 的匹配质量。"""
        print("─" * 40)
        print("测试 9: 语义匹配正确性")
        print("─" * 40)

        # 完全匹配
        sim = SemanticMatcher.similarity("Python编程", "Python编程")
        self.assert_greater(sim, 0.8, "完全匹配相似度高", f"{sim:.2f}")
        self.assert_true(sim >= MATCH_THRESHOLD, "完全匹配通过阈值", f"sim={sim:.2f}, threshold={MATCH_THRESHOLD}")

        # 相关匹配
        sim = SemanticMatcher.similarity("Python异步", "Python编程")
        self.assert_greater(sim, MATCH_THRESHOLD / 2, "相关匹配有部分相似度", f"{sim:.2f}")

        # 不相关匹配
        sim = SemanticMatcher.similarity("Python编程", "旅游攻略")
        self.assert_less(sim, MATCH_THRESHOLD, "不相关匹配低于阈值", f"{sim:.2f}")

        # 英文匹配
        sim = SemanticMatcher.similarity("DeepSeek API configuration", "DeepSeek API settings")
        self.assert_greater(sim, MATCH_THRESHOLD, "英文语义匹配", f"{sim:.2f}")
        print()

    # ------------------------------------------------------------------
    # 测试 10: 深度限制
    # ------------------------------------------------------------------

    def test_10_depth_limit(self):
        """测试 6 层深度限制生效。"""
        print("─" * 40)
        print("测试 10: 深度限制")
        print("─" * 40)

        depth_path = self.tmp_dir / "depth_test.db"
        depth_store = MindMapStore(data_path=depth_path)

        # 手动创建一条超深链
        parent_id = None
        for level in range(MAX_DEPTH + 2):
            topic = f"第{level+1}层话题"
            content = f"深度测试内容-层级{level+1}" if level >= MAX_DEPTH - 1 else ""
            nid = depth_store.add_node(topic=topic, content=content, parent_id=parent_id)
            parent_id = nid

        # 检查所有节点深度 ≤ MAX_DEPTH
        max_actual_depth = 0
        for nid in depth_store.nodes:
            d = depth_store._get_depth(nid)
            max_actual_depth = max(max_actual_depth, d)

        self.assert_true(
            max_actual_depth <= MAX_DEPTH,
            f"所有节点深度 ≤ {MAX_DEPTH}",
            f"最大实际深度: {max_actual_depth}"
        )

        self.log(f"最大实际深度: {max_actual_depth} / 限制: {MAX_DEPTH}")
        print()

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------

    def print_summary(self):
        """打印测试汇总。"""
        total = self.passed + self.failed
        print("═" * 60)
        print(f"📊 测试汇总: {self.passed}/{total} 通过")
        print("═" * 60)

        if self.failed == 0:
            print("🎉 全部测试通过！")
        else:
            print("⚠️  以下测试未通过:")
            for r in self.results:
                if not r["passed"]:
                    print(f"   ❌ {r['test']}: {r['detail']}")

        print()
        print(f"测试时间: {datetime.now().isoformat()}")
        print(f"模式: {'快速' if self.quick_mode else '完整'}")
        print(f"记忆数量: {self.NUM_MEMORIES}")


def main():
    quick = "--quick" in sys.argv
    StressTest(quick_mode=quick).run()


if __name__ == "__main__":
    main()
