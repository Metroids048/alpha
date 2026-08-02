# Alpha持续生成启动脚本（无限模式）
# 自动批次管理：每批次600个候选，自动归档并开始新批次

$ErrorActionPreference = "Stop"

Write-Host "=== Alpha持续生成模式（无限批次）===" -ForegroundColor Cyan
Write-Host ""
Write-Host "工作模式:" -ForegroundColor Yellow
Write-Host "  • 每批次产出600个候选（离线配置上限）" -ForegroundColor White
Write-Host "  • 连续5轮0新增后自动归档当前批次" -ForegroundColor White
Write-Host "  • 重置去重池，开始新批次生成" -ForegroundColor White
Write-Host "  • 循环往复，无需人工干预" -ForegroundColor White
Write-Host ""
Write-Host "配置参数:" -ForegroundColor Yellow
Write-Host "  • 探索型预设: diverse_exploration" -ForegroundColor White
Write-Host "  • 近通过变异: 20%" -ForegroundColor White
Write-Host "  • 同结构上限: 40（释放参数变体）" -ForegroundColor White
Write-Host "  • 批次大小: 300个候选/轮" -ForegroundColor White
Write-Host "  • 间隔: 30秒/轮" -ForegroundColor White
Write-Host ""

# 检查环境
if (-not (Test-Path ".env")) {
    Write-Host "❌ 未找到 .env 文件，请确保包含 WQ_USERNAME 和 WQ_PASSWORD" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".alpha_datafields_cache.json")) {
    Write-Host "⚠️  警告: 未找到字段缓存，首次运行可能需要登录" -ForegroundColor Yellow
    Write-Host "   建议先运行一次 提交Alpha.py 来初始化缓存" -ForegroundColor Yellow
    $response = Read-Host "是否继续? (y/n)"
    if ($response -ne "y") {
        exit 0
    }
}

Write-Host "归档文件将保存为: archive_batch_<时间戳>.csv" -ForegroundColor Gray
Write-Host "主输出文件: 高质量Alpha候选.csv（当前批次）" -ForegroundColor Gray
Write-Host ""
Write-Host "按 Ctrl+C 可随时停止" -ForegroundColor Yellow
Write-Host ""
Start-Sleep -Seconds 3

# 启动持续生成（无限循环）
python 生成Alpha.py `
    --preset diverse_exploration `
    --near-pass-share 0.20 `
    --max-same-shape 40 `
    --batch-size 300 `
    --max-payloads 600 `
    --interval 30 `
    --output "高质量Alpha候选.csv"
