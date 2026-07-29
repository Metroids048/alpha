# Loads WQ_USERNAME / WQ_PASSWORD from .env into the process env only,
# runs the existing safe diagnostic (auth-only), then clears them.
# Never echoes secret values.
$ErrorActionPreference = "Stop"
$root = "C:\Users\Windows11\Desktop\alpha"
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) { Write-Output "NO_ENV_FILE"; exit 1 }

foreach ($line in Get-Content $envFile -Encoding UTF8) {
    $t = $line.Trim()
    if ($t -eq "" -or $t.StartsWith("#")) { continue }
    $i = $t.IndexOf("=")
    if ($i -lt 1) { continue }
    $k = $t.Substring(0, $i).Trim()
    $v = $t.Substring($i + 1).Trim().Trim('"').Trim("'")
    if ($k -eq "WQ_USERNAME" -or $k -eq "WQ_PASSWORD") {
        [System.Environment]::SetEnvironmentVariable($k, $v, "Process")
    }
}

Write-Output ("user_loaded=" + [bool]$env:WQ_USERNAME + " pass_loaded=" + [bool]$env:WQ_PASSWORD)

$py = Join-Path $root ".venv\Scripts\python.exe"
try {
    & $py (Join-Path $root "diagnose_wq_auth.py") --mode no-proxy
} finally {
    Remove-Item Env:WQ_USERNAME -ErrorAction SilentlyContinue
    Remove-Item Env:WQ_PASSWORD -ErrorAction SilentlyContinue
}
