# Intel Mac (x86_64) 嵌入模型安装指南

## 问题

在 Intel Mac (x86_64) 上安装 BGE 嵌入模型时遇到连环依赖问题：

1. `torch ≥ 2.6` 不再提供 macOS x86_64 wheel，最高只能装 `torch 2.2.2`
2. `torch 2.2.2` 因安全漏洞（CVE-2025-32434）被 `transformers` 拒绝加载模型
3. `torch 2.2.2` 与 `numpy ≥ 2` 不兼容（`_ARRAY_API not found`）
4. `sentence-transformers 3.x` 与旧版模型缓存配置不兼容
5. 模型缓存目录格式与 HuggingFace 预期不符

## 解决方案链

### 1. 使用 Hermes 自带的 Python 3.11 venv

```bash
~/.hermes/hermes-agent/venv/bin/python3 -m pip install "numpy<2" "sentence-transformers<3"
```

系统自带的 Python 3.9 最高 torch 2.2.2。Hermes venv 用的是 Python 3.11，也是最高 torch 2.2.2，但至少比 3.9 兼容性好一些。

### 2. 降级依赖

```bash
# 降级 numpy（torch 2.2.2 在 numpy 2.x 下报 _ARRAY_API not found）
~/.hermes/hermes-agent/venv/bin/python3 -m pip install "numpy<2"

# 降级 sentence-transformers（3.x 与旧模型缓存配置不兼容）
~/.hermes/hermes-agent/venv/bin/python3 -m pip install "sentence-transformers<3"
```

### 3. 修复模型缓存配置

模型缓存目录：`~/.cache/hermes/embeddings/BAAI/bge-small-zh-v1.5/`

`1_Pooling/config.json` 缺少 `word_embedding_dimension` 字段（旧版是 `embedding_dimension`，新版要求改名）：

```python
import json
path = '~/.cache/hermes/embeddings/BAAI/bge-small-zh-v1.5/1_Pooling/config.json'
with open(path) as f:
    cfg = json.load(f)
cfg.pop('embedding_dimension', None)   # 删除旧字段
cfg['word_embedding_dimension'] = 512  # BGE small 的维度
with open(path, 'w') as f:
    json.dump(cfg, f, indent=2)
```

### 4. 设置环境变量指向本地路径

修改了 `_get_matcher()` 函数（`mindmap_memory.py`），支持检测 `local:` 后面的值是目录路径还是模型 ID：
- 如果是目录路径 → 直接 `SentenceTransformer(path)` 加载
- 如果不是 → 从 HuggingFace Hub 加载

```bash
export MEMORYTREE_EMBEDDING_MODEL=local:~/.cache/hermes/embeddings/BAAI/bge-small-zh-v1.5
export TRANSFORMERS_CACHE=$HOME/.cache/hermes/embeddings
```

### 5. 验证

```bash
~/.hermes/hermes-agent/venv/bin/python3 -c "
from mindmap_memory import MindMapStore
mt = MindMapStore()
mt.load()
print(type(mt.matcher).__name__)  # 应该输出 LocalEmbeddingModel
"
```

## 已知局限性

- 首次加载约 1-3 秒（模型加载到内存），之后编码速度 100-130 it/s (CPU)
- torch 2.2.2 有已知安全漏洞警告，但在本地环境无实际风险（不联网加载外部模型）
- Intel Mac 无法升级到 torch ≥ 2.6，这是硬件限制

## 完整命令序列

```bash
# 1. 降级依赖
~/.hermes/hermes-agent/venv/bin/python3 -m pip install "numpy<2" "sentence-transformers<3"

# 2. 修复模型缓存配置
python3 -c "
import json
path = '$HOME/.cache/hermes/embeddings/BAAI/bge-small-zh-v1.5/1_Pooling/config.json'
cfg = json.load(open(path))
cfg.pop('embedding_dimension', None)
cfg['word_embedding_dimension'] = 512
json.dump(cfg, open(path, 'w'), indent=2)
"

# 3. 设置环境变量
export MEMORYTREE_EMBEDDING_MODEL=local:~/.cache/hermes/embeddings/BAAI/bge-small-zh-v1.5

# 4. 验证
~/.hermes/hermes-agent/venv/bin/python3 -c "
from mindmap_memory import MindMapStore; m = MindMapStore(); m.load()
print('Matcher:', type(m.matcher).__name__)
# 测试语义检索
r = m.search('编程')
print(f'search 编程: {len(r)} 条')
# 触发记忆守护
c = m.consolidate_today()
print(f'consolidate: {c} 节点')
"
```
