param(
    [string]$Subscription,
    [string]$ResourceGroup = "rg-hw-propertiesapmail-nonprod",
    [switch]$WhatIf,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"

function Test-AzCli {
    $az = Get-Command az -ErrorAction SilentlyContinue
    if (-not $az) {
        throw "Azure CLI was not found. Install Azure CLI, then run 'az login'."
    }
}

function Assert-NoPlaceholderParameters {
    param([string]$Path)

    $raw = Get-Content -Path $Path -Raw
    $blockedValues = @(
        "00000000-0000-0000-0000-000000000000",
        "replace-with-entra-admin-display-name",
        "replace-with-deployment-name",
        "replace-with-ap-intake-mailbox-upn"
    )

    foreach ($value in $blockedValues) {
        if ($raw.Contains($value)) {
            throw "Parameter file '$Path' still contains placeholder value '$value'. Update it before deployment."
        }
    }
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$templateFile = Join-Path $repoRoot "infra\main.bicep"
$parameterFile = Join-Path $repoRoot "infra\main.parameters.nonprod.json"

Test-AzCli

if (-not (Test-Path $templateFile)) {
    throw "Template file not found: $templateFile"
}

if (-not (Test-Path $parameterFile)) {
    throw "Parameter file not found: $parameterFile"
}

if ($Subscription) {
    az account set --subscription $Subscription
}

Assert-NoPlaceholderParameters -Path $parameterFile

if ($ValidateOnly) {
    az deployment group validate `
        --resource-group $ResourceGroup `
        --template-file $templateFile `
        --parameters "@$parameterFile"
    exit $LASTEXITCODE
}

if ($WhatIf) {
    az deployment group what-if `
        --resource-group $ResourceGroup `
        --template-file $templateFile `
        --parameters "@$parameterFile"
    exit $LASTEXITCODE
}

az deployment group create `
    --resource-group $ResourceGroup `
    --template-file $templateFile `
    --parameters "@$parameterFile"
exit $LASTEXITCODE
