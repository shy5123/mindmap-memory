#!/usr/bin/env python3
"""
更新 GitHub Release v1.5.0 描述。
用法： GITHUB_TOKEN=ghp_xxx python3 scripts/update_release_v150.py
"""
import json, os, urllib.request, sys

TOKEN = os.environ.get("GITHUB_TOKEN")
OWNER = "shy5123"
REPO = "mindmap-memory"
TAG = "v1.5.0"

BODY = """v1.5.0 — 嵌入模型修复 + 混合搜索 + 内容哈希去重

🆕 新特性
- 混合搜索：BM25 稀疏检索 + 关键词/余弦相似度，通过 RRF 融合排序，纯 Python 实现零外部依赖
- 内容哈希去重：每次 add_memory 前计算 SHA-256 哈希，相同内容自动跳过不重复添加
- BM25 参数 k1=1.5, b=0.75 默认值

🐛 修复（重点）
- 嵌入模型本地路径加载修复：_get_matcher 支持 local:/path/to/model 和 local:BAAI/bge-small-zh-v1.5 两种格式，绕过 torch 2.2.2 安全检查限制
- Intel Mac (x86_64) 兼容：torch 2.2.2 + sentence-transformers 2.7.0 + numpy 1.26.4 确认可用
- 模型配置修复：为 BGE 池化配置补齐 word_embedding_dimension=512
- 记忆守护空转修复：无嵌入模型时 consolidate_today 也更新 last_consolidate 时间戳
- 核心记忆恢复与重复节点清理
- 核心保护测试：pre_release_tests 新增验证用例

⚙️ 技术细节
- 核心文件：mindmap_memory.py (3022 行)
- 语义相似度实测：编程语言↔Python 从 ~0.0 升至 0.801
- 记忆守护首次真正执行：4/29 个叶子节点重新分类
- 零新增外部依赖
"""

def main():
    if not TOKEN:
        print("错误：请设置 GITHUB_TOKEN 环境变量")
        print("创建地址：https://github.com/settings/tokens (勾选 repo 权限)")
        sys.exit(1)

    # 获取 release_id
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/tags/{TAG}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    
    with urllib.request.urlopen(req) as resp:
        release = json.loads(resp.read())
    
    release_id = release["id"]
    print(f"Release ID: {release_id}")

    # 更新 body
    update_url = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/{release_id}"
    data = json.dumps({"body": BODY.strip()}).encode()
    
    req2 = urllib.request.Request(update_url, data=data, method="PATCH")
    req2.add_header("Authorization", f"Bearer {TOKEN}")
    req2.add_header("Accept", "application/vnd.github.v3+json")
    req2.add_header("Content-Type", "application/json")
    
    with urllib.request.urlopen(req2) as resp:
        result = json.loads(resp.read())
    
    print(f"✅ Release v1.5.0 更新成功！")
    print(f"  查看：https://github.com/{OWNER}/{REPO}/releases/tag/{TAG}")

if __name__ == "__main__":
    main()
