# 重启 pipeline 脚本
Write-Host "=== Stopping old pipeline processes ==="
Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*run_pipeline*"
} | ForEach-Object {
    Write-Host "Stopping PID $($_.Id)..."
    Stop-Process -Id $_.Id -Force
}

Write-Host "`nWaiting 3 seconds..."
Start-Sleep -Seconds 3

# 确认已停止
$remaining = Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*run_pipeline*"
}
if ($remaining) {
    Write-Host "WARNING: Some processes still running, force killing..."
    $remaining | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

Write-Host "`n=== Starting fresh pipeline ==="
# 加载 .env
Get-Content .env | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') {
        [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}

# 启动新 pipeline
& "C:\Users\Windows11\.ai-workspace\venv\Scripts\python.exe" run_pipeline_loop.py
