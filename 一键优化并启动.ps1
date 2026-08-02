# Alpha项目一键清理、优化并启动高质量生成

$ErrorActionPreference = "Stop"

Write-Host "╔════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║   Alpha项目 - 一键清理优化并启动高质量生成   ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# 步骤1: 清理
Write-Host "[步骤 1/2] 执行清理..." -ForegroundColor Yellow
if (Test-Path ".\清理优化.ps1") {
    & ".\清理优化.ps1"
    Write-Host ""
} else {
    Write-Host "❌ 未找到清理脚本" -ForegroundColor Red
    exit 1
}

# 步骤2: 启动高质量生成
Write-Host "[步骤 2/2] 启动高质量生成..." -ForegroundColor Yellow
Write-Host ""
if (Test-Path ".\启动高质量生成.ps1") {
    & ".\启动高质量生成.ps1"
} else {
    Write-Host "❌ 未找到启动脚本" -ForegroundColor Red
    exit 1
}
