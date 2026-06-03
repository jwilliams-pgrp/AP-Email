param(
    [int]$Port = 8001,
    [string]$HostAddress = "127.0.0.1",
    [string]$Dsn = "",
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$envFile = Join-Path $repoRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*#' -or $_ -match '^\s*$') { return }
        $parts = $_ -split '=', 2
        if ($parts.Count -ne 2) { return }
        $key = $parts[0].Trim()
        $value = $parts[1].Trim()
        if (-not [string]::IsNullOrWhiteSpace($key) -and -not (Test-Path "env:$key")) {
            Set-Item -Path "env:$key" -Value $value
        }
    }
}
$apiPath = Join-Path $repoRoot "app\api\main.py"
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $apiPath)) {
    throw "Could not find dashboard API at '$apiPath'."
}

if ($PythonPath) {
    if (-not (Test-Path $PythonPath)) {
        throw "Could not find Python runtime at '$PythonPath'."
    }
    $pythonExe = $PythonPath
    $pythonPrefixArgs = @()
}
elseif (Test-Path $venvPython) {
    $pythonExe = $venvPython
    $pythonPrefixArgs = @()
}
elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonExe = "py"
    $pythonPrefixArgs = @("-3")
}
elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonExe = "python"
    $pythonPrefixArgs = @()
}
else {
    throw "No Python interpreter found. Create .venv, install Python 3.11+, or pass -PythonPath."
}

$env:PYTHONPATH = Join-Path $repoRoot "src"
$env:AP_DASHBOARD_DSN = if ($Dsn) { $Dsn } elseif ($env:AP_DASHBOARD_DSN) { $env:AP_DASHBOARD_DSN } else { "postgresql://postgres@localhost:5432/apautomation" }
$env:APP_ENV = "LOCAL"
$env:DRY_RUN = "true"

Write-Host "Launching AP dashboard API from $apiPath"
Write-Host "Runtime: LOCAL, DRY_RUN=true"
Write-Host "URL: http://$HostAddress`:$Port"

Push-Location $repoRoot
try {
    & $pythonExe @pythonPrefixArgs -m uvicorn app.api.main:app --host $HostAddress --port $Port
}
finally {
    Pop-Location
}
