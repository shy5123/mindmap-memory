#!/usr/bin/env python3
"""
记忆树 — MemoryTree
一棵会新陈代谢的记忆树
==========================================

层级化、自生长的多叉树记忆系统。支持自动分类、语义检索、基于时间的遗忘衰减。

架构概述:
  MemoryNode      — 单个记忆节点 (dataclass)
  MindMapStore    — 记忆存储与操作主类
    ├── 存储层: load/save (SQLite 事务持久化)
    ├── 分类层: add_memory (语义匹配 + 自动建节点)
    ├── 检索层: search (逐层下钻检索)
    ├── 遗忘层: decay (每14天衰减扫描)
    └── 索引层: generate_index (生成轻量系统提示)

数据文件: ~/.hermes/memories/mindmap.db (SQLite，自动从 mindmap.json 迁移)
遗忘日志: ~/.hermes/memories/decay_log/YYYY-MM-DD.json
索引输出: ~/.hermes/memories/MEMORY.md

版本: 1.0.0
许可: MIT
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
import difflib
import hashlib
import logging
import sqlite3
import tempfile
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 路径配置 — 自动检测 HERMES_HOME
# ---------------------------------------------------------------------------

def _get_memories_dir() -> Path:
    """获取记忆目录路径，兼容 profile 切换。"""
    hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    return Path(hermes_home) / "memories"


def _get_data_path() -> Path:
    """获取树形记忆数据文件路径。"""
    return _get_memories_dir() / "mindmap.db"


def _get_decay_log_dir() -> Path:
    """获取遗忘日志目录路径。"""
    return _get_memories_dir() / "decay_log"


# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("mindmap_memory")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

MAX_DEPTH = 6                    # 树的最大深度（根=第1层）
MAX_NON_CORE_NODES = 10_000      # 非核心节点总数上限
NEW_NODE_SCORE = 2               # 新建节点的初始分数（≤2 将沉入树根）
ACCESS_SCORE_INCREMENT = 1       # 每次访问加分
PARENT_MAX_BONUS = 0.1           # 父节点加分上限
DECAY_AMOUNT = 1                 # 每周期衰减量（14天）
DECAY_INTERVAL_DAYS = 14         # 衰减间隔（天）
CORE_MIN_SCORE = 3               # 核心记忆最低分数（>2 确保永不下沉）
SHORT_TERM_MAX = 20              # 短期记忆上限
LONG_TERM_MAX = 40               # 长期记忆上限（>40 为永久记忆）
MATCH_THRESHOLD = 0.20           # 语义匹配最低相似度
DEEP_MATCH_THRESHOLD = 0.10      # 树根关键词匹配阈值（无嵌入模型时）
DEEP_RETENTION_DAYS = 1095       # 树根保留天数（3年）


def _now_iso() -> str:
    """返回当前时间的 ISO 8601 字符串。"""
    return datetime.now().isoformat()


# ---------------------------------------------------------------------------
# MemoryNode — 单个记忆节点
# ---------------------------------------------------------------------------

@dataclass
class MemoryNode:
    """记忆树中的一个节点。

    每个节点代表一个话题或一条记忆内容。
    非叶子节点只存 topic（标题），content 为空。
    叶子节点同时存 topic 和 content（完整记忆内容）。

    Attributes:
        id: 唯一标识符 (UUID)
        topic: 节点标题/话题名
        content: 记忆内容（仅叶子节点有值）
        score: 记忆分数，决定区间归属 (1-20 短期, 21-40 长期, 41+ 永久)
        last_access: 最后访问时间 (ISO 8601)
        parent_id: 父节点 ID，根节点为 None
        children_ids: 子节点 ID 列表
        is_core: 是否为核心记忆（受保护，永不被衰减删除）
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    topic: str = ""
    content: str = ""
    score: int = NEW_NODE_SCORE
    last_access: str = field(default_factory=_now_iso)
    parent_id: Optional[str] = None
    children_ids: List[str] = field(default_factory=list)
    is_core: bool = False
    deleted: bool = False
    deleted_at: str = ""
    created_at: str = ""
    is_deep: bool = False

    @property
    def is_leaf(self) -> bool:
        """是否为叶子节点（无子节点且有内容）。"""
        return len(self.children_ids) == 0

    @property
    def is_root(self) -> bool:
        """是否为根节点。"""
        return self.parent_id is None

    @property
    def depth(self) -> int:
        """节点深度（需要配合 store 计算，此处返回 0 表示未计算）。"""
        return 0  # 具体深度由 MindMapStore 递归计算

    def score_category(self) -> str:
        """返回分数所属的类别名称。"""
        if self.score <= SHORT_TERM_MAX:
            return "短期记忆"
        elif self.score <= LONG_TERM_MAX:
            return "长期记忆"
        return "永久记忆"

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryNode":
        """从字典反序列化。"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# 语义匹配引擎 — 用于自动分类和检索
# ---------------------------------------------------------------------------

class SemanticMatcher:
    """简单的语义匹配引擎。

    使用关键词提取 + 字符串相似度进行匹配。
    不依赖外部 NLP 库，纯 Python 实现，适合小白部署。

    匹配流程:
      1. 提取文本中的关键词（中文按字符/词切分，英文按空格）
      2. 计算与目标话题的 token 重叠率
      3. 使用 SequenceMatcher 计算编辑距离相似度
      4. 综合两个分数得出最终匹配度
    """

    # 中文停用词 — 匹配时忽略这些高频无意义词
    STOP_WORDS = {
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
        "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
        "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
        "什么", "怎么", "如何", "哪", "吗", "吧", "呢", "啊", "哦", "哈", "嗯",
        "这个", "那个", "可以", "需要", "应该", "可能", "已经", "还是", "或者",
        "但", "与", "及", "或", "对", "从", "以", "之", "其", "所", "而",
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "before", "after", "above", "below", "between", "under", "over",
        "and", "but", "or", "not", "no", "nor", "so", "if", "then", "than",
        "too", "very", "just", "now", "here", "there", "when", "where",
        "why", "how", "all", "both", "each", "few", "more", "most", "other",
        "some", "such", "only", "own", "same", "into", "about", "also",
    }

    @staticmethod
    def extract_keywords(text: str) -> List[str]:
        """从文本中提取关键词。

        中文按字符和常见词切分，英文按空格和标点切分。
        过滤停用词和短词。

        Args:
            text: 输入文本

        Returns:
            关键词列表（已去重，保持原始顺序）
        """
        if not text:
            return []

        # 统一转小写
        text = text.lower().strip()

        # 提取中文字符序列作为候选词（2-4字组合）
        chinese_chars = re.findall(r'[\u4e00-\u9fff]+', text)

        keywords = []
        for chunk in chinese_chars:
            # 对中文串按 2-4 字滑动窗口切分
            # 对短词也做滑动窗口，确保短关键词能被提取
            if len(chunk) >= 2:
                for i in range(len(chunk) - 1):
                    for wlen in (2, 3, 4):
                        if i + wlen <= len(chunk):
                            keywords.append(chunk[i:i + wlen])
            elif len(chunk) == 1:
                keywords.append(chunk)  # 单字也保留

        # 提取英文单词
        english_words = re.findall(r'[a-zA-Z][a-zA-Z0-9._-]*', text)
        keywords.extend(w for w in english_words if len(w) >= 2)

        # 过滤停用词和单字（单中文字通常信息量低）
        result = []
        seen = set()
        for kw in keywords:
            if kw in SemanticMatcher.STOP_WORDS:
                continue
            if len(kw) <= 1 and not kw[0].isascii():
                continue
            if kw.lower() not in seen:
                seen.add(kw.lower())
                result.append(kw)

        return result if result else [text.strip()]

    @staticmethod
    def similarity(text_a: str, text_b: str) -> float:
        """计算两段文本的语义相似度。

        综合两个维度:
          1. 关键词重叠率 (权重 0.4)
          2. 编辑距离相似度 (权重 0.6)

        Args:
            text_a: 文本 A
            text_b: 文本 B

        Returns:
            0.0-1.0 之间的相似度分数
        """
        kw_a = set(SemanticMatcher.extract_keywords(text_a))
        kw_b = set(SemanticMatcher.extract_keywords(text_b))

        # 关键词重叠率 — 非对称：侧重查询关键词有多少被匹配
        if kw_a and kw_b:
            common = kw_a & kw_b
            query_coverage = len(common) / len(kw_a) if kw_a else 0  # 查询词命中率（权重高）
            target_ratio = len(common) / len(kw_b) if kw_b else 0     # 目标词相关性
            overlap = 0.7 * query_coverage + 0.3 * target_ratio
        else:
            overlap = 0.0

        # 编辑距离相似度
        seq_sim = difflib.SequenceMatcher(None, text_a.lower(), text_b.lower()).ratio()

        # 综合分数（关键词匹配权重高，因为中文场景更有效）
        return 0.5 * overlap + 0.5 * seq_sim

    # ------------------------------------------------------------------
    # BM25 稀疏检索（纯 Python 实现，零外部依赖）
    # ------------------------------------------------------------------

    # BM25 超参数（与学术界标准一致）
    BM25_K1 = 1.5   # 词频饱和度控制
    BM25_B = 0.75   # 文档长度归一化

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """将文本切分为词条（复用 extract_keywords 的词条提取逻辑）。"""
        return SemanticMatcher.extract_keywords(text)

    @staticmethod
    def _bm25_score_single(
        query_tokens: List[str],
        doc_tokens: List[str],
        idf: Dict[str, float],
        avgdl: float,
    ) -> float:
        """对单个文档计算 BM25 分数。

        Args:
            query_tokens: 查询词条列表
            doc_tokens: 文档词条列表
            idf: 预计算的 IDF 字典 {term: idf_value}
            avgdl: 平均文档长度

        Returns:
            BM25 分数（非归一化，可用于排序）
        """
        if not query_tokens or not doc_tokens:
            return 0.0

        from collections import Counter
        doc_len = len(doc_tokens)
        tf = Counter(doc_tokens)
        k1 = SemanticMatcher.BM25_K1
        b = SemanticMatcher.BM25_B

        score = 0.0
        for token in set(query_tokens):
            if token not in idf:
                continue
            f = tf.get(token, 0)
            if f == 0:
                continue
            # BM25 term weight
            numerator = f * (k1 + 1.0)
            denominator = f + k1 * (1.0 - b + b * doc_len / avgdl) if avgdl > 0 else f + k1
            score += idf[token] * numerator / denominator

        return score

    @classmethod
    def bm25_search(
        cls,
        query: str,
        candidates: List[str],
    ) -> List[float]:
        """BM25 稀疏检索：对查询与候选文档列表计算 BM25 分数。

        基于标准 BM25 公式（Robertson et al., 1995），纯 Python 实现。
        在 corpus 上构建倒排索引和 IDF，然后对每个候选文档打分。

        Args:
            query: 查询文本
            candidates: 候选文档文本列表

        Returns:
            与 candidates 等长的 BM25 分数列表
        """
        import math
        from collections import Counter

        if not candidates:
            return []

        # 构建语料统计
        doc_tokens_list = [cls._tokenize(doc) for doc in candidates]
        query_tokens = cls._tokenize(query)

        if not query_tokens:
            return [0.0] * len(candidates)

        N = len(doc_tokens_list)
        doc_lengths = [len(dt) for dt in doc_tokens_list]
        avgdl = sum(doc_lengths) / N if N > 0 else 1.0

        # 计算 DF（文档频率）和 IDF
        df: Dict[str, int] = {}
        for dt in doc_tokens_list:
            for token in set(dt):
                df[token] = df.get(token, 0) + 1

        idf: Dict[str, float] = {}
        for token, freq in df.items():
            # IDF 平滑公式（与学术界实现一致）
            idf[token] = math.log((N - freq + 0.5) / (freq + 0.5) + 1.0)

        # 对每个候选文档计算 BM25 分数
        scores = []
        for doc_tokens in doc_tokens_list:
            s = cls._bm25_score_single(query_tokens, doc_tokens, idf, avgdl)
            scores.append(s)

        return scores

    # ------------------------------------------------------------------
    # RRF 融合排序（Reciprocal Rank Fusion）
    # ------------------------------------------------------------------

    @staticmethod
    def rrf_fusion(
        score_sets: List[List[float]],
        k: int = 60,
    ) -> List[float]:
        """将多组分数通过 RRF 融合为单一排序分数。

        对每组分数独立排序（降序），然后对每个候选项计算 RRF：
            RRF_score(i) = Σ 1 / (k + rank_j(i))
        其中 rank_j(i) 是候选项 i 在第 j 组排序中的排名（从 1 开始）。

        Args:
            score_sets: 多组分数列表，每组与候选项一一对应
            k: RRF 常数（默认 60，与学术界一致）

        Returns:
            融合后的 RRF 分数列表（长度等于各组候选数）
        """
        if not score_sets:
            return []

        n_candidates = len(score_sets[0])
        if n_candidates == 0:
            return []

        # 对每组分数计算排名（降序，分数相同取平均排名）
        ranks_list: List[List[float]] = []
        for scores in score_sets:
            # 创建 (index, score) 对，按 score 降序排列
            indexed = list(enumerate(scores))
            indexed.sort(key=lambda x: x[1], reverse=True)

            ranks = [0.0] * n_candidates
            i = 0
            while i < n_candidates:
                # 处理相同分数：取平均排名
                j = i
                while j < n_candidates and indexed[j][1] == indexed[i][1]:
                    j += 1
                avg_rank = (i + 1 + j) / 2.0  # 1-indexed 平均排名
                for m in range(i, j):
                    ranks[indexed[m][0]] = avg_rank
                i = j
            ranks_list.append(ranks)

        # RRF 融合：对每个候选项求和
        rrf_scores = [0.0] * n_candidates
        for ranks in ranks_list:
            for i in range(n_candidates):
                rrf_scores[i] += 1.0 / (k + ranks[i])

        return rrf_scores

    @classmethod
    def hybrid_search(
        cls,
        query: str,
        candidates: List[str],
        rrf_k: int = 60,
    ) -> List[Tuple[int, float]]:
        """混合搜索：BM25 稀疏检索 + 关键词/余弦相似度，通过 RRF 融合排序。

        综合两种检索信号：
          1. BM25 稀疏检索 — 捕获精确关键词匹配
          2. 语义相似度   — 捕获同义/相关语义

        通过 RRF 融合两组排名，得到最终排序。

        Args:
            query: 查询文本
            candidates: 候选文档文本列表
            rrf_k: RRF 常数（默认 60）

        Returns:
            [(候选项索引, RRF融合分数), ...] 按分数降序排列
        """
        if not candidates:
            return []

        # 第一路：BM25 稀疏检索
        bm25_scores = cls.bm25_search(query, candidates)

        # 第二路：语义相似度（关键词重叠 + 编辑距离）
        keyword_scores = [cls.similarity(query, c) for c in candidates]

        # RRF 融合
        fused = cls.rrf_fusion([bm25_scores, keyword_scores], k=rrf_k)

        # 构建排序结果
        result = [(i, fused[i]) for i in range(len(fused))]
        result.sort(key=lambda x: x[1], reverse=True)
        return result


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 语义模型接口 — 可插拔的匹配引擎
# ---------------------------------------------------------------------------

class SemanticModel:
    """语义匹配模型的抽象接口。

    默认使用 KeywordModel（零依赖），可通过环境变量切换嵌入模型：
      export MEMORYTREE_EMBEDDING_MODEL=openai:text-embedding-3-small
      export MEMORYTREE_EMBEDDING_API_KEY=***        # 可选
      export MEMORYTREE_EMBEDDING_API_BASE=https://...   # 可选，兼容第三方 API
      # 恢复默认: unset MEMORYTREE_EMBEDDING_MODEL 或设为 "keyword"
    """

    def similarity(self, text_a: str, text_b: str) -> float:
        raise NotImplementedError

    def extract_keywords(self, text: str) -> list:
        raise NotImplementedError

    def batch_similarity(self, query: str, candidates: list[str]) -> list[float]:
        """批量计算相似度。默认逐个调用 similarity()，子类可重写为批量编码。"""
        return [self.similarity(query, c) for c in candidates]

    def hybrid_search(
        self, query: str, candidates: list[str], rrf_k: int = 60
    ) -> list[tuple]:
        """混合搜索：BM25 + 语义相似度 + RRF 融合。
        
        默认实现使用 SemanticMatcher.hybrid_search。
        子类可重写为使用嵌入向量的混合搜索。
        
        Returns:
            [(index, rrf_score), ...] 按分数降序排列
        """
        return SemanticMatcher.hybrid_search(query, candidates, rrf_k=rrf_k)

    def health_status(self) -> str:
        """返回健康状态字符串。默认实现始终健康。"""
        return ""


class KeywordModel(SemanticModel):
    """默认模型：关键词重叠 + 编辑距离。零依赖。"""

    def similarity(self, text_a: str, text_b: str) -> float:
        return SemanticMatcher.similarity(text_a, text_b)

    def extract_keywords(self, text: str) -> list:
        return SemanticMatcher.extract_keywords(text)


class OpenAIEmbeddingModel(SemanticModel):
    """基于 OpenAI 兼容嵌入 API 的语义匹配。

    支持任何兼容 /v1/embeddings 的服务。
    """

    def __init__(self, model: str = "text-embedding-3-small",
                 api_key: str = "", api_base: str = ""):
        self.model = model
        self.api_key = api_key or os.environ.get(
            "MEMORYTREE_EMBEDDING_API_KEY",
            os.environ.get("OPENAI_API_KEY", "")
        )
        self.api_base = api_base or os.environ.get(
            "MEMORYTREE_EMBEDDING_API_BASE",
            "https://api.openai.com/v1"
        )
        self._cache: dict = {}
        self._failures: int = 0
        self._alerted: bool = False
        self._checked: bool = False

    def _preflight(self) -> bool:
        """快速预检 API 是否可达（3秒超时）。"""
        if self._checked:
            return not self._alerted
        self._checked = True
        if not self.api_key:
            self._alerted = True
            msg = (
                "⚠️  记忆树嵌入 API 未配置 API Key。\n"
                "   设置 MEMORYTREE_EMBEDDING_API_KEY 或 OPENAI_API_KEY\n"
                "   或 unset MEMORYTREE_EMBEDDING_MODEL 使用关键词匹配"
            )
            print(msg, file=sys.stderr)
            return False
        return True

    def _embed(self, text: str) -> list:
        if not self._preflight():
            return None
        key = text[:200]
        if key in self._cache:
            return self._cache[key]

        import urllib.request
        url = f"{self.api_base.rstrip('/')}/embeddings"
        body = json.dumps({
            "model": self.model, "input": text[:8000]
        }).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
            vec = data["data"][0]["embedding"]
            self._cache[key] = vec
            # API 恢复后清除告警
            if self._alerted:
                logger.info("嵌入 API 已恢复")
                print("✅ 记忆树嵌入 API 已恢复，语义匹配正常", file=sys.stderr)
                self._alerted = False
            return vec
        except Exception as e:
            self._failures += 1
            logger.warning("嵌入 API 失败，回退关键词: %s", e)
            if not self._alerted:
                self._alerted = True
                msg = (
                    f"⚠️  记忆树嵌入 API ({self.model}) 不可用，已回退关键词匹配。\n"
                    f"   原因: {e}\n"
                    f"   影响: 语义匹配精度降低（如同义词、相关概念匹配变差）\n"
                    f"   修复: 检查 MEMORYTREE_EMBEDDING_API_KEY 和网络连接\n"
                    f"   抑制: unset MEMORYTREE_EMBEDDING_MODEL 使用纯关键词匹配"
                )
                print(msg, file=sys.stderr)
            return None

    @property
    def healthy(self) -> bool:
        """API 是否健康（最近一次调用成功）。"""
        return not self._alerted

    @staticmethod
    def _cosine(a: list, b: list) -> float:
        if not a or not b:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    def similarity(self, text_a: str, text_b: str) -> float:
        va = self._embed(text_a)
        vb = self._embed(text_b)
        if va is None or vb is None:
            return SemanticMatcher.similarity(text_a, text_b)
        return max(0.0, min(1.0, (self._cosine(va, vb) + 1) / 2))

    def extract_keywords(self, text: str) -> list:
        return SemanticMatcher.extract_keywords(text)

    def health_status(self) -> str:
        """返回嵌入 API 健康状态。"""
        if self.healthy:
            return ""
        return (
            f"嵌入 API ({self.model}) 不可用，已回退关键词匹配 "
            f"(失败 {self._failures} 次)。"
            f"检查 MEMORYTREE_EMBEDDING_API_KEY 或 unset MEMORYTREE_EMBEDDING_MODEL"
        )


class LocalEmbeddingModel(SemanticModel):
    """基于本地 sentence-transformers 的语义匹配。

    零 API 调用、零延迟、零费用。首次使用自动下载模型。
    """

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        self.model_name = model_name
        self._model = None
        self._cache: dict = {}
        self._init_ok = False
        self._init_error: str = ""

    def _ensure_model(self):
        """惰性加载模型。"""
        if self._model is not None:
            return self._model
        if self._init_error:
            return None
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
            self._init_ok = True
            logger.info("本地嵌入模型已加载: %s", self.model_name)
            return self._model
        except ImportError:
            self._init_error = "sentence-transformers 未安装 (pip install sentence-transformers)"
            logger.warning("本地嵌入模型不可用: %s", self._init_error)
            return None
        except Exception as e:
            self._init_error = str(e)
            logger.warning("本地嵌入模型加载失败: %s", e)
            return None

    def _embed(self, text: str):
        model = self._ensure_model()
        if model is None:
            return None
        key = text[:200]
        if key in self._cache:
            return self._cache[key]
        vec = model.encode(text, normalize_embeddings=True).tolist()
        self._cache[key] = vec
        return vec

    def similarity(self, text_a: str, text_b: str) -> float:
        va = self._embed(text_a)
        vb = self._embed(text_b)
        if va is None or vb is None:
            return SemanticMatcher.similarity(text_a, text_b)
        dot = sum(x * y for x, y in zip(va, vb))
        return max(0.0, min(1.0, (dot + 1) / 2))

    def batch_similarity(self, query: str, candidates: list[str]) -> list[float]:
        """批量计算 query 与多个候选文本的相似度。

        一次编码 query，批量编码 candidates。比逐个调用 similarity() 快 10-50 倍。
        """
        model = self._ensure_model()
        if model is None:
            return [SemanticMatcher.similarity(query, c) for c in candidates]
        qv = self._embed(query)
        if qv is None:
            return [SemanticMatcher.similarity(query, c) for c in candidates]
        try:
            cvs = model.encode(candidates, normalize_embeddings=True, show_progress_bar=False)
            import numpy as np
            dots = np.dot(cvs, qv)
            return [max(0.0, min(1.0, float(d))) for d in dots]
        except Exception:
            return [SemanticMatcher.similarity(query, c) for c in candidates]

    def extract_keywords(self, text: str) -> list:
        return SemanticMatcher.extract_keywords(text)

    @property
    def healthy(self) -> bool:
        return self._init_ok or not self._init_error

    def health_status(self) -> str:
        if self._init_ok:
            return ""
        return f"本地嵌入模型 ({self.model_name}) 不可用: {self._init_error}"


def _get_matcher() -> SemanticModel:
    """根据 MEMORYTREE_EMBEDDING_MODEL 创建匹配器。"""
    cfg = os.environ.get("MEMORYTREE_EMBEDDING_MODEL", "").strip()
    if not cfg or cfg == "keyword":
        return KeywordModel()
    if ":" in cfg:
        prov, model = cfg.split(":", 1)
        prov = prov.lower().strip()
        if prov == "openai":
            logger.info("使用嵌入模型: %s", model.strip())
            return OpenAIEmbeddingModel(model=model.strip())
        if prov == "local":
            local_path = os.path.expanduser(model).strip()
            if os.path.isdir(local_path):
                logger.info("使用本地嵌入模型（本地路径）: %s", local_path)
                return LocalEmbeddingModel(model_name=local_path)
            logger.info("使用本地嵌入模型: %s", model.strip())
            return LocalEmbeddingModel(model_name=model.strip())
    logger.warning("未知嵌入配置 '%s'，回退关键词", cfg)
    return KeywordModel()


# MindMapStore — 记忆存储主类
# ---------------------------------------------------------------------------

class MindMapStore:
    """记忆树（MemoryTree）存储。

    管理整个记忆树的生命周期：创建、检索、更新、遗忘。
    数据持久化到 SQLite 数据库，使用事务保证数据安全。

    Usage:
        store = MindMapStore()
        store.load()
        node_id = store.add_memory("Python异步编程的最佳实践...")
        results = store.search("Python")
        store.decay_if_needed()
        store.save()
    """

    def __init__(self, data_path: Optional[Path] = None, matcher: Optional[SemanticModel] = None):
        """初始化记忆存储。

        Args:
            data_path: 数据文件路径，默认 ~/.hermes/memories/mindmap.db
            matcher: 语义匹配模型，默认根据 MEMORYTREE_EMBEDDING_MODEL 环境变量自动选择
        """
        self.data_path = data_path or _get_data_path()
        self.decay_log_dir = _get_decay_log_dir()
        self.nodes: Dict[str, MemoryNode] = {}  # id → node
        self.root_ids: List[str] = []            # 根节点 ID 列表
        self.last_decay: Optional[str] = None    # 上次衰减时间 (ISO 8601)
        self.last_consolidate: Optional[str] = None  # 上次记忆园丁时间
        self.version: int = 1                    # 数据格式版本
        self.matcher: SemanticModel = matcher or _get_matcher()
        # 分类匹配器：默认用关键词（快速），可单独配置
        _cls_cfg = os.environ.get("MEMORYTREE_CLASSIFY_MODEL", "").strip()
        self.classify_matcher: SemanticModel = (
            _get_matcher() if _cls_cfg == "embedding" else KeywordModel()
        )
        # 内容哈希去重：SHA-256 哈希集合 + 计数器
        self._content_hashes: set = set()
        self._duplicates_skipped: int = 0
        # 混合搜索使用次数
        self._hybrid_search_count: int = 0

    # ------------------------------------------------------------------
    # 持久化 — 原子读写
    # ------------------------------------------------------------------

    def load(self, auto_decay: bool = True, auto_consolidate: bool = True) -> bool:
        """从 SQLite 数据库加载记忆树。

        如果数据库不存在，尝试从旧 mindmap.json 自动迁移。
        加载后自动检查是否需要衰减扫描（距上次超过 7 天则执行）。
        加载后自动检查是否需要记忆园丁（距上次超过 24 小时则执行）。

        Args:
            auto_decay: 是否加载后自动衰减（默认 True）
            auto_consolidate: 是否加载后自动记忆园丁（默认 True）

        Returns:
            True 如果成功加载，False 如果是新数据库
        """
        self.data_path.parent.mkdir(parents=True, exist_ok=True)

        # 自动迁移：如果 .db 不存在但 .json 存在
        json_path = self.data_path.with_suffix(".json")
        if not self.data_path.exists() and json_path.exists():
            logger.info("检测到旧 mindmap.json，自动迁移到 SQLite...")
            self._migrate_json_to_sqlite(json_path)

        if not self.data_path.exists():
            logger.info("记忆数据库文件不存在，创建空数据库: %s", self.data_path)
            self.nodes = {}
            self.root_ids = []
            self.last_decay = _now_iso()
            return False

        try:
            conn = sqlite3.connect(str(self.data_path))
            conn.row_factory = sqlite3.Row

            # 确保表存在
            conn.execute("""CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY, value TEXT
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY, topic TEXT, content TEXT, score INTEGER DEFAULT 1,
                last_access TEXT, parent_id TEXT, children_ids TEXT, is_core INTEGER DEFAULT 0,
                deleted INTEGER DEFAULT 0, deleted_at TEXT DEFAULT '', is_deep INTEGER DEFAULT 0, created_at TEXT DEFAULT ''
            )""")
            # 向后兼容：旧 DB 缺少列时自动补充
            try:
                conn.execute("ALTER TABLE nodes ADD COLUMN deleted INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE nodes ADD COLUMN deleted_at TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE nodes ADD COLUMN is_deep INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE nodes ADD COLUMN created_at TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass

            # 加载元数据
            for row in conn.execute("SELECT key, value FROM meta"):
                if row["key"] == "version":
                    self.version = int(row["value"])
                elif row["key"] == "last_decay":
                    self.last_decay = row["value"]
                elif row["key"] == "last_consolidate":
                    self.last_consolidate = row["value"]
                elif row["key"] == "duplicates_skipped":
                    self._duplicates_skipped = int(row["value"])

            # 加载节点
            self.nodes = {}
            for row in conn.execute("SELECT * FROM nodes"):
                node = MemoryNode(
                    id=row["id"], topic=row["topic"] or "",
                    content=row["content"] or "", score=row["score"],
                    last_access=row["last_access"] or _now_iso(),
                    parent_id=row["parent_id"] or None,
                    children_ids=json.loads(row["children_ids"]) if row["children_ids"] else [],
                    is_core=bool(row["is_core"]),
                    deleted=bool(row["deleted"]) if "deleted" in row.keys() else False,
                    deleted_at=(row["deleted_at"] or "") if "deleted_at" in row.keys() else "",
                    is_deep=bool(row["is_deep"]) if "is_deep" in row.keys() else False,
                    created_at=(row["created_at"] or "") if "created_at" in row.keys() else "",
                )
                self.nodes[node.id] = node

            # 计算根节点
            self.root_ids = [nid for nid, n in self.nodes.items() if n.is_root and not n.deleted and not n.is_deep]

            # 从已有节点重建内容哈希集合
            self._content_hashes = set()
            for n in self.nodes.values():
                if n.content and not n.deleted:
                    self._content_hashes.add(hashlib.sha256(n.content.encode("utf-8")).hexdigest())

            conn.close()

            logger.info(
                "已加载 %d 个节点（%d 个根节点）",
                len(self.nodes), len(self.root_ids)
            )

            if auto_decay:
                self.decay_if_needed()

            # 自动记忆园丁：距上次超过 24 小时则执行
            if auto_consolidate:
                self.consolidate_if_needed()

            return True

        except (sqlite3.DatabaseError, json.JSONDecodeError) as e:
            logger.error("记忆数据库损坏: %s，将尝试恢复", e)
            backup_path = self.data_path.with_suffix(".db.bak")
            try:
                import shutil
                shutil.copy2(str(self.data_path), str(backup_path))
                logger.warning("已备份损坏文件至: %s", backup_path)
            except OSError:
                pass
            # 删除损坏文件，下次 save() 将创建新数据库
            try:
                self.data_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.nodes = {}
            self.root_ids = []
            self.last_decay = _now_iso()
            return False

    def _migrate_json_to_sqlite(self, json_path):
        """将旧 mindmap.json 数据迁移到 SQLite 数据库。"""
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.version = data.get("version", 1)
            self.last_decay = data.get("last_decay")
            self.nodes = {}
            for node_id, node_data in data.get("nodes", {}).items():
                self.nodes[node_id] = MemoryNode.from_dict(node_data)
            self.root_ids = data.get("root_ids", [])
            self.save()
            logger.info("迁移完成: %d 个节点从 mindmap.json 导入 mindmap.db", len(self.nodes))
            # 重命名旧文件为备份
            json_path.rename(json_path.with_suffix(".json.migrated"))
            logger.info("旧文件已备份为 mindmap.json.migrated")
        except Exception as e:
            logger.warning("JSON→SQLite 迁移失败: %s，将创建新数据库", e)
            self.nodes = {}
            self.root_ids = []
            self.last_decay = _now_iso()

    def save(self) -> bool:
        """将记忆树保存到 SQLite 数据库。

        使用事务保证写入原子性。全量替换模式，简单可靠。

        Returns:
            True 保存成功
        """
        self.data_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            conn = sqlite3.connect(str(self.data_path))
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("""CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY, topic TEXT, content TEXT, score INTEGER DEFAULT 1,
                last_access TEXT, parent_id TEXT, children_ids TEXT, is_core INTEGER DEFAULT 0,
                deleted INTEGER DEFAULT 0, deleted_at TEXT DEFAULT '', is_deep INTEGER DEFAULT 0, created_at TEXT DEFAULT ''
            )""")

            # 原子事务
            conn.execute("BEGIN")
            conn.execute("DELETE FROM meta")
            conn.execute("INSERT INTO meta VALUES (?, ?)", ("version", str(self.version)))
            if self.last_decay:
                conn.execute("INSERT INTO meta VALUES (?, ?)", ("last_decay", self.last_decay))
            if self.last_consolidate:
                conn.execute("INSERT INTO meta VALUES (?, ?)", ("last_consolidate", self.last_consolidate))
            if self._duplicates_skipped:
                conn.execute("INSERT INTO meta VALUES (?, ?)", ("duplicates_skipped", str(self._duplicates_skipped)))
            conn.execute("DELETE FROM nodes")
            for node in self.nodes.values():
                conn.execute(
                    "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (node.id, node.topic, node.content, node.score,
                     node.last_access, node.parent_id,
                     json.dumps(node.children_ids, ensure_ascii=False),
                     1 if node.is_core else 0,
                     1 if node.deleted else 0,
                     node.deleted_at,
                     1 if node.is_deep else 0,
                     node.created_at)
                )
            conn.commit()
            conn.close()
            logger.debug("已保存 %d 个节点", len(self.nodes))
            return True

        except sqlite3.DatabaseError as e:
            logger.error("保存记忆数据库失败: %s", e)
            try:
                conn.rollback()
            except Exception:
                pass
            return False

    # ------------------------------------------------------------------
    # 节点操作 — 基础 CRUD
    # ------------------------------------------------------------------

    def _get_depth(self, node_id: str) -> int:
        """递归计算节点在树中的深度（根=第1层）。

        Args:
            node_id: 节点 ID

        Returns:
            深度值 (1-based)
        """
        depth = 1
        current = self.nodes.get(node_id)
        while current and current.parent_id:
            depth += 1
            current = self.nodes.get(current.parent_id)
            if depth > MAX_DEPTH + 10:  # 安全上限，防止循环引用
                break
        return depth

    def _count_non_core_nodes(self) -> int:
        """统计非核心节点总数（排除已删除和树根节点）。"""
        return sum(1 for n in self.nodes.values() if not n.is_core and not n.deleted and not n.is_deep)

    def _generate_topic_and_keywords(self, content: str) -> Tuple[str, List[str]]:
        """从记忆内容自动生成话题标题和关键词。

        策略（按优先级）：
          1. 匹配 \"类别/子类别:\" 模式 → 提取类别作为话题
          2. 提取中文关键词（2-4字组合，过滤停用词）
          3. 提取英文关键词（2+字母单词）
          4. 选择最高频/最长关键词作为话题

        Args:
            content: 记忆内容

        Returns:
            (话题标题, 关键词列表)
        """
        content = content.strip()

        # 策略 1: 匹配 \"Category/Subcategory:\" 或 \"Category：Subcategory\" 模式
        structured_match = re.match(
            r'([\u4e00-\u9fff\w]+)\s*[/／]\s*([\u4e00-\u9fff\w]+)\s*[:：]',
            content
        )
        if structured_match:
            category = structured_match.group(1)
            subcategory = structured_match.group(2)
            # 用类别作为父话题，子类别作为候选关键词
            return category, [category, subcategory]

        # 策略 2: 提取有意义的关键词
        keywords = self.matcher.extract_keywords(content)

        if not keywords:
            # 回退到简单截断
            cleaned = re.sub(r'\s+', ' ', content)
            return cleaned[:20] + ("…" if len(cleaned) > 20 else ""), []

        # 策略 3: 选最高频/最长的中文关键词作为话题
        # 优先选中文关键词（更有话题性），其次选英文
        chinese_kw = [k for k in keywords if re.search(r'[\u4e00-\u9fff]', k)]
        english_kw = [k for k in keywords if not re.search(r'[\u4e00-\u9fff]', k)]

        if chinese_kw:
            # 选择最长且不在常见词列表中的中文关键词
            chinese_kw.sort(key=lambda x: -len(x))
            topic = chinese_kw[0]
            return topic, keywords
        elif english_kw:
            english_kw.sort(key=lambda x: -len(x))
            topic = english_kw[0]
            return topic, keywords
        else:
            return keywords[0], keywords

    def _generate_topic_from_content(self, content: str) -> str:
        """从记忆内容自动生成话题标题（兼容旧接口）。

        Args:
            content: 记忆内容

        Returns:
            生成的话题标题
        """
        topic, _ = self._generate_topic_and_keywords(content)
        return topic

    def add_node(
        self,
        topic: str,
        content: str = "",
        parent_id: Optional[str] = None,
    ) -> str:
        """添加一个新节点到记忆树。

        自动处理:
        - 深度限制（超过 MAX_DEPTH 合并到第 6 层父节点）
        - 节点总数限制（超过 MAX_NON_CORE_NODES 删除最低分节点）
        - 子节点列表维护
        - 自动保存

        Args:
            topic: 节点标题
            content: 节点内容（叶子节点使用）
            parent_id: 父节点 ID，None 则为根节点

        Returns:
            新节点的 ID
        """
        # 深度检查
        if parent_id and self._get_depth(parent_id) >= MAX_DEPTH:
            # 超过最大深度，不创建子节点，将内容合并到父节点
            parent = self.nodes.get(parent_id)
            if parent:
                logger.info(
                    "已达到最大深度 %d，将内容合并到父节点 '%s'",
                    MAX_DEPTH, parent.topic
                )
                if parent.content:
                    parent.content += "\n\n" + content
                else:
                    parent.content = content
                parent.last_access = _now_iso()
                # 哈希去重：注册合并后的内容哈希
                if content:
                    self._content_hashes.add(hashlib.sha256(content.encode("utf-8")).hexdigest())
                if parent.content:
                    self._content_hashes.add(hashlib.sha256(parent.content.encode("utf-8")).hexdigest())
                if not self.save():
                    logger.error("保存失败（可能磁盘满或权限不足），已回退")
                    return ""
                return parent_id
            # 父节点不存在，降级为根节点
            parent_id = None

        # 节点总数检查
        non_core_count = self._count_non_core_nodes()
        if non_core_count >= MAX_NON_CORE_NODES:
            # 找到最低分且最久未访问的非核心节点并删除
            candidates = [
                (nid, n) for nid, n in self.nodes.items()
                if not n.is_core and not n.is_root
            ]
            if candidates:
                # 按分数升序、最后访问时间升序排序
                candidates.sort(key=lambda x: (x[1].score, x[1].last_access))
                victim_id, victim = candidates[0]
                logger.warning(
                    "非核心节点已达上限 %d，删除最低分节点 '%s' (score=%d)",
                    MAX_NON_CORE_NODES, victim.topic, victim.score
                )
                self._log_decay([victim], "容量上限清理")
                self._remove_node_cascade(victim_id)

        # 创建节点
        node = MemoryNode(
            topic=topic,
            content=content,
            parent_id=parent_id,
            created_at=_now_iso(),
        )

        self.nodes[node.id] = node

        # 维护父子关系
        if parent_id and parent_id in self.nodes:
            parent = self.nodes[parent_id]
            parent.children_ids.append(node.id)
        else:
            self.root_ids.append(node.id)

        if not self.save():
            # 保存失败 — 回退内存中的变更
            logger.error("保存失败（可能磁盘满或权限不足），回退节点 '%s'", topic)
            if parent_id and parent_id in self.nodes:
                parent = self.nodes[parent_id]
                if node.id in parent.children_ids:
                    parent.children_ids.remove(node.id)
            if node.id in self.root_ids:
                self.root_ids.remove(node.id)
            self.nodes.pop(node.id, None)
            return ""
        # 哈希去重：成功添加后注册内容哈希
        if content:
            self._content_hashes.add(hashlib.sha256(content.encode("utf-8")).hexdigest())
        logger.info("已添加节点 '%s' (id=%s, 深度=%d)", topic, node.id[:8], self._get_depth(node.id))
        return node.id

    def _remove_node_cascade(self, node_id: str) -> None:
        """级联删除节点及其所有子节点。

        递归删除整棵子树，同时维护父节点的 children_ids。
        核心记忆子节点不受级联影响——它们会被提升为根节点。

        Args:
            node_id: 要删除的节点 ID
        """
        if node_id not in self.nodes:
            return

        node = self.nodes[node_id]

        # 处理子节点：核心记忆提升为根节点，非核心记忆级联删除
        for child_id in list(node.children_ids):
            child = self.nodes.get(child_id)
            if child and child.is_core:
                # 核心记忆子节点不受父节点衰减影响，提升为根节点
                child.parent_id = None
                self.root_ids.append(child_id)
                if child_id in node.children_ids:
                    node.children_ids.remove(child_id)
                logger.info(
                    "核心记忆 '%s' 从已删除父节点 '%s' 提升为根节点",
                    child.topic, node.topic
                )
            else:
                self._remove_node_cascade(child_id)

        # 从父节点的 children_ids 中移除
        if node.parent_id and node.parent_id in self.nodes:
            parent = self.nodes[node.parent_id]
            if node_id in parent.children_ids:
                parent.children_ids.remove(node_id)

        # 从根节点列表中移除
        if node_id in self.root_ids:
            self.root_ids.remove(node_id)

        # 软删除节点本身（保留数据，标记 deleted）
        node = self.nodes[node_id]
        node.deleted = True
        node.deleted_at = _now_iso()
        logger.debug("已软删除节点 '%s' (%s)", node.topic, node_id[:8])

    def _log_decay(self, removed_nodes: List[MemoryNode], reason: str = "衰减删除") -> None:
        """将删除的节点写入遗忘日志。

        日志按日期分文件，方便后悔时查找。

        Args:
            removed_nodes: 被删除的节点列表
            reason: 删除原因
        """
        self.decay_log_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now().strftime("%Y-%m-%d")
        log_path = self.decay_log_dir / f"{today}.json"

        # 加载已有日志（如果存在）
        existing = []
        if log_path.exists():
            try:
                existing = json.loads(log_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        # 追加新记录
        for node in removed_nodes:
            existing.append({
                "timestamp": _now_iso(),
                "reason": reason,
                "node": node.to_dict(),
                "depth": self._get_depth(node.id) if node.id in self.nodes else "已删除",
            })

        try:
            log_path.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            logger.info("已记录 %d 条遗忘日志到 %s", len(removed_nodes), log_path)
        except OSError as e:
            logger.error("写入遗忘日志失败: %s", e)

    # ------------------------------------------------------------------
    # 语义匹配与分类 — 自动放置新记忆
    # ------------------------------------------------------------------

    def _find_best_match(
        self,
        content: str,
        candidates: List[str],
    ) -> Tuple[Optional[str], float]:
        """在候选节点列表中寻找与内容最匹配的节点。

        同时匹配节点 topic 和 content 的开头部分，提高召回率。

        Args:
            content: 待分类的内容
            candidates: 候选节点 ID 列表

        Returns:
            (最佳匹配节点ID, 相似度分数) 或 (None, 0.0)
        """
        best_id = None
        best_score = 0.0

        # 批量模式：收集候选文本，一次编码
        node_ids = []
        search_texts = []
        for node_id in candidates:
            node = self.nodes.get(node_id)
            if not node or node.deleted:
                continue
            search_text = node.topic
            if node.content:
                search_text += " " + node.content[:80]
            node_ids.append(node_id)
            search_texts.append(search_text)

        if not node_ids:
            return None, 0.0

        sims = self.classify_matcher.batch_similarity(content, search_texts)
        for node_id, sim in zip(node_ids, sims):
            if sim > best_score:
                best_score = sim
                best_id = node_id

        return best_id, best_score

    def add_memory(self, content: str, topic_hint: str = "") -> str:
        """添加一条新记忆，自动分类到话题树中。

        工作流程:
          1. 使用智能话题提取 → 短关键词作为话题
          2. 如果内容有 \"类别/子类:\" 结构，自动创建二级层级
          3. 遍历根节点，寻找最匹配的话题
          4. 如果匹配度 > MATCH_THRESHOLD，在该话题下继续下钻
          5. 如果匹配度低，创建新的根节点

        Args:
            content: 记忆内容
            topic_hint: 可选的话题提示（用于辅助分类）

        Returns:
            新创建的叶子节点 ID
        """
        if not content or not content.strip():
            logger.warning("忽略空内容")
            return ""

        content = content.strip()

        # 内容哈希去重：相同 SHA-256 哈希跳过（已有相同内容不再重复添加）
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if content_hash in self._content_hashes:
            self._duplicates_skipped += 1
            logger.debug("跳过重复内容 (sha256=%s...): %s", content_hash[:16], content[:80])
            return ""

        # 智能提取话题和关键词
        auto_topic, keywords = self._generate_topic_and_keywords(content)
        topic = topic_hint.strip() if topic_hint else auto_topic

        # 检查是否有结构化分类（\"类别/子类:\" 模式）
        structured_match = re.match(
            r'([\u4e00-\u9fff\w]+)\s*[/／]\s*([\u4e00-\u9fff\w]+)\s*[:：]',
            content
        )

        if structured_match and not topic_hint:
            # 有结构化分类：先找到或创建父话题，再在父话题下创建子话题叶子
            category = structured_match.group(1)
            subcategory = structured_match.group(2)

            # 找或创建父话题
            parent_id = None
            for rid in self.root_ids:
                root = self.nodes.get(rid)
                if root and root.topic == category:
                    parent_id = rid
                    break

            if not parent_id:
                # 创建新父话题
                parent_id = self.add_node(topic=category, content="")
                logger.info("创建父话题 '%s'", category)

            # 在父话题下找或创建子话题
            child_id = None
            parent = self.nodes[parent_id]
            for cid in parent.children_ids:
                child = self.nodes.get(cid)
                if child and child.topic == subcategory:
                    child_id = cid
                    break

            if child_id:
                # 子话题已存在，添加内容作为叶子
                return self.add_node(topic=subcategory, content=content, parent_id=child_id)
            else:
                return self.add_node(topic=subcategory, content=content, parent_id=parent_id)

        # 如果没有根节点，直接创建为根
        if not self.root_ids:
            return self.add_node(topic=topic, content=content)

        # 在根节点中寻找最佳匹配（使用内容+关键词增强匹配）
        search_text = content
        if keywords:
            search_text = " ".join(keywords[:3]) + " " + content

        best_root_id, root_sim = self._find_best_match(search_text, self.root_ids)

        if best_root_id and root_sim >= MATCH_THRESHOLD:
            # 在匹配到的根话题下继续下钻
            return self._drill_down_add(content, topic, best_root_id, depth=1)
        else:
            # 创建新根节点
            logger.info(
                "未找到匹配话题 (最高相似度 %.2f < %.2f)，创建新根节点 '%s'",
                root_sim, MATCH_THRESHOLD, topic
            )
            return self.add_node(topic=topic, content=content)

    def _drill_down_add(
        self,
        content: str,
        topic: str,
        parent_id: str,
        depth: int,
    ) -> str:
        """在父节点下递归寻找合适的插入位置。

        Args:
            content: 记忆内容
            topic: 生成的话题标题
            parent_id: 当前父节点 ID
            depth: 当前深度

        Returns:
            新创建节点的 ID，或合并到的节点 ID
        """
        parent = self.nodes.get(parent_id)
        if not parent:
            return self.add_node(topic=topic, content=content)

        # 达到最大深度或父节点没有子节点 → 作为叶子插入
        if depth >= MAX_DEPTH - 1 or not parent.children_ids:
            return self.add_node(topic=topic, content=content, parent_id=parent_id)

        # 在子节点中寻找匹配
        best_child_id, child_sim = self._find_best_match(content, parent.children_ids)

        if best_child_id and child_sim >= MATCH_THRESHOLD:
            # 继续下钻
            return self._drill_down_add(content, topic, best_child_id, depth + 1)
        else:
            # 在当前层级创建新叶子
            return self.add_node(topic=topic, content=content, parent_id=parent_id)

    # ------------------------------------------------------------------
    # 检索 — 逐层下钻
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        max_depth: int = MAX_DEPTH,
    ) -> List[MemoryNode]:
        """检索与查询最相关的记忆节点。

        逐层下钻算法:
          1. 在所有根节点中找最匹配的话题
          2. 进入该话题，评估是否下钻子话题
          3. 重复直到叶子节点或达到 max_depth
          4. 同层多候选时：分数高优先，同分则最近访问优先
          5. 最终匹配到的节点执行 +1 加分

        Args:
            query: 检索查询
            max_depth: 最大搜索深度

        Returns:
            匹配到的节点列表（按相关性排序）
        """
        if not query or not self.root_ids:
            return []

        results = []
        visited = set()

        # 第一层：在所有根节点中寻找匹配
        matches = self._search_at_level(query, self.root_ids)
        if not matches:
            return []

        for matched_id, sim in matches[:3]:  # 最多取 3 个根话题
            if matched_id in visited:
                continue
            visited.add(matched_id)

            # 逐层下钻
            drill_results = self._drill_down_search(
                query, matched_id, depth=1, max_depth=max_depth, visited=visited
            )
            results.extend(drill_results)

        # 对结果去重并排序
        seen_ids = set()
        unique_results = []
        for node in results:
            if node.id not in seen_ids:
                seen_ids.add(node.id)
                unique_results.append(node)

        unique_results.sort(key=lambda n: (-n.score, n.last_access))

        # 对匹配到的节点执行 +1 加分
        for node in unique_results:
            self._apply_access_bonus(node.id)

        # 宽搜兜底：下钻结果 < 5 条时，混合搜索匹配所有活跃叶子
        if len(unique_results) < 5:
            all_leaves = [
                n for n in self.nodes.values()
                if n.is_leaf and n.content and not n.deleted and not n.is_deep
                and n.id not in seen_ids
            ]
            if all_leaves:
                leaf_texts = [n.content for n in all_leaves]
                # 使用混合搜索（BM25 + 语义相似度 + RRF 融合）
                hybrid_results = self.matcher.hybrid_search(query, leaf_texts)
                self._hybrid_search_count += 1
                # RRF 分数阈值：正值表示至少在一个排序中命中
                for idx, rrf_score in hybrid_results:
                    if rrf_score > 0 and all_leaves[idx].id not in seen_ids:
                        seen_ids.add(all_leaves[idx].id)
                        unique_results.append(all_leaves[idx])
                        self._apply_access_bonus(all_leaves[idx].id)

        # 树根回退：活跃树无结果时，搜索深池
        if not unique_results:
            deep_hits = self._search_deep(query)
            unique_results.extend(deep_hits)

        self.save()
        return unique_results

    def _search_deep(self, query: str) -> List[MemoryNode]:
        """搜索树根深池（is_deep=True 的节点）。

        无嵌入模型时使用更宽松的阈值（DEEP_MATCH_THRESHOLD）。
        命中后自动恢复：is_deep=False，score 恢复为可访问状态。
        """
        deep_nodes = [
            n for n in self.nodes.values()
            if n.is_deep and not n.deleted and n.content
        ]
        if not deep_nodes:
            return []

        # 检查是否有嵌入模型（通过 matcher 类型判断）
        has_embedding = not isinstance(self.matcher, KeywordModel)

        threshold = MATCH_THRESHOLD if has_embedding else DEEP_MATCH_THRESHOLD

        node_texts = [n.content for n in deep_nodes]
        sims = self.matcher.batch_similarity(query, node_texts)

        hits = []
        for node, sim in zip(deep_nodes, sims):
            if sim >= threshold:
                # 命中！重新生长枝叶——恢复到活跃树
                node.is_deep = False
                node.score = NEW_NODE_SCORE + ACCESS_SCORE_INCREMENT
                node.last_access = _now_iso()
                hits.append(node)
                logger.info(
                    "树根命中！'%s' 重新生长枝叶 (sim=%.3f)",
                    node.topic, sim
                )

        if hits:
            logger.info("树根检索命中 %d 条记忆，已恢复至活跃树", len(hits))

        return hits

    def _search_at_level(
        self,
        query: str,
        candidates: List[str],
    ) -> List[Tuple[str, float]]:
        """在指定候选节点列表中搜索匹配。

        使用混合搜索（BM25 稀疏检索 + 关键词相似度 + RRF 融合），
        同时匹配节点的 topic 和 content（对于叶子节点，content 包含完整记忆）。
        按分数和最近访问时间排序。

        Args:
            query: 查询文本
            candidates: 候选节点 ID 列表

        Returns:
            [(节点ID, 相似度), ...] 按相关性排序
        """
        # 收集所有候选文本
        node_ids = []
        search_texts = []
        for node_id in candidates:
            node = self.nodes.get(node_id)
            if not node or node.deleted:
                continue
            search_text = node.topic
            if node.content:
                search_text += " " + node.content[:200]
            if node.children_ids:
                child_topics = " ".join(
                    self.nodes[cid].topic
                    for cid in node.children_ids[:10]
                    if cid in self.nodes
                )
                search_text += " " + child_topics
            node_ids.append(node_id)
            search_texts.append(search_text)

        if not node_ids:
            return []

        # 使用混合搜索（BM25 + 语义相似度 + RRF 融合）
        hybrid_results = self.matcher.hybrid_search(query, search_texts)
        self._hybrid_search_count += 1

        scored = []
        for idx, rrf_score in hybrid_results:
            if rrf_score > 0:  # RRF 正值表示至少在一个排序中命中
                scored.append((node_ids[idx], rrf_score))

        # 先按相似度排序，同相似度按分数+时间排序
        scored.sort(
            key=lambda x: (
                -x[1],                                    # RRF分数越高越好
                -self.nodes[x[0]].score,                  # 分数越高越好
                self.nodes[x[0]].last_access,             # 时间越新越好
            )
        )
        return scored

    def _drill_down_search(
        self,
        query: str,
        node_id: str,
        depth: int,
        max_depth: int,
        visited: set,
    ) -> List[MemoryNode]:
        """递归下钻检索。

        Args:
            query: 查询文本
            node_id: 当前节点 ID
            depth: 当前深度
            max_depth: 最大深度
            visited: 已访问节点集合

        Returns:
            匹配到的节点列表
        """
        node = self.nodes.get(node_id)
        if not node:
            return []

        # 没有子节点 → 返回自身（叶子节点）
        if not node.children_ids or depth >= max_depth:
            return [node]

        # 在子节点中搜索匹配
        child_matches = self._search_at_level(query, node.children_ids)

        if not child_matches:
            # 子节点无匹配 → 返回当前节点
            return [node]

        # 有子节点匹配 → 继续下钻
        results = []
        for child_id, _ in child_matches[:2]:  # 最多下钻 2 个子话题
            if child_id in visited:
                continue
            visited.add(child_id)
            drill = self._drill_down_search(
                query, child_id, depth + 1, max_depth, visited
            )
            results.extend(drill)

        # 如果下钻后没有新结果，返回当前节点
        return results if results else [node]

    def recall(self) -> List[MemoryNode]:
        """返回全部根节点及其子树结构（用于查看完整记忆树）。

        Returns:
            根节点列表
        """
        return [self.nodes[rid] for rid in self.root_ids if rid in self.nodes and not self.nodes[rid].deleted]

    def get_subtree(self, node_id: str) -> List[MemoryNode]:
        """获取某个节点的完整子树。

        Args:
            node_id: 根节点 ID

        Returns:
            子树中所有节点的列表（BFS 顺序）
        """
        if node_id not in self.nodes:
            return []

        result = []
        queue = [node_id]
        while queue:
            current_id = queue.pop(0)
            node = self.nodes.get(current_id)
            if node:
                result.append(node)
                queue.extend(node.children_ids)

        return result

    # ------------------------------------------------------------------
    # 访问加分 — 只加匹配节点，不加父节点
    # ------------------------------------------------------------------

    def _apply_access_bonus(self, node_id: str) -> None:
        """对指定节点应用访问加分。

        规则:
          - 直接匹配节点: +1 分（ACCESS_SCORE_INCREMENT）
          - 父节点: 不加分（除非也直接匹配）

        Args:
            node_id: 被访问的节点 ID
        """
        node = self.nodes.get(node_id)
        if not node:
            return

        node.last_access = _now_iso()
        node.score += ACCESS_SCORE_INCREMENT
        logger.debug("节点 '%s' 加分至 %d", node.topic, node.score)

    # ------------------------------------------------------------------
    # 核心记忆标记
    # ------------------------------------------------------------------

    def set_core(self, node_id: str, is_core: bool) -> bool:
        """设置节点的核心记忆标记。

        核心记忆的 score 永远不会降到 1 以下。

        Args:
            node_id: 节点 ID
            is_core: 是否为核心记忆

        Returns:
            True 操作成功
        """
        node = self.nodes.get(node_id)
        if not node:
            logger.warning("节点 %s 不存在", node_id)
            return False

        node.is_core = is_core
        # 如果设为核心且分数低于最低保护值，提升到最低值
        if is_core and node.score < CORE_MIN_SCORE:
            node.score = CORE_MIN_SCORE

        self.save()
        logger.info("节点 '%s' 核心标记已设为 %s", node.topic, is_core)
        return True

    # ------------------------------------------------------------------
    # 编辑 — replace / remove（完整 CRUD）
    # ------------------------------------------------------------------

    def replace_memory(self, search_text: str, new_content: str) -> dict:
        """按内容子串查找并替换记忆。

        在节点的 topic 和 content 中搜索 search_text，找到唯一匹配后替换。
        多个匹配时返回候选列表让用户细化。

        Args:
            search_text: 用于查找的文本片段
            new_content: 替换后的新内容

        Returns:
            {"success": bool, "replaced": int, "message": str, "candidates": [...]}
        """
        if not search_text or not search_text.strip():
            return {"success": False, "error": "search_text 不能为空"}
        if not new_content or not new_content.strip():
            return {"success": False, "error": "new_content 不能为空"}

        search_text = search_text.strip()
        new_content = new_content.strip()

        matches = []
        for nid, node in self.nodes.items():
            if search_text in node.topic or search_text in node.content:
                matches.append((nid, node))

        if not matches:
            return {"success": False, "error": f"未找到匹配 '{search_text[:50]}' 的记忆"}

        if len(matches) > 1:
            return {
                "success": False,
                "error": f"找到 {len(matches)} 条匹配，请细化搜索条件",
                "candidates": [
                    {"node_id": nid[:12], "topic": n.topic, "preview": n.content[:80]}
                    for nid, n in matches[:10]
                ],
            }

        node_id, node = matches[0]
        old_topic = node.topic

        node.content = new_content
        node.last_access = _now_iso()

        new_topic, _ = self._generate_topic_and_keywords(new_content)
        if new_topic and new_topic != old_topic:
            node.topic = new_topic

        self.save()
        logger.info(
            "已替换节点 '%s' → '%s' (id=%s)",
            old_topic, node.topic, node_id[:8]
        )
        return {
            "success": True,
            "replaced": 1,
            "node_id": node_id,
            "old_topic": old_topic,
            "new_topic": node.topic,
            "message": f"已更新记忆: {old_topic} → {node.topic}",
        }

    def remove_memory(self, search_text: str, force: bool = False) -> dict:
        """按内容子串查找并删除记忆。

        删除时会级联删除子节点（核心子节点提升为根节点），
        并写入遗忘日志以便后悔恢复。

        Args:
            search_text: 用于查找的文本片段
            force: 是否强制删除（跳过核心保护确认）

        Returns:
            {"success": bool, "removed": int, "message": str, "candidates": [...]}
        """
        if not search_text or not search_text.strip():
            return {"success": False, "error": "search_text 不能为空"}

        search_text = search_text.strip()

        matches = []
        for nid, node in self.nodes.items():
            if search_text in node.topic or search_text in node.content:
                matches.append((nid, node))

        if not matches:
            return {"success": False, "error": f"未找到匹配 '{search_text[:50]}' 的记忆"}

        if len(matches) > 1:
            return {
                "success": False,
                "error": f"找到 {len(matches)} 条匹配，请细化搜索条件",
                "candidates": [
                    {
                        "node_id": nid[:12],
                        "topic": n.topic,
                        "is_core": n.is_core,
                        "preview": n.content[:80],
                    }
                    for nid, n in matches[:10]
                ],
            }

        node_id, node = matches[0]

        if node.is_core and not force:
            return {
                "success": False,
                "error": f"'{node.topic}' 是核心记忆，删除需要 force=true",
                "hint": "如果确实要删除核心记忆，使用 force=True 参数",
            }

        subtree_before = self.get_subtree(node_id)
        children_count = len(subtree_before) - 1

        self._remove_node_cascade(node_id)
        actually_removed = [n for n in subtree_before if n.deleted]
        core_tag = " ⭐核心" if node.is_core else ""
        self._log_decay(actually_removed, f"手动删除{core_tag}")

        self.save()
        logger.info(
            "手动删除节点 '%s'%s 及其 %d 个子节点",
            node.topic, core_tag, children_count
        )
        return {
            "success": True,
            "removed": len(actually_removed),
            "topic": node.topic,
            "was_core": node.is_core,
            "children_removed": children_count,
            "message": f"已删除记忆: {node.topic} (含 {children_count} 个子节点)",
        }

    # ------------------------------------------------------------------
    # 记忆园丁 — 嵌入模型重分类当天记忆
    # ------------------------------------------------------------------

    def _should_consolidate(self) -> bool:
        """判断是否需要执行记忆园丁（距上次超过 24 小时）。"""
        if not self.last_consolidate:
            return True
        try:
            last = datetime.fromisoformat(self.last_consolidate.replace("Z", "+00:00"))
            elapsed = datetime.now() - last.replace(tzinfo=None)
            return elapsed.total_seconds() > 86400  # 24 小时
        except (ValueError, TypeError):
            return True

    def consolidate_if_needed(self) -> int:
        """如果需要，执行记忆园丁。"""
        if not self._should_consolidate():
            return 0
        return self.consolidate_today()

    def consolidate_today(self) -> int:
        """用嵌入模型重新分类今天新增的叶子节点。

        仅在配置了嵌入模型（支持 batch_similarity）时生效。
        对每个今天新增的叶子，计算它和所有根话题的嵌入相似度，
        如果有更优的父话题（相似度提升 > 0.10），则迁移。

        Returns:
            重新分类的节点数量
        """
        # 只有嵌入模型才值得重分类
        if not hasattr(self.matcher, 'batch_similarity') or \
           type(self.matcher).__name__ == 'KeywordModel':
            self.last_consolidate = _now_iso()
            self.save()
            return 0

        today_prefix = datetime.now().strftime("%Y-%m-%d")
        today_leaves = [
            (nid, n) for nid, n in self.nodes.items()
            if n.is_leaf and n.content and not n.deleted
            and (n.last_access.startswith(today_prefix) or 
                 (n.deleted_at and n.deleted_at.startswith(today_prefix)))
        ]
        if len(today_leaves) < 2:
            self.last_consolidate = _now_iso()
            self.save()
            return 0

        # 收集可见根话题
        visible_roots = [
            (rid, self.nodes[rid]) for rid in self.root_ids
            if rid in self.nodes and not self.nodes[rid].deleted
        ]
        if not visible_roots:
            return 0

        root_texts = [r.topic for _, r in visible_roots]
        migrated = 0

        for nid, node in today_leaves:
            if not root_texts:
                break
            # 跳过已经是根节点的
            if node.is_root:
                continue
            sims = self.matcher.batch_similarity(node.content, root_texts)
            best_idx = max(range(len(sims)), key=lambda i: sims[i])
            best_root_id, best_root = visible_roots[best_idx]
            best_sim = sims[best_idx]

            # 当前父话题相似度
            current_sim = 0.0
            if node.parent_id and node.parent_id in self.nodes:
                parent = self.nodes[node.parent_id]
                current_sims = self.matcher.batch_similarity(
                    node.content, [parent.topic]
                )
                current_sim = current_sims[0] if current_sims else 0.0

            # 只有明显提升才迁移（避免抖动）
            if best_sim > current_sim + 0.10 and best_root_id != node.parent_id:
                old_parent = node.parent_id
                # 从旧父节点移除
                if old_parent and old_parent in self.nodes:
                    old = self.nodes[old_parent]
                    if nid in old.children_ids:
                        old.children_ids.remove(nid)
                # 挂到新根话题下
                node.parent_id = best_root_id
                best_root.children_ids.append(nid)
                node.last_access = _now_iso()
                migrated += 1
                logger.info(
                    "记忆园丁: '%s' 从 '%s' 迁移到 '%s' (sim %.3f → %.3f)",
                    node.topic[:20],
                    self.nodes[old_parent].topic if old_parent and old_parent in self.nodes else "根",
                    best_root.topic[:20],
                    current_sim, best_sim
                )

        self.last_consolidate = _now_iso()
        if migrated > 0:
            self.save()
            logger.info("记忆园丁完成: %d/%d 个节点重新分类", migrated, len(today_leaves))
        return migrated

    # ------------------------------------------------------------------
    # 遗忘衰减机制
    # ------------------------------------------------------------------

    def _should_decay(self) -> bool:
        """判断是否需要进行衰减扫描。

        条件: 距离上次衰减已超过 DECAY_INTERVAL_DAYS 天。

        Returns:
            True 需要衰减
        """
        if not self.last_decay:
            return True  # 首次运行

        try:
            last = datetime.fromisoformat(self.last_decay.replace("Z", "+00:00"))
            elapsed = datetime.now() - last.replace(tzinfo=None)
            return elapsed.days >= DECAY_INTERVAL_DAYS
        except (ValueError, TypeError):
            return True

    def decay_if_needed(self) -> List[MemoryNode]:
        """如果需要，执行衰减扫描。

        衰减规则:
          - 遍历所有节点
          - 如果 last_access 距今 > 7 天，score -= 1
          - 核心记忆 score 不低于 CORE_MIN_SCORE (3)
          - score <= 2 且非核心: 沉入树根（is_deep=True），不删除
          - 树根节点超过 DEEP_RETENTION_DAYS 天未访问: 软删除

        Returns:
            被删除的节点列表
        """
        if not self._should_decay():
            logger.debug("距离上次衰减不足 %d 天，跳过", DECAY_INTERVAL_DAYS)
            return []

        logger.info("开始每周衰减扫描...")
        removed = []
        sunk_count = 0

        now = datetime.now()
        cutoff = now - timedelta(days=DECAY_INTERVAL_DAYS)

        # 第一阶段：活跃树衰减 + 下沉
        for node_id, node in list(self.nodes.items()):
            if node.deleted or node.is_deep:
                continue  # 跳过软删除和已在树根的节点

            try:
                last_access = datetime.fromisoformat(
                    node.last_access.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except (ValueError, TypeError):
                continue

            days_since_access = (now - last_access).days

            if days_since_access >= DECAY_INTERVAL_DAYS:
                if node.is_core:
                    # 核心记忆保护：不低于 CORE_MIN_SCORE
                    if node.score > CORE_MIN_SCORE:
                        old_score = node.score
                        node.score = max(CORE_MIN_SCORE, node.score - DECAY_AMOUNT)
                        logger.debug(
                            "核心节点 '%s' 衰减: %d → %d (保护下限: %d)",
                            node.topic, old_score, node.score, CORE_MIN_SCORE
                        )
                    elif node.score < CORE_MIN_SCORE:
                        # 核心记忆分数异常低 → 恢复到保护值
                        node.score = CORE_MIN_SCORE
                        logger.debug(
                            "核心节点 '%s' 分数恢复: %d → %d",
                            node.topic, node.score - DECAY_AMOUNT, CORE_MIN_SCORE
                        )
                else:
                    node.score -= DECAY_AMOUNT
                    logger.debug(
                        "节点 '%s' 衰减: score=%d, 距上次访问 %d 天",
                        node.topic, node.score, days_since_access
                    )

                    # score ≤ 2 → 沉入树根（不删除，归档）
                    if node.score <= 2:
                        node.is_deep = True
                        sunk_count += 1
                        logger.info(
                            "节点 '%s' 沉入树根 (score=%d, 距上次访问 %d 天)",
                            node.topic, node.score, days_since_access
                        )

        # 第二阶段：树根时间淘汰（超过 DEEP_RETENTION_DAYS 天）
        deep_cutoff = now - timedelta(days=DEEP_RETENTION_DAYS)
        to_remove = []

        for node_id, node in list(self.nodes.items()):
            if not node.is_deep or node.deleted:
                continue
            try:
                last_access = datetime.fromisoformat(
                    node.last_access.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except (ValueError, TypeError):
                continue

            if last_access < deep_cutoff:
                to_remove.append(node_id)

        # 按天分组淘汰（最早的一天先删）
        if to_remove:
            to_remove.sort(key=lambda nid: self.nodes[nid].last_access)
            # 每次最多清理一天的节点
            first_day = self.nodes[to_remove[0]].last_access[:10] if to_remove else ""
            day_nodes = [nid for nid in to_remove if self.nodes[nid].last_access[:10] == first_day]

            for node_id in day_nodes:
                if node_id not in self.nodes:
                    continue
                node = self.nodes[node_id]
                subtree_before = self.get_subtree(node_id)
                self._remove_node_cascade(node_id)
                actually_removed = [n for n in subtree_before if n.deleted]
                removed.extend(actually_removed)
                logger.info(
                    "树根淘汰节点 '%s' (归档 %d 天) 及其 %d 个子节点",
                    node.topic,
                    (now - last_access).days if 'last_access' in dir() else 0,
                    len(actually_removed) - 1
                )

        # 记录日志
        if sunk_count:
            logger.info("本轮共 %d 个节点沉入树根", sunk_count)
        if removed:
            self._log_decay(removed, "树根时间淘汰")
            logger.info("树根淘汰共删除 %d 个节点", len(removed))
        elif not sunk_count:
            logger.info("衰减扫描完成，无需操作")

        self.last_decay = now.isoformat()
        self.save()
        return removed

    # ------------------------------------------------------------------
    # 迁移 — 从旧扁平 MEMORY.md 导入
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 恢复 — 找回被软删除的记忆
    # ------------------------------------------------------------------

    def recover_memory(self, search_text: str = "") -> dict:
        """恢复被软删除的记忆。

        不加参数时列出最近被删的节点（最多 20 条）。
        提供 search_text 时，在已删除节点中搜索并恢复匹配项。
        """
        deleted_nodes = [
            (nid, n) for nid, n in self.nodes.items()
            if n.deleted
        ]
        if not deleted_nodes:
            return {"success": True, "recovered": 0, "message": "没有被软删除的记忆"}

        deleted_nodes.sort(key=lambda x: x[1].deleted_at or "", reverse=True)

        if not search_text or not search_text.strip():
            return {
                "success": True, "recovered": 0,
                "message": f"找到 {len(deleted_nodes)} 条已删除记忆",
                "candidates": [
                    {"node_id": nid[:12], "topic": n.topic,
                     "deleted_at": n.deleted_at[:19] if n.deleted_at else "未知",
                     "preview": (n.content or n.topic)[:80]}
                    for nid, n in deleted_nodes[:20]
                ],
            }

        search_text = search_text.strip()
        matches = [(nid, n) for nid, n in deleted_nodes
                   if search_text in n.topic or search_text in (n.content or "")]
        if not matches:
            return {"success": False, "error": f"已删除记忆中未找到 '{search_text[:50]}'"}

        recovered = 0
        for nid, node in matches:
            node.deleted = False
            node.deleted_at = ""
            node.last_access = _now_iso()
            if node.parent_id:
                parent = self.nodes.get(node.parent_id)
                if parent and parent.deleted:
                    node.parent_id = None
            if node.is_root and nid not in self.root_ids:
                self.root_ids.append(nid)
            recovered += 1
            logger.info("已恢复节点 '%s' (%s)", node.topic, nid[:8])

        self.save()
        return {"success": True, "recovered": recovered,
                "message": f"已恢复 {recovered} 条记忆"}

    # ------------------------------------------------------------------
    # 同步 — 从原生 memory 工具增量导入新增条目
    # ------------------------------------------------------------------

    def sync_from_native(self) -> int:
        """扫描 MEMORY.md，将 mindmap.db 中没有的条目自动纳入。

        在每次 write_index_to_md() 前自动调用（修剪同步），
        也暴露为独立的 CLI 命令和 API。

        Returns:
            新导入的条目数量，0 表示无新增
        """
        mem_dir = _get_memories_dir()
        md_path = mem_dir / "MEMORY.md"

        if not md_path.exists():
            return 0

        try:
            raw = md_path.read_text(encoding="utf-8")
        except OSError:
            return 0

        if not raw.strip():
            return 0

        # 按 § 分隔符拆分条目
        entries = [e.strip() for e in raw.split("\n§\n")]
        entries = [e for e in entries if e]

        # 收集已有记忆的内容指纹（去重依据）
        existing_contents = {n.content.strip()[:200] for n in self.nodes.values() if n.content.strip()}
        existing_topics = {n.topic.strip() for n in self.nodes.values() if n.topic.strip()}

        imported = 0
        for entry in entries:
            if not entry.strip():
                continue
            # 跳过空行 / 索引头 / 分隔线
            if entry.startswith("═") or entry.startswith("─") or entry.startswith("节点总数"):
                continue
            if "记忆树索引" in entry or "检索提示" in entry or "管理命令" in entry:
                continue
            # 去重：内容前200字符匹配 或 话题完全匹配
            fingerprint = entry.strip()[:200]
            if fingerprint in existing_contents:
                continue
            # 也检查话题名（MemoryTree 的 topic 通常较短）
            entry_first_line = entry.split("\n")[0].strip()
            if entry_first_line in existing_topics:
                continue

            node_id = self.add_memory(entry)
            if node_id:
                existing_contents.add(fingerprint)
                imported += 1

        if imported:
            logger.info("修剪同步: 从 MEMORY.md 导入 %d 条新记忆", imported)
        return imported

    def migrate_from_flat(self, memory_md_path: Optional[Path] = None) -> int:
        """从旧的扁平 MEMORY.md 文件迁移记忆到树形结构。

        迁移流程:
          1. 读取 MEMORY.md，按 § 分隔符拆分为条目
          2. 为每个条目创建叶子节点
          3. 自动分类到话题树中
          4. 备份原文件为 MEMORY.md.bak
          5. 自动生成新的轻量 INDEX

        Args:
            memory_md_path: MEMORY.md 路径，默认 ~/.hermes/memories/MEMORY.md

        Returns:
            迁移的条目数量，0 表示无需迁移
        """
        mem_dir = _get_memories_dir()
        md_path = memory_md_path or (mem_dir / "MEMORY.md")

        if not md_path.exists():
            logger.info("MEMORY.md 不存在，无需迁移")
            return 0

        # 如果已有树形数据，检查是否已经迁移过
        if self.root_ids and self.nodes:
            logger.info("记忆树已存在 (%d 个节点)，跳过迁移", len(self.nodes))
            return 0

        # 备份原文件
        bak_path = md_path.with_suffix(".md.bak")
        try:
            bak_path.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
            logger.info("已备份原 MEMORY.md 到 %s", bak_path)
        except OSError as e:
            logger.warning("备份失败: %s", e)

        # 读取并解析条目
        try:
            raw = md_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.error("读取 MEMORY.md 失败: %s", e)
            return 0

        if not raw.strip():
            return 0

        # 使用 § 分隔符拆分条目（与 MemoryStore 一致）
        entries = [e.strip() for e in raw.split("\n§\n")]
        entries = [e for e in entries if e]

        if not entries:
            return 0

        logger.info("开始迁移 %d 条扁平记忆...", len(entries))

        migrated = 0
        for entry in entries:
            if not entry.strip():
                continue
            node_id = self.add_memory(entry)
            if node_id:
                migrated += 1

        self.save()
        logger.info("迁移完成: %d/%d 条记忆已导入树形结构", migrated, len(entries))
        return migrated

    # ------------------------------------------------------------------
    # 索引生成 — 替换 MEMORY.md 为轻量 INDEX
    # ------------------------------------------------------------------

    def _render_tree_line(
        self,
        node_id: str,
        prefix: str = "",
        is_last: bool = True,
        depth: int = 0,
    ) -> List[str]:
        """递归渲染树形结构的单行文本。

        Args:
            node_id: 当前节点 ID
            prefix: 行前缀（用于缩进和对齐）
            is_last: 是否为同级最后一个节点
            depth: 当前深度

        Returns:
            渲染行列表
        """
        node = self.nodes.get(node_id)
        if not node:
            return []

        lines = []

        # 选择连接符
        connector = "└── " if is_last else "├── "
        if depth == 0:
            connector = ""  # 根节点不需要连接符

        # 分数区间标签
        cat = node.score_category()
        core_tag = " ⭐核心" if node.is_core else ""
        child_count = f" [子:{len(node.children_ids)}]" if node.children_ids else ""

        line = f"{prefix}{connector}{node.topic} (分数:{node.score}, {cat}{core_tag}{child_count})"
        lines.append(line)

        # 如果有内容且是叶子节点，缩进显示内容摘要
        if node.content and node.is_leaf:
            content_preview = node.content[:60].replace("\n", " ")
            if len(node.content) > 60:
                content_preview += "…"
            lines.append(f"{prefix}{'    ' if is_last else '│   '}    📝 {content_preview}")

        # 递归渲染子节点
        children = [cid for cid in node.children_ids if cid in self.nodes]
        for i, child_id in enumerate(children):
            child_is_last = (i == len(children) - 1)
            if depth == 0:
                child_prefix = ""
            else:
                child_prefix = prefix + ("    " if is_last else "│   ")
            lines.extend(
                self._render_tree_line(child_id, child_prefix, child_is_last, depth + 1)
            )

        return lines

    def generate_index(self) -> str:
        """生成轻量级的记忆索引文本。

        此文本将写入 MEMORY.md，在 Hermes 启动时自动注入 system prompt。
        相比旧的扁平全量注入，大幅减少 token 消耗。

        Returns:
            格式化的索引文本
        """
        if not self.root_ids:
            return "记忆树为空。使用 '记忆' 或 '记住' 指令添加记忆。"

        lines = []
        separator = "═" * 50

        lines.append(separator)
        lines.append("记忆树索引 (MemoryTree Index)")
        lines.append(separator)
        lines.append(f"节点总数: {len(self.nodes)} | "
                      f"根话题: {len(self.root_ids)} | "
                      f"核心记忆: {sum(1 for n in self.nodes.values() if n.is_core)}")
        lines.append(f"上次衰减: {self.last_decay or '未执行'}")

        # 嵌入模型健康状态
        health = self.matcher.health_status()
        if health:
            lines.append(f"⚠️  {health}")
        lines.append("")

        # 渲染每个根话题的子树
        visible_roots = [rid for rid in self.root_ids if not self.nodes.get(rid, MemoryNode()).deleted]
        for i, root_id in enumerate(visible_roots):
            is_last_root = (i == len(self.root_ids) - 1)
            root_lines = self._render_tree_line(root_id, "", is_last_root, 0)
            lines.extend(root_lines)
            if not is_last_root:
                lines.append("")  # 根话题之间空行

        lines.append("")
        lines.append("─" * 50)
        lines.append("检索提示: 对话中提及相关话题时，会自动下钻加载完整记忆。")
        lines.append("管理命令: python mindmap_memory.py [add|search|recall|decay|core|stats]")

        return "\n".join(lines)

    def write_index_to_md(self) -> bool:
        """将索引写入 MEMORY.md 文件，替换旧扁平格式。

        写入前自动执行修剪同步：扫描 MEMORY.md 中是否有原生 memory 工具
        新增的条目，如有则自动纳入 mindmap.db。

        Hermes 启动时会自动将此文件内容注入 system prompt。

        Returns:
            True 写入成功
        """
        # 修剪同步：检查原生 memory 工具是否有新增
        self.sync_from_native()
        mem_dir = _get_memories_dir()
        mem_dir.mkdir(parents=True, exist_ok=True)

        index_text = self.generate_index()
        md_path = mem_dir / "MEMORY.md"

        try:
            # 原子写入
            fd, tmp_path = tempfile.mkstemp(
                dir=str(mem_dir), suffix=".tmp", prefix=".mem_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(index_text)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, str(md_path))
                logger.info("已更新 MEMORY.md 索引 (%d 字符)", len(index_text))
                return True
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as e:
            logger.error("写入 MEMORY.md 失败: %s", e)
            return False

    # ------------------------------------------------------------------
    # 统计信息
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """返回记忆系统的统计信息。

        Returns:
            包含各项统计指标的字典
        """
        total = len(self.nodes)
        core_count = sum(1 for n in self.nodes.values() if n.is_core)
        non_core = total - core_count

        # 分数分布
        short_term = sum(1 for n in self.nodes.values() if 1 <= n.score <= SHORT_TERM_MAX)
        long_term = sum(1 for n in self.nodes.values() if SHORT_TERM_MAX < n.score <= LONG_TERM_MAX)
        permanent = sum(1 for n in self.nodes.values() if n.score > LONG_TERM_MAX)

        # 深度分布
        max_found_depth = 0
        depth_counts = {}
        for node_id in self.nodes:
            d = self._get_depth(node_id)
            max_found_depth = max(max_found_depth, d)
            depth_counts[d] = depth_counts.get(d, 0) + 1

        # 叶子节点数
        leaf_count = sum(1 for n in self.nodes.values() if n.is_leaf)

        deleted_count = sum(1 for n in self.nodes.values() if n.deleted)
        deep_count = sum(1 for n in self.nodes.values() if n.is_deep and not n.deleted)
        return {
            "节点总数": total,
            "活跃节点": total - deleted_count - deep_count,
            "已删除(可恢复)": deleted_count,
            "树根归档": deep_count,
            "根话题数": len(self.root_ids),
            "核心记忆": core_count,
            "非核心节点": non_core,
            "非核心上限": MAX_NON_CORE_NODES,
            "非核心使用率": f"{non_core / MAX_NON_CORE_NODES * 100:.1f}%",
            "短期记忆(1-20)": short_term,
            "长期记忆(21-40)": long_term,
            "永久记忆(41+)": permanent,
            "叶子节点": leaf_count,
            "最大深度": max_found_depth,
            "深度限制": MAX_DEPTH,
            "上次衰减": self.last_decay or "未执行",
            "上次修剪": self.last_consolidate or "未执行",
            "跳过重复(哈希去重)": self._duplicates_skipped,
            "内存哈希数": len(self._content_hashes),
            "混合搜索使用次数": self._hybrid_search_count,
        }

    def print_stats(self) -> None:
        """以易读格式打印统计信息。"""
        stats = self.stats()
        print("\n📊 记忆树 — 统计信息")
        print("═" * 40)
        for key, value in stats.items():
            print(f"  {key}: {value}")
        print()

    def setup_embeddings(self) -> None:
        """自动下载并配置 BGE 嵌入模型。"""
        import subprocess
        import shutil

        MODEL_NAME = "BAAI/bge-small-zh-v1.5"
        CACHE_DIR = os.path.expanduser(f"~/.cache/hermes/embeddings/{MODEL_NAME}")
        MODEL_FILES = [
            "config.json",
            "model.safetensors",
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "sentence_bert_config.json",
            "modules.json",
            "1_Pooling/config.json",
        ]

        print("🔧 记忆树嵌入模型安装向导")
        print("═" * 40)
        print(f"  模型: {MODEL_NAME}")
        print(f"  用途: 中文语义匹配（提高记忆检索精度）")
        print(f"  大小: ~100MB")
        print()

        # 步骤 1: 检查 Python 版本
        print("📋 步骤 1/6: 检查 Python 版本...")
        py_ver = sys.version_info
        if py_ver < (3, 8):
            print(f"   ❌ Python {py_ver.major}.{py_ver.minor} 不满足要求（需要 3.8+）")
            return
        print(f"   ✅ Python {py_ver.major}.{py_ver.minor}.{py_ver.micro} — 满足要求")
        print()

        # 步骤 2: 安装 sentence-transformers
        print("📦 步骤 2/6: 安装 sentence-transformers...")
        try:
            import sentence_transformers  # noqa: F401
            print("   ✅ sentence-transformers 已安装")
        except ImportError:
            print("   🔄 正在安装 sentence-transformers（可能需要几分钟）...")
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "--quiet", "sentence-transformers"],
                    stdout=sys.stdout, stderr=sys.stderr
                )
                print("   ✅ sentence-transformers 安装完成")
            except subprocess.CalledProcessError as e:
                print(f"   ❌ 安装失败: {e}")
                print("   请手动运行: pip install sentence-transformers")
                return
        print()

        # 步骤 3: 创建模型目录
        print("📁 步骤 3/6: 创建模型目录...")
        os.makedirs(CACHE_DIR, exist_ok=True)
        print(f"   ✅ 目录已就绪: {CACHE_DIR}")
        print()

        # 步骤 4: 下载模型文件
        print("🔄 步骤 4/6: 下载模型文件...")
        all_exist = True
        for fname in MODEL_FILES:
            fpath = os.path.join(CACHE_DIR, fname)
            if os.path.exists(fpath):
                continue
            all_exist = False
            break

        if all_exist:
            print("   ✅ 模型文件已存在，跳过下载")
        else:
            # 尝试使用 huggingface_hub
            use_hub = False
            try:
                from huggingface_hub import snapshot_download  # noqa: F401
                use_hub = True
            except ImportError:
                print("   📦 安装 huggingface-hub...")
                try:
                    subprocess.check_call(
                        [sys.executable, "-m", "pip", "install", "--quiet", "huggingface-hub"],
                        stdout=sys.stdout, stderr=sys.stderr
                    )
                    use_hub = True
                except subprocess.CalledProcessError:
                    use_hub = False

            if use_hub:
                print("   🔄 使用 huggingface-hub 下载模型...")
                try:
                    from huggingface_hub import snapshot_download
                    snapshot_download(
                        repo_id=MODEL_NAME,
                        local_dir=CACHE_DIR,
                        local_dir_use_symlinks=False,
                    )
                    print("   ✅ 模型下载完成")
                except Exception as e:
                    print(f"   ⚠️  huggingface-hub 下载失败: {e}")
                    use_hub = False

            if not use_hub:
                # 优雅降级: 使用 curl
                print("   🔄 使用 curl 下载模型文件（优雅降级）...")
                base_url = f"https://huggingface.co/{MODEL_NAME}/resolve/main"
                for fname in MODEL_FILES:
                    fpath = os.path.join(CACHE_DIR, fname)
                    if os.path.exists(fpath):
                        print(f"   ✅ {fname} 已存在")
                        continue
                    os.makedirs(os.path.dirname(fpath), exist_ok=True)
                    url = f"{base_url}/{fname}"
                    print(f"   ⬇️  下载 {fname}...")
                    try:
                        subprocess.check_call(
                            ["curl", "-L", "--silent", "--show-error", "-o", fpath, url],
                            stdout=sys.stdout, stderr=sys.stderr
                        )
                        print(f"   ✅ {fname} 下载完成")
                    except (subprocess.CalledProcessError, FileNotFoundError) as e:
                        print(f"   ❌ 下载 {fname} 失败: {e}")
                        print(f"   请手动从 {url} 下载并放到 {fpath}")
                        return
        print()

        # 步骤 5: 环境变量提示
        print("⚙️  步骤 5/6: 配置环境变量...")
        print(f"   请在 shell 配置文件中添加以下行：")
        print(f"   export MEMORYTREE_EMBEDDING_MODEL=local:{MODEL_NAME}")
        print()
        print(f"   或临时设置（当前会话有效）：")
        print(f"   export MEMORYTREE_EMBEDDING_MODEL=local:{MODEL_NAME}")
        print()

        # 步骤 6: 验证
        print("🧪 步骤 6/6: 验证模型...")
        try:
            from sentence_transformers import SentenceTransformer

            # 如果尚未设置环境变量，临时设置以验证
            old_env = os.environ.get("MEMORYTREE_EMBEDDING_MODEL", "")
            os.environ["MEMORYTREE_EMBEDDING_MODEL"] = f"local:{MODEL_NAME}"

            model = SentenceTransformer(CACHE_DIR)
            embedding = model.encode("测试中文句子")
            print(f"   ✅ 模型验证成功！向量维度: {len(embedding)}")
            print(f"   样本输出: {embedding[:5].tolist()}...")

            if old_env:
                os.environ["MEMORYTREE_EMBEDDING_MODEL"] = old_env
            elif "MEMORYTREE_EMBEDDING_MODEL" in os.environ:
                del os.environ["MEMORYTREE_EMBEDDING_MODEL"]  # 不保留临时设置

        except Exception as e:
            print(f"   ⚠️  验证失败（可能仍需设置环境变量）: {e}")
            print(f"   请确保已设置: export MEMORYTREE_EMBEDDING_MODEL=local:{MODEL_NAME}")

        print()
        print("═" * 40)
        print("✅ 安装引导完成！")
        print()
        print("📝 下一步:")
        print(f"   1. 在 shell 配置 (~/.zshrc 或 ~/.bashrc) 中添加：")
        print(f"      export MEMORYTREE_EMBEDDING_MODEL=local:{MODEL_NAME}")
        print(f"   2. 重新加载配置: source ~/.zshrc")
        print(f"   3. 重新启动 Hermes Agent 或重新加载 mindmap-memory 技能")
        print(f"   4. 运行 'python3 mindmap_memory.py consolidate' 测试嵌入模型")
        print()


# ---------------------------------------------------------------------------
# CLI 入口 — 独立运行或作为库导入
# ---------------------------------------------------------------------------

def cli_main():
    """命令行入口。支持 add, search, recall, decay, migrate, core, stats, sync, replace, remove, recover 命令。"""
    json_mode = "--json" in sys.argv
    if json_mode:
        sys.argv.remove("--json")
        logging.getLogger().setLevel(logging.WARNING)

    if len(sys.argv) < 2:
        if json_mode:
            print(json.dumps({"command": "help", "available": ["add", "search", "recall", "decay", "migrate", "sync", "replace", "remove", "recover", "core", "stats", "consolidate", "setup-embeddings"]}))
            return
        print("用法: python mindmap_memory.py <命令> [参数]")
        print()
        print("命令:")
        print("  add <内容>           添加一条记忆")
        print("  search <查询>         检索记忆")
        print("  recall               查看完整记忆树")
        print("  decay                手动触发衰减扫描")
        print("  migrate              从 MEMORY.md 迁移旧记忆")
        print("  core <节点ID>         切换核心记忆标记")
        print("  stats                显示统计信息")
        print("  sync                 从 MEMORY.md 增量导入新记忆")
        print("  replace <搜索> <新内容>  替换已有记忆")
        print("  remove <搜索>          删除指定记忆")
        print("  recover [搜索]          恢复已删除的记忆")
        print("  consolidate            记忆园丁：用嵌入模型重分类当天记忆")
        print("  setup-embeddings       安装引导：自动下载并配置 BGE 嵌入模型")
        print()
        print("在 Hermes 对话中使用 /mindmap-memory 加载此技能")
        return

    command = sys.argv[1].lower()
    store = MindMapStore()
    store.load()

    if command == "add":
        if len(sys.argv) < 3:
            if json_mode:
                print(json.dumps({"command": "add", "success": False, "error": "需要提供记忆内容"}))
            else:
                print("错误: 需要提供记忆内容")
            return
        content = " ".join(sys.argv[2:])
        node_id = store.add_memory(content)
        if json_mode:
            print(json.dumps({"command": "add", "success": True, "node_id": node_id}))
        else:
            print(f"✅ 已添加记忆 | 节点ID: {node_id[:8]}...")
        store.write_index_to_md()

    elif command == "search":
        if len(sys.argv) < 3:
            if json_mode:
                print(json.dumps({"command": "search", "success": False, "error": "需要提供查询内容"}))
            else:
                print("错误: 需要提供查询内容")
            return
        query = " ".join(sys.argv[2:])
        results = store.search(query)
        if json_mode:
            result_list = []
            for node in results:
                result_list.append({
                    "topic": node.topic,
                    "score": node.score,
                    "score_category": node.score_category(),
                    "is_core": node.is_core,
                    "content": node.content
                })
            print(json.dumps({"command": "search", "count": len(results), "results": result_list}))
        else:
            if results:
                print(f"🔍 找到 {len(results)} 条相关记忆:\n")
                for i, node in enumerate(results, 1):
                    print(f"  [{i}] {node.topic}")
                    print(f"      分数: {node.score} ({node.score_category()})")
                    print(f"      核心: {'⭐是' if node.is_core else '否'}")
                    if node.content:
                        preview = node.content[:100].replace("\n", " ")
                        if len(node.content) > 100:
                            preview += "…"
                        print(f"      内容: {preview}")
                    print()
            else:
                print("未找到相关记忆。")

    elif command == "recall":
        if not store.root_ids:
            if json_mode:
                print(json.dumps({"command": "recall", "tree": ""}))
            else:
                print("记忆树为空。")
        else:
            tree = store.generate_index()
            if json_mode:
                print(json.dumps({"command": "recall", "tree": tree}))
            else:
                print(tree)

    elif command == "decay":
        removed = store.decay_if_needed()
        count = len(removed) if removed else 0
        if json_mode:
            print(json.dumps({"command": "decay", "removed_count": count}))
        else:
            if removed:
                print(f"🗑️  衰减扫描完成，已删除 {count} 个节点")
            else:
                print("✅ 衰减扫描完成，无需删除节点。")
        store.write_index_to_md()

    elif command == "migrate":
        count = store.migrate_from_flat()
        if json_mode:
            print(json.dumps({"command": "migrate", "count": count}))
        else:
            if count > 0:
                print(f"✅ 已迁移 {count} 条扁平记忆到树形结构")
                store.write_index_to_md()
            else:
                print("无需迁移（记忆树已存在或 MEMORY.md 为空）。")

    elif command == "core":
        if len(sys.argv) < 3:
            if json_mode:
                print(json.dumps({"command": "core", "success": False, "error": "需要提供节点 ID"}))
            else:
                print("错误: 需要提供节点 ID")
            return
        node_id_prefix = sys.argv[2]
        matched = None
        for nid in store.nodes:
            if nid.startswith(node_id_prefix):
                matched = nid
                break
        if not matched:
            if json_mode:
                print(json.dumps({"command": "core", "success": False, "error": f"未找到节点: {node_id_prefix}"}))
            else:
                print(f"未找到节点: {node_id_prefix}")
            return
        node = store.nodes[matched]
        new_state = not node.is_core
        store.set_core(matched, new_state)
        if json_mode:
            print(json.dumps({"command": "core", "success": True, "node_id": matched, "new_state": new_state}))
        else:
            print(f"✅ 节点 '{node.topic}' 核心标记已设为 {new_state}")

    elif command == "replace":
        if len(sys.argv) < 4:
            if json_mode:
                print(json.dumps({"command": "replace", "success": False, "error": "用法: python mindmap_memory.py replace <搜索文本> <新内容>"}))
            else:
                print("错误: 用法: python mindmap_memory.py replace <搜索文本> <新内容>")
            return
        search_text = sys.argv[2]
        new_content = " ".join(sys.argv[3:])
        result = store.replace_memory(search_text, new_content)
        if json_mode:
            print(json.dumps({"command": "replace", **result}))
        else:
            if result.get("success"):
                print(f"✅ {result['message']}")
                store.write_index_to_md()
            elif "candidates" in result:
                print(f"⚠️  {result['error']}")
                for c in result["candidates"]:
                    print(f"    [{c['node_id']}] {c['topic']}: {c['preview']}")
            else:
                print(f"❌ {result['error']}")

    elif command == "remove":
        if len(sys.argv) < 3:
            if json_mode:
                print(json.dumps({"command": "remove", "success": False, "error": "用法: python mindmap_memory.py remove <搜索文本>"}))
            else:
                print("错误: 用法: python mindmap_memory.py remove <搜索文本>")
            return
        search_text = " ".join(sys.argv[2:])
        force = "--force" in sys.argv
        result = store.remove_memory(search_text, force=force)
        if json_mode:
            print(json.dumps({"command": "remove", **result}))
        else:
            if result.get("success"):
                print(f"✅ {result['message']}")
                store.write_index_to_md()
            elif "candidates" in result:
                print(f"⚠️  {result['error']}")
                for c in result["candidates"]:
                    core = "⭐" if c["is_core"] else "  "
                    print(f"    {core} [{c['node_id']}] {c['topic']}: {c['preview']}")
            else:
                print(f"❌ {result['error']}")

    elif command == "recover":
        search_text = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        result = store.recover_memory(search_text)
        if json_mode:
            print(json.dumps({"command": "recover", **result}))
        else:
            if result.get("recovered", 0) > 0:
                print(f"✅ {result['message']}")
                store.write_index_to_md()
            elif "candidates" in result:
                print(f"📋 {result['message']}:")
                for c in result["candidates"]:
                    print(f"    [{c['node_id']}] {c['topic']} ({c['deleted_at']}) {c['preview']}")
            elif result.get("success"):
                print(f"✅ {result['message']}")
            else:
                print(f"❌ {result['error']}")

    elif command == "consolidate":
        count = store.consolidate_today()
        if json_mode:
            print(json.dumps({"command": "consolidate", "count": count}))
        else:
            if count > 0:
                print(f"🧠 记忆园丁完成：{count} 个节点重新分类")
                store.write_index_to_md()
            else:
                print("🧠 记忆园丁：无需重新分类（可能未配置嵌入模型或无当天新增记忆）")

    elif command == "setup-embeddings":
        store.setup_embeddings()

    elif command == "stats":
        stats_data = store.stats()
        if json_mode:
            print(json.dumps({"command": "stats", "data": stats_data}))
        else:
            store.print_stats()

    elif command == "sync":
        count = store.sync_from_native()
        if json_mode:
            print(json.dumps({"command": "sync", "count": count}))
        else:
            if count > 0:
                print(f"✅ 已从 MEMORY.md 导入 {count} 条新记忆")
                store.write_index_to_md()
            else:
                print("✅ 无需同步（无新增条目）")

    else:
        if json_mode:
            print(json.dumps({"command": "unknown", "available": ["add", "search", "recall", "decay", "migrate", "sync", "replace", "remove", "recover", "core", "stats", "consolidate", "setup-embeddings"]}))
        else:
            print(f"未知命令: {command}")
            print("可用: add, search, recall, decay, migrate, sync, replace, remove, recover, core, stats, consolidate, setup-embeddings")


if __name__ == "__main__":
    cli_main()
