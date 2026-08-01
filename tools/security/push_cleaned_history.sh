#!/bin/bash
# P0-01 Git 历史清理 — 最终推送脚本
# 执行此脚本以完成历史清理流程

set -e

echo "=========================================="
echo "P0-01 Git 历史清理 — 最终推送"
echo "=========================================="
echo ""

# 步骤 1: 验证工作目录
if [ ! -d ".git" ]; then
    echo "错误: 当前目录不是 Git 仓库"
    echo "请先 cd 到 alpha 目录"
    exit 1
fi

# 步骤 2: 验证远程配置
echo "[1/5] 验证远程配置..."
git remote -v
echo ""

if ! git remote -v | grep -q "origin.*github.com/Metroids048/alpha"; then
    echo "错误: origin 远程未正确配置"
    echo "请运行: git remote add origin https://github.com/Metroids048/alpha.git"
    exit 1
fi

# 步骤 3: 验证历史已清理
echo "[2/5] 验证历史已清理..."
if ! python tools/security/verify_git_history.py; then
    echo ""
    echo "错误: 仍然检测到敏感路径"
    echo "历史清理可能未成功完成"
    exit 1
fi
echo ""

# 步骤 4: 显示即将推送的提交
echo "[3/5] 即将推送的提交:"
git log --oneline --graph --all | head -10
echo ""

# 步骤 5: 最终确认
echo "[4/5] ⚠️  最终确认"
echo ""
echo "此操作将："
echo "  - 重写远程仓库的完整 Git 历史（不可逆）"
echo "  - 删除所有敏感路径（4555 条路径已从本地移除）"
echo "  - 要求其他协作者重新 clone 仓库"
echo ""
echo "备份位于: ../alpha-backup-before-filter.git"
echo ""
read -p "确认执行 git push --force? (输入 YES 继续): " confirmation

if [ "$confirmation" != "YES" ]; then
    echo "操作已取消"
    exit 0
fi

# 步骤 6: 执行推送
echo ""
echo "[5/5] 执行推送..."
echo ""

git push --force --all origin
echo ""
git push --force --tags origin
echo ""

echo "=========================================="
echo "✅ 历史清理完成！"
echo "=========================================="
echo ""
echo "后续操作："
echo "1. 通知协作者执行:"
echo "   rm -rf alpha && git clone https://github.com/Metroids048/alpha.git"
echo ""
echo "2. 验证远程仓库:"
echo "   git clone --depth=1 https://github.com/Metroids048/alpha.git /tmp/alpha-verify"
echo "   cd /tmp/alpha-verify && python tools/security/verify_git_history.py"
echo ""
echo "3. 备份可安全删除（建议保留30天）:"
echo "   rm -rf ../alpha-backup-before-filter.git"
echo ""
