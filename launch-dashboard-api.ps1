param(
    [int]$Port = 8001,
    [string]$HostAddress = "127.0.0.1",
    [string]$Dsn = "host=localhost dbname=apautomation user=postgres password=llamas",
    [string]$PythonPath = "C:\Users\williamsje\AppData\Local\Programs\Python\Python314\python.exe"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$apiPath = Join-Path $repoRoot "app\api\main.py"

if (-not (Test-Path $apiPath)) {
    throw "Could not find dashboard API at '$apiPath'."
}

if (-not (Test-Path $PythonPath)) {
    throw "Could not find Python runtime at '$PythonPath'. Pass -PythonPath explicitly."
}

$env:PYTHONPATH = Join-Path $repoRoot "src"
$env:AP_DASHBOARD_DSN = $Dsn
$env:RUNTIME = "LOCAL"
$env:DRY_RUN = "true"

Write-Host "Launching AP dashboard API from $apiPath"
Write-Host "Runtime: LOCAL, DRY_RUN=true"
Write-Host "URL: http://$HostAddress`:$Port"

Push-Location $repoRoot
try {
    & $PythonPath -m uvicorn app.api.main:app --host $HostAddress --port $Port
}
finally {
    Pop-Location
}
