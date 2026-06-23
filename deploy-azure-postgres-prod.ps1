param(
    [string]$Subscription,
    [string]$ResourceGroup = "rg0hw-propertiesapmail-prod",
    [string]$PostgresServer = "pg-hw-propertiesapmail-prod",
    [string]$DatabaseName = "apautomation",
    [Parameter(Mandatory = $true)]
    [string]$AdminUser,
    [string]$FunctionIdentityName = "id-hw-propertiesapmail-prod",
    [switch]$ConfirmProduction
)

$ErrorActionPreference = "Stop"

if (-not $ConfirmProduction) {
    throw "Production PostgreSQL deployment requires -ConfirmProduction."
}

function Test-CommandAvailable {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found in PATH."
    }
}

function Invoke-PsqlFile {
    param(
        [string]$Database,
        [string]$File,
        [string[]]$ExtraArgs = @()
    )

    psql `
        -h $script:HostName `
        -U $AdminUser `
        -d $Database `
        -v ON_ERROR_STOP=1 `
        @ExtraArgs `
        -f $File

    if ($LASTEXITCODE -ne 0) {
        throw "psql failed while applying $File"
    }
}

function Invoke-PsqlCommand {
    param(
        [string]$Database,
        [string]$Command
    )

    psql `
        -h $script:HostName `
        -U $AdminUser `
        -d $Database `
        -v ON_ERROR_STOP=1 `
        -c $Command

    if ($LASTEXITCODE -ne 0) {
        throw "psql failed while running command against $Database"
    }
}

function Register-FunctionIdentityPrincipal {
    $principalObjectId = az identity show `
        --resource-group $ResourceGroup `
        --name $FunctionIdentityName `
        --query "principalId" `
        -o tsv

    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($principalObjectId)) {
        throw "Failed to resolve managed identity principal id for '$FunctionIdentityName'."
    }

    $escapedName = $FunctionIdentityName.Replace("'", "''")
    $escapedObjectId = $principalObjectId.Replace("'", "''")
    $principalExists = psql `
        -h $script:HostName `
        -U $AdminUser `
        -d postgres `
        -v ON_ERROR_STOP=1 `
        -At `
        -c "select 1 from pgaadauth_list_principals(false) where rolname = '$escapedName';"

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to check Azure PostgreSQL Entra principal registration for '$FunctionIdentityName'."
    }

    if ($principalExists) {
        Invoke-PsqlCommand -Database "postgres" -Command "select pgaadauth_update_principal_with_oid('$escapedName', '$escapedObjectId', 'service', false, false);"
    }
    elseif (psql -h $script:HostName -U $AdminUser -d postgres -v ON_ERROR_STOP=1 -At -c "select 1 from pg_roles where rolname = '$escapedName';") {
        Invoke-PsqlCommand -Database "postgres" -Command "select pgaadauth_update_principal_with_oid('$escapedName', '$escapedObjectId', 'service', false, false);"
    }
    else {
        Invoke-PsqlCommand -Database "postgres" -Command "select pgaadauth_create_principal_with_oid('$escapedName', '$escapedObjectId', 'service', false, false);"
    }
}

function Enable-RequiredAzureExtensions {
    $requiredExtensions = @("PGCRYPTO", "PG_TRGM")
    $currentValue = az postgres flexible-server parameter show `
        --resource-group $ResourceGroup `
        --server-name $PostgresServer `
        --name "azure.extensions" `
        --query "value" `
        -o tsv

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to read Azure PostgreSQL azure.extensions parameter."
    }

    $extensions = @()
    if (-not [string]::IsNullOrWhiteSpace($currentValue)) {
        $extensions = $currentValue.Split(",") |
            ForEach-Object { $_.Trim() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    }

    foreach ($requiredExtension in $requiredExtensions) {
        if ($extensions -notcontains $requiredExtension) {
            $extensions += $requiredExtension
        }
    }

    $newValue = ($extensions | Select-Object -Unique) -join ","
    if ($newValue -ne $currentValue) {
        az postgres flexible-server parameter set `
            --resource-group $ResourceGroup `
            --server-name $PostgresServer `
            --name "azure.extensions" `
            --value $newValue `
            --only-show-errors | Out-Null

        if ($LASTEXITCODE -ne 0) {
            throw "Failed to allow-list required Azure PostgreSQL extensions: $newValue"
        }
    }
}

function New-AzureCompatibleSchemaFile {
    param([string]$SourceFile)

    $temporaryFile = Join-Path ([System.IO.Path]::GetTempPath()) "apautomation-azure-$([System.Guid]::NewGuid()).sql"
    Get-Content -Path $SourceFile |
        Where-Object { $_ -notmatch '^\s*SET\s+transaction_timeout\s*=' } |
        Set-Content -Path $temporaryFile -Encoding UTF8

    return $temporaryFile
}

Test-CommandAvailable -Name "az"
Test-CommandAvailable -Name "psql"

if ($Subscription) {
    az account set --subscription $Subscription
    if ($LASTEXITCODE -ne 0) {
        throw "az account set failed."
    }
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$schemaFile = Join-Path $repoRoot "db\schema.sql"
$seedFile = Join-Path $repoRoot "db\seed.sql"
$permissionsFile = Join-Path $repoRoot "db\azure-permissions.sql"

foreach ($file in @($schemaFile, $seedFile, $permissionsFile)) {
    if (-not (Test-Path $file)) {
        throw "Required SQL file not found: $file"
    }
}

$script:HostName = "$PostgresServer.postgres.database.azure.com"

Enable-RequiredAzureExtensions

$token = az account get-access-token --resource-type oss-rdbms --query accessToken -o tsv
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token)) {
    throw "Failed to acquire Azure PostgreSQL Entra token."
}

$env:PGPASSWORD = $token

$existingDatabase = psql `
    -h $script:HostName `
    -U $AdminUser `
    -d postgres `
    -v ON_ERROR_STOP=1 `
    -At `
    -c "select 1 from pg_database where datname = '$DatabaseName';"

if ($LASTEXITCODE -ne 0) {
    throw "Failed to check database existence."
}

if (-not $existingDatabase) {
    psql `
        -h $script:HostName `
        -U $AdminUser `
        -d postgres `
        -v ON_ERROR_STOP=1 `
        -c "create database ""$DatabaseName"";"

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create database $DatabaseName."
    }
}

try {
    $azureSchemaFile = New-AzureCompatibleSchemaFile -SourceFile $schemaFile
    $azureSeedFile = New-AzureCompatibleSchemaFile -SourceFile $seedFile
    Invoke-PsqlFile -Database $DatabaseName -File $azureSchemaFile
    Invoke-PsqlFile -Database $DatabaseName -File $azureSeedFile
    Register-FunctionIdentityPrincipal
    Invoke-PsqlFile -Database $DatabaseName -File $permissionsFile -ExtraArgs @("-v", "function_identity_name=$FunctionIdentityName")
}
finally {
    if ($azureSchemaFile -and (Test-Path $azureSchemaFile)) {
        Remove-Item -LiteralPath $azureSchemaFile -Force
    }
    if ($azureSeedFile -and (Test-Path $azureSeedFile)) {
        Remove-Item -LiteralPath $azureSeedFile -Force
    }
}

psql `
    -h $script:HostName `
    -U $AdminUser `
    -d $DatabaseName `
    -v ON_ERROR_STOP=1 `
    -c "select 'routing_destinations' as table_name, count(*) from routing_destinations union all select 'runtime_config', count(*) from runtime_config union all select 'ownership', count(*) from ownership union all select 'asset', count(*) from asset union all select 'workflow_rules', count(*) from workflow_rules union all select 'workflow_rule_conditions', count(*) from workflow_rule_conditions order by table_name;"

if ($LASTEXITCODE -ne 0) {
    throw "Verification query failed."
}
