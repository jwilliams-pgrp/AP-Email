param(
  [string]$WorkbookPath = "reference\Medius Routing.xlsx",
  [string]$Database = "apautomation",
  [string]$User = "postgres",
  [string]$HostName = "localhost"
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.IO.Compression.FileSystem

function Read-ZipText($zip, [string]$name) {
  $entry = $zip.GetEntry($name)
  if (-not $entry) {
    return $null
  }

  $reader = New-Object System.IO.StreamReader($entry.Open())
  try {
    return $reader.ReadToEnd()
  }
  finally {
    $reader.Dispose()
  }
}

function Convert-CellValue($cell, $sharedStrings) {
  $valueNode = $cell.GetElementsByTagName("v") | Select-Object -First 1
  if (-not $valueNode) {
    return $null
  }

  $rawValue = $valueNode.InnerText
  if ($cell.GetAttribute("t") -eq "s" -and $rawValue -ne "") {
    return $sharedStrings[[int]$rawValue]
  }

  return $rawValue
}

function Get-ColumnLetters([string]$cellRef) {
  return ([regex]::Match($cellRef, "^[A-Z]+")).Value
}

function Quote-Sql($value) {
  if ($null -eq $value -or [string]::IsNullOrWhiteSpace([string]$value)) {
    return "null"
  }

  $stringValue = [string]$value
  return "'" + $stringValue.Replace("'", "''") + "'"
}

function Quote-JsonSql($value) {
  if ($null -eq $value) {
    return "'{}'::jsonb"
  }

  $json = $value | ConvertTo-Json -Compress -Depth 8
  return (Quote-Sql $json) + "::jsonb"
}

function Normalize-Code([string]$value) {
  if ([string]::IsNullOrWhiteSpace($value)) {
    return $null
  }

  return $value.Trim().ToUpperInvariant()
}

function Get-Value($rowMap, [string[]]$names) {
  foreach ($name in $names) {
    if ($rowMap.ContainsKey($name) -and -not [string]::IsNullOrWhiteSpace([string]$rowMap[$name])) {
      return [string]$rowMap[$name]
    }
  }

  return $null
}

$resolvedWorkbook = (Resolve-Path $WorkbookPath).Path
$sourceFileName = Split-Path $resolvedWorkbook -Leaf
$sql = New-Object System.Collections.Generic.List[string]
$propertySheets = @("Building #", "Address #", "Tenant Name", "3rd Party", "BUs", "MF")

$sql.Add("insert into seed_batches (seed_batch_code, description, source_file, metadata) values ('reference_medius_routing_v1', 'Reference import from Medius Routing workbook.', $(Quote-Sql $sourceFileName), '{""environment"":""LOCAL""}'::jsonb) on conflict (seed_batch_code) do update set description = excluded.description, source_file = excluded.source_file, metadata = excluded.metadata;")

$zip = [System.IO.Compression.ZipFile]::OpenRead($resolvedWorkbook)
try {
  $workbook = [xml](Read-ZipText $zip "xl/workbook.xml")
  $rels = [xml](Read-ZipText $zip "xl/_rels/workbook.xml.rels")
  $sharedXml = [xml](Read-ZipText $zip "xl/sharedStrings.xml")

  $sharedStrings = @()
  if ($sharedXml) {
    foreach ($item in $sharedXml.GetElementsByTagName("si")) {
      $sharedStrings += $item.InnerText
    }
  }

  $relMap = @{}
  foreach ($rel in $rels.GetElementsByTagName("Relationship")) {
    $relMap[$rel.GetAttribute("Id")] = $rel.GetAttribute("Target")
  }

  foreach ($sheet in $workbook.GetElementsByTagName("sheet")) {
    $sheetName = $sheet.GetAttribute("name")
    $relationshipId = $sheet.GetAttribute("id", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")
    $target = "xl/" + $relMap[$relationshipId].TrimStart("/")
    $sheetXml = [xml](Read-ZipText $zip $target)
    $rows = @($sheetXml.GetElementsByTagName("row"))

    if ($rows.Count -eq 0) {
      continue
    }

    $headerRow = $null
    $headersByColumn = @{}

    foreach ($row in $rows) {
      $candidate = @{}
      foreach ($cell in @($row.GetElementsByTagName("c"))) {
        $value = Convert-CellValue $cell $sharedStrings
        if (-not [string]::IsNullOrWhiteSpace([string]$value)) {
          $candidate[(Get-ColumnLetters $cell.GetAttribute("r"))] = [string]$value
        }
      }

      $headerValues = @($candidate.Values | Where-Object { $_ -match "BUILDING|ADDRESS|TENANT|BU|Property|Cost Center" })
      if ($headerValues.Count -gt 0) {
        $headerRow = [int]$row.GetAttribute("r")
        foreach ($key in $candidate.Keys) {
          $headersByColumn[$key] = $candidate[$key].Trim()
        }
        break
      }
    }

    if (-not $headerRow) {
      continue
    }

    foreach ($row in $rows) {
      $rowNumber = [int]$row.GetAttribute("r")
      if ($rowNumber -le $headerRow) {
        continue
      }

      $rowMap = @{}
      foreach ($cell in @($row.GetElementsByTagName("c"))) {
        $column = Get-ColumnLetters $cell.GetAttribute("r")
        if (-not $headersByColumn.ContainsKey($column)) {
          continue
        }

        $value = Convert-CellValue $cell $sharedStrings
        if (-not [string]::IsNullOrWhiteSpace([string]$value)) {
          $rowMap[$headersByColumn[$column]] = [string]$value
        }
      }

      if ($rowMap.Count -eq 0) {
        continue
      }

      $sql.Add("insert into reference_rows (source_file, source_sheet, source_row, row_data, seed_batch_code) values ($(Quote-Sql $sourceFileName), $(Quote-Sql $sheetName), $rowNumber, $(Quote-JsonSql $rowMap), 'reference_medius_routing_v1') on conflict (source_file, source_sheet, source_row) do update set row_data = excluded.row_data, seed_batch_code = excluded.seed_batch_code, imported_at = now();")

      if (-not $propertySheets.Contains($sheetName)) {
        continue
      }

      if ($sheetName -eq "BUs") {
        $propertyCode = Normalize-Code (Get-Value $rowMap @("Building Abbreviation"))
      }
      elseif ($sheetName -eq "MF") {
        $propertyCode = Normalize-Code (Get-Value $rowMap @("BU"))
      }
      else {
        $propertyCode = Normalize-Code (Get-Value $rowMap @("BUILDING"))
      }

      if ($propertyCode -and $propertyCode.Length -gt 20) {
        continue
      }

      if ($propertyCode) {
        $propertyName = Get-Value $rowMap @("Building Name", "Property", "TENANT")
        $costCenter = Get-Value $rowMap @("Cost Center")
        $defaultDestination = if ($sheetName -eq "MF") { "MEDIUS_MF" } else { $null }

        $sql.Add("insert into properties (property_code, property_name, cost_center, business_unit_code, default_destination_code, seed_batch_code) values ($(Quote-Sql $propertyCode), $(Quote-Sql $propertyName), $(Quote-Sql $costCenter), $(if ($sheetName -eq "MF") { Quote-Sql "MF" } else { "null" }), $(Quote-Sql $defaultDestination), 'reference_medius_routing_v1') on conflict (property_code) do update set property_name = coalesce(excluded.property_name, properties.property_name), cost_center = coalesce(excluded.cost_center, properties.cost_center), business_unit_code = coalesce(excluded.business_unit_code, properties.business_unit_code), default_destination_code = coalesce(excluded.default_destination_code, properties.default_destination_code), seed_batch_code = excluded.seed_batch_code, updated_at = now();")

        foreach ($aliasSpec in @(
          @{ Type = "tenant"; Value = Get-Value $rowMap @("TENANT") },
          @{ Type = "address"; Value = Get-Value $rowMap @("ADDRESS", "Property Address", "Address") },
          @{ Type = "property_name"; Value = Get-Value $rowMap @("Building Name", "Property") },
          @{ Type = "legal_entity"; Value = Get-Value $rowMap @("Legal Entity") }
        )) {
          if (-not [string]::IsNullOrWhiteSpace($aliasSpec.Value)) {
            $sql.Add("insert into property_aliases (property_id, alias_type, alias_value, source_sheet, source_row, seed_batch_code) select property_id, $(Quote-Sql $aliasSpec.Type), $(Quote-Sql $aliasSpec.Value), $(Quote-Sql $sheetName), $rowNumber, 'reference_medius_routing_v1' from properties where property_code = $(Quote-Sql $propertyCode) on conflict (alias_type, alias_value) do nothing;")
          }
        }

        $routeLabel = Get-Value $rowMap @("PM")
        $subjectInstruction = Get-Value $rowMap @("Notes")
        if (-not [string]::IsNullOrWhiteSpace($routeLabel) -or -not [string]::IsNullOrWhiteSpace($subjectInstruction)) {
          $sql.Add("insert into property_routes (property_id, route_label, subject_instruction, source_sheet, source_row, seed_batch_code) select property_id, $(Quote-Sql $routeLabel), $(Quote-Sql $subjectInstruction), $(Quote-Sql $sheetName), $rowNumber, 'reference_medius_routing_v1' from properties where property_code = $(Quote-Sql $propertyCode) on conflict do nothing;")
        }
      }
    }
  }
}
finally {
  $zip.Dispose()
}

$tempSql = Join-Path $env:TEMP "apautomation_reference_import.sql"
$sql -join [Environment]::NewLine | Set-Content -LiteralPath $tempSql -Encoding UTF8

psql -h $HostName -U $User -d $Database -v ON_ERROR_STOP=1 -f $tempSql
