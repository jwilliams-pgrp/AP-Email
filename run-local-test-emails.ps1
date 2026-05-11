param(
  [string]$SourceFolder = "reference\test_emails",
  [string]$SourcePattern = "*.msg",
  [string]$DatabaseUrl = "",
  [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"

if (-not $PythonPath) {
  $KnownPython = "C:\Users\williamsje\AppData\Local\Programs\Python\Python314\python.exe"
  if (Test-Path $KnownPython) {
    $PythonPath = $KnownPython
  } else {
    $PythonPath = "python"
  }
}

$env:PYTHONPATH = "src"
$env:APP_ENV = "LOCAL"
$env:DRY_RUN = "true"

if (-not $env:PGPASSWORD) {
  $env:PGPASSWORD = "llamas"
}

$arguments = @(
  "-m", "ap_automation.cli",
  "--source-folder", $SourceFolder,
  "--source-pattern", $SourcePattern,
  "--codex-skip-git-repo-check"
)

if ($DatabaseUrl) {
  $arguments += @("--database-url", $DatabaseUrl)
}

& $PythonPath @arguments
exit $LASTEXITCODE
