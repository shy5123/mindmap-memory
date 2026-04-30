#!/usr/bin/env bash
# =============================================================================
# 记忆树 MemoryTree — 一键安装脚本
# 用法: bash install.sh
# =============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SKILL_DIR="$HERMES_HOME/skills/custom/mindmap-memory"
TOOLS_DIR="$HERMES_HOME/hermes-agent/tools"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo -e "${CYAN}🌲 记忆树 MemoryTree v1.2.1${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# 检测是否已在安装位置（开发模式）
if [ "$SCRIPT_DIR" = "$SKILL_DIR" ]; then
    echo -e "${YELLOW}⚠️  已在安装位置，跳过文件复制（开发模式）${NC}"
    echo -e "  ${GREEN}✓${NC} 文件已就位"
else
    # 1. 安装 Skill 文件
    echo -e "${YELLOW}[1/3]${NC} 安装 Skill 到 $SKILL_DIR"
    mkdir -p "$SKILL_DIR/scripts" "$SKILL_DIR/tools"

    cp "$SCRIPT_DIR/mindmap_memory.py"     "$SKILL_DIR/"
    cp "$SCRIPT_DIR/SKILL.md"              "$SKILL_DIR/"
    cp "$SCRIPT_DIR/README.md"             "$SKILL_DIR/"
    cp "$SCRIPT_DIR/.gitignore"            "$SKILL_DIR/"
    cp "$SCRIPT_DIR/install.sh"            "$SKILL_DIR/"
    cp "$SCRIPT_DIR/scripts/"*.py          "$SKILL_DIR/scripts/"
    cp "$SCRIPT_DIR/tools/"*.py            "$SKILL_DIR/tools/"
    chmod +x "$SKILL_DIR/mindmap_memory.py"
    echo -e "  ${GREEN}✓${NC} mindmap_memory.py + SKILL.md + README.md + scripts/ + tools/"

    # 2. 安装原生工具到 Hermes
    echo -e "${YELLOW}[2/3]${NC} 安装原生工具到 $TOOLS_DIR"
    mkdir -p "$TOOLS_DIR"
    cp "$SCRIPT_DIR/tools/memory_tree_tool.py" "$TOOLS_DIR/"
    echo -e "  ${GREEN}✓${NC} memory_tree_tool.py → $TOOLS_DIR/"
fi

# 3. 验证
echo -e "${YELLOW}[3/3]${NC} 验证安装"
python3 "$SKILL_DIR/mindmap_memory.py" stats 2>&1 | grep "节点总数" || true
echo -e "  ${GREEN}✓${NC} 记忆树引擎正常"

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  安装完成！${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  接下来："
echo "  1. 重启 Hermes（让原生工具生效）"
echo "  2. 在对话中输入 /mindmap-memory 加载技能"
echo ""
echo "  CLI 用法:"
echo "    python3 $SKILL_DIR/mindmap_memory.py add \"记住的内容\""
echo "    python3 $SKILL_DIR/mindmap_memory.py search \"关键词\""
echo ""
