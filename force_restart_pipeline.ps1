# 强制停止所有 Python 进程（包括 pipeline）
Write-Host "=== Stopping all Python processes ==="
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

# 确认已停止
$remaining = Get-Process python -ErrorAction SilentlyContinue
if ($remaining) {
    Write-Host "WARNING: Some Python processes still running, retrying..."
    $remaining | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

Write-Host "`n✓ All Python processes stopped"
Write-Host "`nWaiting 5 seconds before restart..."
Start-Sleep -Seconds 5

Write-Host "`n=== Starting fresh pipeline with new database ==="
# 加载环境变量
Get-Content .env | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') {
        [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}

# 启动 pipeline
& "C:\Users\Windows11\.ai-workspace\venv\Scripts\python.exe" run_pipeline_loop.py
