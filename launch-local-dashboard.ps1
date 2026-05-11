param(
    [int]$ApiPort = 8001,
    [int]$WebPort = 5173,
    [string]$HostAddress = "127.0.0.1"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$apiBase = "http://$HostAddress`:$ApiPort"

Write-Host "Starting AP dashboard API at $apiBase"
Start-Process -FilePath powershell.exe -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-NoExit",
    "-File",
    (Join-Path $repoRoot "launch-dashboard-api.ps1"),
    "-Port",
    "$ApiPort",
    "-HostAddress",
    $HostAddress
) -WorkingDirectory $repoRoot

Start-Sleep -Seconds 2

Write-Host "Starting React dashboard at http://$HostAddress`:$WebPort"
Start-Process -FilePath powershell.exe -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-NoExit",
    "-File",
    (Join-Path $repoRoot "launch-react-app.ps1"),
    "-Port",
    "$WebPort",
    "-HostAddress",
    $HostAddress,
    "-ApiBase",
    $apiBase
) -WorkingDirectory $repoRoot

Write-Host "Dashboard URL: http://$HostAddress`:$WebPort"
