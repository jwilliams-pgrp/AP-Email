param(
    [string]$AppPath,
    [int]$Port = 5173,
    [string]$HostAddress = "127.0.0.1",
    [string]$ApiBase,
    [switch]$Install
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $AppPath) {
    $candidatePaths = @(
        "app",
        "app\web",
        "app\client",
        "app\frontend",
        "client",
        "frontend"
    )

    foreach ($candidatePath in $candidatePaths) {
        $packageJsonPath = Join-Path $repoRoot (Join-Path $candidatePath "package.json")
        if (Test-Path $packageJsonPath) {
            $AppPath = Join-Path $repoRoot $candidatePath
            break
        }
    }
}

if (-not $AppPath) {
    throw "Could not find a React app package.json in known local paths. Pass -AppPath explicitly."
}

$resolvedAppPath = Resolve-Path $AppPath
$packageJsonPath = Join-Path $resolvedAppPath "package.json"

if (-not (Test-Path $packageJsonPath)) {
    throw "No package.json found at '$packageJsonPath'."
}

$packageJson = Get-Content $packageJsonPath -Raw | ConvertFrom-Json
$scripts = $packageJson.scripts

if ($scripts.PSObject.Properties.Name -contains "dev") {
    $scriptName = "dev"
    $scriptCommand = [string]$scripts.dev
}
elseif ($scripts.PSObject.Properties.Name -contains "start") {
    $scriptName = "start"
    $scriptCommand = [string]$scripts.start
}
else {
    throw "package.json must define a 'dev' or 'start' script."
}

$viteBin = Join-Path $resolvedAppPath "node_modules\vite\bin\vite.js"

if (Test-Path (Join-Path $resolvedAppPath "pnpm-lock.yaml")) {
    $packageManager = "pnpm"
}
elseif (Test-Path (Join-Path $resolvedAppPath "yarn.lock")) {
    $packageManager = "yarn"
}
else {
    $packageManager = "npm"
}

$env:DRY_RUN = "true"
$env:RUNTIME = "LOCAL"
$env:HOST = $HostAddress
$env:PORT = "$Port"
if ($ApiBase) {
    $env:VITE_API_BASE = $ApiBase
}

Write-Host "Launching React app from $resolvedAppPath"
Write-Host "Runtime: LOCAL, DRY_RUN=true"
Write-Host "URL: http://$HostAddress`:$Port"
if ($ApiBase) {
    Write-Host "API: $ApiBase"
}

Push-Location $resolvedAppPath
try {
    if ($Install) {
        if ($packageManager -eq "npm") {
            npm install
        }
        elseif ($packageManager -eq "pnpm") {
            pnpm install
        }
        else {
            yarn install
        }
    }

    if (Test-Path $viteBin) {
        node $viteBin --host $HostAddress --port $Port
    }
    else {
        $runArgs = @()
        if ($scriptCommand -notmatch "(^|\s)--host(\s|=|$)") {
            $runArgs += @("--host", $HostAddress)
        }
        if ($scriptCommand -notmatch "(^|\s)--port(\s|=|$)") {
            $runArgs += @("--port", "$Port")
        }

        if ($packageManager -eq "npm") {
            if ($runArgs.Count -gt 0) {
                npm run $scriptName -- @runArgs
            }
            else {
                npm run $scriptName
            }
        }
        elseif ($packageManager -eq "pnpm") {
            pnpm run $scriptName @runArgs
        }
        else {
            yarn $scriptName @runArgs
        }
    }
}
finally {
    Pop-Location
}
