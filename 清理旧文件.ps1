# 清理旧版本生成脚本和冗余文件
# 执行前会提示确认

$ErrorActionPreature = "Stop"

Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "🧹 清理旧版本文件" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""
Write-Host "即将删除以下文件：" -ForegroundColor Red
Write-Host ""
Write-Host "旧生成脚本：" -ForegroundColor Yellow
Write-Host "  • 生成Alpha_v2.py" -ForegroundColor Gray
Write-Host "  • 启动高质量生成.ps1 (旧版)" -ForegroundColor Gray
Write-Host ""
Write-Host "旧输出文件：" -ForegroundColor Yellow
Write-Host "  • 待提交Alpha列表.csv" -ForegroundColor Gray
Write-Host ""
Write-Host "旧文档：" -ForegroundColor Yellow
Write-Host "  • ALPHA_QUALITY_CORRELATION_FIX.md" -ForegroundColor Gray
Write-Host "  • Alpha系统使用指南.md" -ForegroundColor Gray
Write-Host "  • DESCRIPTION_AND_ALPHA_QUALITY_AUDIT.md" -ForegroundColor Gray
Write-Host "  • DESCRIPTION_PIPELINE_IMPLEMENTATION.md" -ForegroundColor Gray
Write-Host "  • README_质量驱动.md" -ForegroundColor Gray
Write-Host "  • 版本对比与选择指南.md" -ForegroundColor Gray
Write-Host "  • worldquant_brain_submission_skill.md" -ForegroundColor Gray
Write-Host ""
Write-Host "旧辅助脚本：" -ForegroundColor Yellow
Write-Host "  • bootstrap_research.py" -ForegroundColor Gray
Write-Host "  • bootstrap_research_full.py" -ForegroundColor Gray
Write-Host "  • brain_batch_resim.py" -ForegroundColor Gray
Write-Host "  • brain_scan_pipeline.py" -ForegroundColor Gray
Write-Host "  • 提交通过门槛的alpha.py" -ForegroundColor Gray
Write-Host "  • 更新alpha数据.py" -ForegroundColor Gray
Write-Host "  • 测试质量驱动.py" -ForegroundColor Gray
Write-Host "  • package.json" -ForegroundColor Gray
Write-Host ""
Write-Host "保留文件：" -ForegroundColor Green
Write-Host "  ✓ 生成高质量Alpha.py (新统一入口)" -ForegroundColor White
Write-Host "  ✓ 启动高质量Alpha生成.ps1 (新启动脚本)" -ForegroundColor White
Write-Host "  ✓ auto_alpha_pipeline_rebuilt_v50.py (核心引擎)" -ForegroundColor White
Write-Host "  ✓ 提交Alpha.py (提交脚本)" -ForegroundColor White
Write-Host "  ✓ 高质量Alpha候选.csv (新输出)" -ForegroundColor White
Write-Host "  ✓ alpha_generated_expressions.csv (历史)" -ForegroundColor White
Write-Host "  ✓ alpha_submission_feedback.csv (平台反馈)" -ForegroundColor White
Write-Host ""

$response = Read-Host "确认删除? (yes/no)"
if ($response -ne "yes") {
    Write-Host "❌ 已取消清理" -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host "🗑️  开始清理..." -ForegroundColor Cyan

# 删除已在git中标记删除的文件
git add -u

# 删除未跟踪的旧文件
$filesToDelete = @(
    "启动高质量生成.ps1"
)

foreach ($file in $filesToDelete) {
    if (Test-Path $file) {
        Remove-Item $file -Force
        Write-Host "  ✓ 已删除: $file" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "✅ 清理完成" -ForegroundColor Green
Write-Host ""
Write-Host "建议接下来：" -ForegroundColor Yellow
Write-Host "  1. 运行 git status 确认删除" -ForegroundColor White
Write-Host "  2. 运行 git commit -m 'refactor: 精简生成系统，统一高质量入口'" -ForegroundColor White
Write-Host "  3. 运行 .\启动高质量Alpha生成.ps1 测试新系统" -ForegroundColor White
Write-Host ""
