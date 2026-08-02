# 高质量Alpha生成启动脚本
# 核心理念：优中选优，质量第一，通过平台门槛为目标

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "🎯 高质量Alpha生成系统" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "核心目标:" -ForegroundColor Yellow
Write-Host "  • 夏普值 ≥ 1.57（平台门槛）" -ForegroundColor White
Write-Host "  • Fitness ≥ 1.0" -ForegroundColor White
Write-Host "  • 换手率 1% - 70%" -ForegroundColor White
Write-Host "  • 低自相关性" -ForegroundColor White
Write-Host ""
Write-Host "策略特点:" -ForegroundColor Yellow
Write-Host "  • 质量优先：每轮150个候选（精简）" -ForegroundColor White
Write-Host "  • 时间换质量：60秒/轮（深度打磨）" -ForegroundColor White
Write-Host "  • 结构多样：同结构上限8次" -ForegroundColor White
Write-Host "  • 低竞争字段：25%使用低热度字段" -ForegroundColor White
Write-Host "  • 探索导向：近通过变异仅15%占比" -ForegroundColor White
Write-Host ""
Write-Host "输出文件:" -ForegroundColor Yellow
Write-Host "  • 高质量Alpha候选.csv（主输出）" -ForegroundColor Green
Write-Host "  • alpha_generated_expressions.csv（历史）" -ForegroundColor Gray
Write-Host ""

# 检查环境
if (-not (Test-Path ".env")) {
    Write-Host "❌ 未找到 .env 文件" -ForegroundColor Red
    Write-Host "   请创建 .env 文件并配置：" -ForegroundColor Yellow
    Write-Host "   WQ_USERNAME=你的用户名" -ForegroundColor Gray
    Write-Host "   WQ_PASSWORD=你的密码" -ForegroundColor Gray
    exit 1
}

if (-not (Test-Path ".alpha_datafields_cache.json")) {
    Write-Host "⚠️  未找到字段缓存文件" -ForegroundColor Yellow
    Write-Host "   首次运行需要登录以初始化缓存" -ForegroundColor Yellow
    Write-Host "   建议先运行一次 提交Alpha.py" -ForegroundColor Yellow
    $response = Read-Host "是否继续? (y/n)"
    if ($response -ne "y") {
        exit 0
    }
}

Write-Host "提示: 按 Ctrl+C 可随时停止" -ForegroundColor Gray
Write-Host ""
Start-Sleep -Seconds 2

# 启动生成
Write-Host "🚀 启动高质量Alpha生成..." -ForegroundColor Cyan
Write-Host ""

python 生成高质量Alpha.py

Write-Host ""
Write-Host "✅ 生成任务结束" -ForegroundColor Green
