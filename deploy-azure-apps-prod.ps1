param(
    [string]$Subscription,
    [string]$ResourceGroup = "rg-hw-propertiesapmail-prod",
    [switch]$SkipSwa,
    [switch]$SkipFunction,
    [switch]$ConfirmProduction
)

$ErrorActionPreference = "Stop"

if (-not $ConfirmProduction) {
    throw "Production app deployment requires -ConfirmProduction."
}

function Test-Command {
    param(
        [string]$Name,
        [string]$InstallHint
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found. $InstallHint"
    }
}

function Get-ParameterValue {
    param(
        [object]$Parameters,
        [string]$Name
    )

    $parameter = $Parameters.parameters.$Name
    if (-not $parameter -or [string]::IsNullOrWhiteSpace([string]$parameter.value)) {
        throw "Parameter '$Name' was not found or has no value in infra\main.parameters.prod.json."
    }

    return [string]$parameter.value
}

function Assert-PathExists {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Required path not found: $Path"
    }
}

function Assert-KeyVaultSecretsExist {
    param([string]$VaultName)

    $requiredSecretNames = @(
        "AZURE-CLIENT-ID-MAIL",
        "AZURE-CLIENT-SECRET-MAIL",
        "AZURE-TENANT-ID",
        "TEAMS-WEBHOOK-URL-PROPERTIES-AP"
    )

    foreach ($secretName in $requiredSecretNames) {
        $secretId = az keyvault secret show `
            --vault-name $VaultName `
            --name $secretName `
            --query "id" `
            -o tsv 2>$null

        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($secretId)) {
            throw "Required Key Vault secret '$secretName' was not found in '$VaultName'. Create it before production app deployment."
        }
    }
}

function Copy-DirectoryContents {
    param(
        [string]$Source,
        [string]$Destination
    )

    Assert-PathExists -Path $Source
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Copy-Item -Path (Join-Path $Source "*") -Destination $Destination -Recurse -Force
}

function New-FunctionZipFromDirectory {
    param(
        [string]$SourceDirectory,
        [string]$DestinationPath
    )

    Add-Type -AssemblyName System.IO.Compression.FileSystem

    $sourceRoot = (Resolve-Path -LiteralPath $SourceDirectory).Path.TrimEnd([System.IO.Path]::DirectorySeparatorChar)
    $zip = [System.IO.Compression.ZipFile]::Open($DestinationPath, [System.IO.Compression.ZipArchiveMode]::Create)

    try {
        Get-ChildItem -LiteralPath $sourceRoot -Recurse -File | ForEach-Object {
            $relativePath = $_.FullName.Substring($sourceRoot.Length).TrimStart("\", "/")
            $entryName = $relativePath -replace "\\", "/"
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $zip,
                $_.FullName,
                $entryName,
                [System.IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
    }
    finally {
        $zip.Dispose()
    }
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$parameterFile = Join-Path $repoRoot "infra\main.parameters.prod.json"
$webRoot = Join-Path $repoRoot "app\web"
$webDist = Join-Path $webRoot "dist"
$swaConfig = Join-Path $repoRoot "staticwebapp.config.json"
$deployRoot = Join-Path $repoRoot ".deploy\prod"
$functionStage = Join-Path $deployRoot "function"
$functionZip = Join-Path $deployRoot "function-app.zip"

Test-Command -Name "az" -InstallHint "Install Azure CLI, then run 'az login'."
Test-Command -Name "npm" -InstallHint "Install Node.js LTS."
Test-Command -Name "npx" -InstallHint "Install Node.js LTS."
Test-Command -Name "python" -InstallHint "Install Python 3.11 or newer and ensure 'python' is on PATH."

Assert-PathExists -Path $parameterFile
Assert-PathExists -Path (Join-Path $repoRoot "function_app.py")
Assert-PathExists -Path (Join-Path $repoRoot "host.json")
Assert-PathExists -Path (Join-Path $repoRoot "requirements.txt")
Assert-PathExists -Path (Join-Path $repoRoot "pyproject.toml")
Assert-PathExists -Path (Join-Path $repoRoot "src\ap_automation")
Assert-PathExists -Path (Join-Path $repoRoot "app\api")
Assert-PathExists -Path (Join-Path $webRoot "package.json")
Assert-PathExists -Path $swaConfig

if ($Subscription) {
    az account set --subscription $Subscription
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to set Azure subscription '$Subscription'."
    }
}

az account show --only-show-errors | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Azure CLI is not logged in. Run 'az login' and retry."
}

$parameters = Get-Content -Path $parameterFile -Raw | ConvertFrom-Json
$staticWebAppName = Get-ParameterValue -Parameters $parameters -Name "staticWebAppName"
$functionAppName = Get-ParameterValue -Parameters $parameters -Name "functionAppName"
$keyVaultName = Get-ParameterValue -Parameters $parameters -Name "keyVaultName"

Assert-KeyVaultSecretsExist -VaultName $keyVaultName

$expectedFunctionAppId = az functionapp show `
    --name $functionAppName `
    --resource-group $ResourceGroup `
    --query "id" `
    -o tsv
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($expectedFunctionAppId)) {
    throw "Failed to resolve expected Function App resource id for '$functionAppName'. Deploy or validate infrastructure before app deployment."
}

$linkedBackendJson = az staticwebapp backends show `
    --name $staticWebAppName `
    --resource-group $ResourceGroup `
    -o json
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($linkedBackendJson)) {
    throw "Static Web App '$staticWebAppName' is missing a linked backend. Run the Bicep infrastructure deployment before app deployment."
}

$linkedBackend = $linkedBackendJson | ConvertFrom-Json
$linkedBackendRecord = @($linkedBackend) | Where-Object { $_.name -eq "functionApp" } | Select-Object -First 1
if (-not $linkedBackendRecord) {
    throw "Static Web App '$staticWebAppName' is missing linked backend 'functionApp'. Run the Bicep infrastructure deployment before app deployment."
}

$linkedBackendResourceId = [string]$linkedBackendRecord.backendResourceId
if ([string]::IsNullOrWhiteSpace($linkedBackendResourceId) -and $linkedBackendRecord.properties) {
    $linkedBackendResourceId = [string]$linkedBackendRecord.properties.backendResourceId
}
if ($linkedBackendResourceId -ne $expectedFunctionAppId) {
    throw "Static Web App '$staticWebAppName' linked backend points to '$linkedBackendResourceId', expected '$expectedFunctionAppId'. Redeploy infrastructure to link the correct Function App backend."
}

if (-not $SkipSwa) {
    Write-Host "Building Static Web App '$staticWebAppName'."
    if (-not (Test-Path -LiteralPath (Join-Path $webRoot "node_modules"))) {
        npm --prefix $webRoot install
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed."
        }
    }

    npm --prefix $webRoot run build
    if ($LASTEXITCODE -ne 0) {
        throw "npm build failed."
    }

    Copy-Item -LiteralPath $swaConfig -Destination (Join-Path $webDist "staticwebapp.config.json") -Force

    Write-Host "Fetching Static Web App deployment token."
    $swaToken = az staticwebapp secrets list `
        --name $staticWebAppName `
        --resource-group $ResourceGroup `
        --query "properties.apiKey" `
        -o tsv
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($swaToken)) {
        throw "Failed to retrieve Static Web App deployment token."
    }

    Write-Host "Deploying Static Web App '$staticWebAppName'."
    Push-Location -LiteralPath $repoRoot
    $previousNodeOptions = $env:NODE_OPTIONS
    try {
        if ([string]::IsNullOrWhiteSpace($env:NODE_OPTIONS)) {
            $env:NODE_OPTIONS = "--use-system-ca"
        }
        elseif ($env:NODE_OPTIONS -notmatch "(^|\s)--use-system-ca(\s|$)") {
            $env:NODE_OPTIONS = "$env:NODE_OPTIONS --use-system-ca"
        }

        npx @azure/static-web-apps-cli deploy $webDist `
            --app-name $staticWebAppName `
            --resource-group $ResourceGroup `
            --deployment-token $swaToken `
            --swa-config-location $webDist `
            --env production
        if ($LASTEXITCODE -ne 0) {
            throw "Static Web App deployment failed."
        }
    }
    finally {
        $env:NODE_OPTIONS = $previousNodeOptions
        Pop-Location
    }
}

if (-not $SkipFunction) {
    Write-Host "Packaging Function App '$functionAppName'."
    if (Test-Path -LiteralPath $functionStage) {
        Remove-Item -LiteralPath $functionStage -Recurse -Force
    }
    New-Item -ItemType Directory -Path $functionStage -Force | Out-Null
    $wheelStage = Join-Path $functionStage "wheels"
    New-Item -ItemType Directory -Path $wheelStage -Force | Out-Null

    Write-Host "Building local ap_automation wheel."
    python -m pip wheel --no-deps --wheel-dir $wheelStage $repoRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Local ap_automation wheel build failed."
    }

    $localPackageWheels = Get-ChildItem -LiteralPath $wheelStage -Filter "ap_automation-*.whl" |
        Sort-Object LastWriteTime -Descending
    if ($localPackageWheels.Count -ne 1) {
        throw "Expected exactly one ap_automation wheel in $wheelStage, found $($localPackageWheels.Count)."
    }

    Copy-Item -LiteralPath (Join-Path $repoRoot "function_app.py") -Destination $functionStage -Force
    Copy-Item -LiteralPath (Join-Path $repoRoot "host.json") -Destination $functionStage -Force
    Copy-Item -LiteralPath (Join-Path $repoRoot "requirements.txt") -Destination $functionStage -Force
    Add-Content -LiteralPath (Join-Path $functionStage "requirements.txt") -Value ""
    Add-Content -LiteralPath (Join-Path $functionStage "requirements.txt") -Value "wheels/$($localPackageWheels[0].Name)"
    New-Item -ItemType Directory -Path (Join-Path $functionStage "app") -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $repoRoot "app\__init__.py") -Destination (Join-Path $functionStage "app") -Force
    Copy-DirectoryContents -Source (Join-Path $repoRoot "app\api") -Destination (Join-Path $functionStage "app\api")

    if (Test-Path -LiteralPath $functionZip) {
        Remove-Item -LiteralPath $functionZip -Force
    }

    New-FunctionZipFromDirectory -SourceDirectory $functionStage -DestinationPath $functionZip

    Write-Host "Deploying Function App '$functionAppName'."
    az functionapp deployment source config-zip `
        --resource-group $ResourceGroup `
        --name $functionAppName `
        --src $functionZip `
        --build-remote true
    if ($LASTEXITCODE -ne 0) {
        throw "Function App deployment failed."
    }
}

Write-Host ""
Write-Host "Deployment targets:"

if (-not $SkipSwa) {
    $swaHost = az staticwebapp show `
        --name $staticWebAppName `
        --resource-group $ResourceGroup `
        --query "defaultHostname" `
        -o tsv
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($swaHost)) {
        Write-Host "Static Web App: https://$swaHost"
    }
}

if (-not $SkipFunction) {
    $functionHost = az functionapp show `
        --name $functionAppName `
        --resource-group $ResourceGroup `
        --query "defaultHostName" `
        -o tsv
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($functionHost)) {
        Write-Host "Function App: https://$functionHost"
    }

    Write-Host ""
    Write-Host "Discovered functions:"
    az functionapp function list `
        --name $functionAppName `
        --resource-group $ResourceGroup `
        --query "[].name" `
        -o table
    if ($LASTEXITCODE -ne 0) {
        throw "Function deployment completed, but function discovery failed."
    }
}
