<#
README
Usage: .\repro_export_mdb.ps1 -OutDir C:\temp\debug
If -OutDir is omitted, defaults to C:\Users\Michael\Desktop\ce_debug.
#>
param(
    [Parameter(Mandatory=$false)]
    [string]$OutDir = "C:\Users\Michael\Desktop\ce_debug"
)

$ErrorActionPreference = 'Stop'

$OutDir = [IO.Path]::GetFullPath($OutDir)
[IO.Directory]::CreateDirectory($OutDir) | Out-Null

$requestBody = @{
    complex_ids   = @(5087, 5089)
    mdb_name      = 'bom_complexes.mdb'
    export_folder = 'C:/Users/Michael/Desktop'
} | ConvertTo-Json -Depth 4

$requestPath  = Join-Path $OutDir 'request.json'
$statusPath   = Join-Path $OutDir 'http_status.txt'
$headersPath  = Join-Path $OutDir 'response_headers.txt'
$responsePath = Join-Path $OutDir 'response.json'

$requestBody | Set-Content -LiteralPath $requestPath -Encoding UTF8

try {
    $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/exports/mdb' -Method Post -ContentType 'application/json' -Body $requestBody -ErrorAction Stop
} catch {
    $resp = $_.Exception.Response
    $statusCode = if ($resp -and $resp.StatusCode) { [int]$resp.StatusCode } else { 0 }
    if ($resp) {
        $reader = New-Object IO.StreamReader($resp.GetResponseStream())
        $rawBody = $reader.ReadToEnd()
        Set-Content -LiteralPath $responsePath -Value $rawBody -Encoding UTF8
        $resp.Headers | Out-String | Set-Content -LiteralPath $headersPath -Encoding UTF8
    }
    Set-Content -LiteralPath $statusPath -Value $statusCode
    Write-Host "Request:    $requestPath"
    Write-Host "Status:     $statusPath"
    Write-Host "Headers:    $headersPath"
    Write-Host "Response:   $responsePath"
    exit 1
}

[IO.File]::WriteAllText($statusPath, [string][int]$response.StatusCode)
$response.Headers | Out-String | Set-Content -LiteralPath $headersPath -Encoding UTF8
$response.Content | Set-Content -LiteralPath $responsePath -Encoding UTF8

Write-Host "Request:    $requestPath"
Write-Host "Status:     $statusPath"
Write-Host "Headers:    $headersPath"
Write-Host "Response:   $responsePath"

if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
    exit 1
}
