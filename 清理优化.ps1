# Alpha项目全面清理和优化脚本
# 执行前请确认无重要数据丢失

$ErrorActionPreference = "Continue"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

Write-Host "=== Alpha项目清理和优化 ===" -ForegroundColor Cyan
Write-Host ""

# 1. 创建归档目录
Write-Host "[1/6] 创建归档目录..." -ForegroundColor Yellow
$archiveDir = "archive_$timestamp"
New-Item -ItemType Directory -Path $archiveDir -Force | Out-Null
Write-Host "✓ 已创建: $archiveDir" -ForegroundColor Green

# 2. 归档大型历史数据
Write-Host "`n[2/6] 归档大型历史数据..." -ForegroundColor Yellow
$toArchive = @(
    "research_memory.sqlite.backup-*",
    "alpha_batch_diagnostics.csv",
    "numerai_alpha_inventory.csv",
    "hopeful_alphas_v34.jsonl",
    "alpha-final-review.zip",
    "alpha_generated_expressions.csv"  # 151MB，已饱和，归档后重新开始
)
foreach ($pattern in $toArchive) {
    $files = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue
    if ($files) {
        Move-Item -Path $pattern -Destination $archiveDir -Force -ErrorAction SilentlyContinue
        Write-Host "  ✓ 已归档: $pattern" -ForegroundColor Green
    }
}

# 3. 删除旧版本和测试脚本
Write-Host "`n[3/6] 删除旧版本和测试脚本..." -ForegroundColor Yellow
$toDelete = @(
    # 旧版本脚本
    "生成Alpha_v2.py",
    "生成Alpha民意.py",
    "启动Alpha.py",
    "提交通过门槛的alpha.py",
    "修交商讨门槛的alpha.py",
    "测试质量驱动.py",
    "更新alpha数据.py",
    "bootstrap_research*.py",
    "brain_batch_resim.py",
    "brain_scan_pipeline.py",

    # Legacy数据文件
    "legacy_*.csv",
    "new_alpha_*.csv",
    "description_*.csv",
    "platform_*.csv",
    "platform_*.json",
    "submission_*.csv",
    "alpha_pipeline_*.csv",
    "knowledge_source_inventory.csv",
    "old_alpha_pilot.csv",

    # 测试文件
    "1.csv",
    "pass.csv",
    "Alpha Models.csv",
    "package*.json",
    "alpha_candidates.jsonl",

    # 旧日志
    "*.log",
    "pipeline_loop_state.json",

    # 旧文档
    "版本对比与选择指南.md",
    "Alpha系统使用指南.md",
    "README_质量驱动.md",
    "worldquant_brain_submission_skill.md",
    "ALPHA_QUALITY_CORRELATION_FIX.md",
    "CONSULTANT_FACTORY_FINAL_ACCEPTANCE.md",
    "CURRENT_MAIN_ACCEPTANCE_AUDIT.md",
    "DESCRIPTION_AND_ALPHA_QUALITY_AUDIT.md",
    "DESCRIPTION_PIPELINE_IMPLEMENTATION.md",
    "MINIMAL_BUSINESS_PILOT_REPORT.md",
    "PLATFORM_ACCESS_RECOVERY_REPORT.md"
)

$deletedCount = 0
foreach ($pattern in $toDelete) {
    $files = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue
    if ($files) {
        Remove-Item -Path $pattern -Force -Recurse -ErrorAction SilentlyContinue
        $deletedCount += $files.Count
        Write-Host "  ✓ 已删除: $pattern" -ForegroundColor Green
    }
}
Write-Host "  总计删除 $deletedCount 个文件" -ForegroundColor Green

# 4. 清理待提交列表（过多历史导致去重失败）
Write-Host "`n[4/6] 重置待提交Alpha列表（旧数据已归档）..." -ForegroundColor Yellow
if (Test-Path "待提交Alpha列表.csv") {
    Move-Item "待提交Alpha列表.csv" $archiveDir -Force
    Write-Host "  ✓ 旧列表已归档，将从空白开始生成高质量候选" -ForegroundColor Green
}

# 5. 统计清理结果
Write-Host "`n[5/6] 清理统计..." -ForegroundColor Yellow
$archivedSize = (Get-ChildItem $archiveDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host "  已归档: $([math]::Round($archivedSize, 2)) MB" -ForegroundColor Cyan

# 6. 显示保留的核心文件
Write-Host "`n[6/6] 保留的核心文件:" -ForegroundColor Yellow
$coreFiles = @(
    "生成Alpha.py",
    "提交Alpha.py",
    "auto_alpha_pipeline_rebuilt_v50.py",
    "已提交Alpha历史.csv",
    "alpha_submission_feedback.csv",
    "hopeful_alphas.jsonl",
    ".alpha_datafields_cache.json",
    ".alpha_datasets_cache.json",
    "alpha_novelty_index.json",
    "research_memory.sqlite",
    "research_memory_quality.sqlite",
    "AGENTS.md",
    "CLAUDE.md"
)
foreach ($file in $coreFiles) {
    if (Test-Path $file) {
        $size = (Get-Item $file).Length / 1MB
        Write-Host "  ✓ $file" -NoNewline -ForegroundColor Green
        Write-Host " ($([math]::Round($size, 2)) MB)" -ForegroundColor Gray
    }
}

Write-Host "`n=== 清理完成 ===" -ForegroundColor Cyan
Write-Host "归档目录: $archiveDir" -ForegroundColor Yellow
Write-Host ""
