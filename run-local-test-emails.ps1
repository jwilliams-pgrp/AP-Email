param(
  [string]$SourceFolder = "reference\test_emails",
  [string]$SourcePattern = "*.msg",
  [string]$DatabaseUrl = "",
  [string]$PythonPath = "",
  [switch]$SourceIntake,
  [int]$MaxIntakeMessages = 0
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
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

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
$env:APP_ENV = "LOCAL"
$env:DRY_RUN = "false"

$failed = 0

if ($SourceIntake) {
  $processed = 0
  while ($true) {
    if ($MaxIntakeMessages -gt 0 -and $processed -ge $MaxIntakeMessages) {
      break
    }

    $arguments = @(
      "-m", "ap_automation.cli",
      "--source-intake",
      "--codex-skip-git-repo-check"
    )

    if ($DatabaseUrl) {
      $arguments += @("--database-url", $DatabaseUrl)
    }

    $commandOutput = & $pythonExe @pythonPrefixArgs @arguments 2>&1
    $commandOutputText = ($commandOutput | Out-String).TrimEnd()
    if ($commandOutputText) {
      Write-Host $commandOutputText
    }

    if ($LASTEXITCODE -ne 0) {
      $failed += 1
      Write-Host "Failed with exit code ${LASTEXITCODE} while processing Graph intake."
      continue
    }

    if ($commandOutputText -match "no email found in Graph intake folder") {
      break
    }

    $processed += 1
  }
}
else {
  $sourcePath = Join-Path $repoRoot $SourceFolder
  if (-not (Test-Path -LiteralPath $sourcePath)) {
    throw "Source folder not found: '$sourcePath'."
  }

  $files = @(Get-ChildItem -Path $sourcePath -Filter $SourcePattern -File | Sort-Object -Property FullName)
  if ($files.Count -eq 0) {
    throw "No files matched '$SourcePattern' in '$sourcePath'."
  }

  foreach ($file in $files) {
    Write-Host "Processing $($file.FullName)"

    $arguments = @(
      "-m", "ap_automation.cli",
      "--source-email", $file.FullName,
      "--codex-skip-git-repo-check"
    )

    if ($DatabaseUrl) {
      $arguments += @("--database-url", $DatabaseUrl)
    }

    & $pythonExe @pythonPrefixArgs @arguments
    if ($LASTEXITCODE -ne 0) {
      $failed += 1
      Write-Host "Failed with exit code ${LASTEXITCODE}: $($file.FullName)"
    }
  }
}

if ($failed -gt 0) {
  throw "$failed file(s) failed processing."
}
