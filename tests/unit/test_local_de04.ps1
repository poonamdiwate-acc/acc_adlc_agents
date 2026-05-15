<#
.SYNOPSIS
    Local test script for DE-04 API Contracts Agent.
    Reads inputs from TWO folders and writes to api_contracts_response.

.DESCRIPTION
    The API Contracts agent automatically reads from:
    1. C:\SharedFolderAdlc\<ThreadId>\bs_docs\ - Business requirements
    2. C:\SharedFolderAdlc\<ThreadId>\data_design_response\ - Data model from DE-03
    
    It writes output to:
    - C:\SharedFolderAdlc\<ThreadId>\api_contracts_response\

.USAGE
    # Step 1: Start server in a separate terminal:
    #   .\.venv\Scripts\python.exe run.py --host 127.0.0.1 --port 8080

    # Step 2: Run DE-03 first to generate data design:
    .\test_local_de03.ps1 -ThreadId "thr-005" -Port 8080

    # Step 3: Run this script (it automatically reads from both folders):
    .\test_local_de04.ps1 -ThreadId "thr-005" -Port 8080
    .\test_local_de04.ps1 -ThreadId "thr-005" -Port 8080 -Format "docx"
    .\test_local_de04.ps1 -ThreadId "thr-005" -Port 8080 -Format "html"
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$ThreadId,

    [ValidateSet("json","docx","pdf","html")]
    [string]$Format = "json",

    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

# --- Config ---
$BasePath  = "C:\SharedFolderAdlc"
$InputDir1 = Join-Path (Join-Path $BasePath $ThreadId) "bs_docs"
$InputDir2 = Join-Path (Join-Path $BasePath $ThreadId) "data_design_response"
$OutputDir = Join-Path (Join-Path $BasePath $ThreadId) "api_contracts_response"
$ServerUrl = "http://127.0.0.1:$Port"
$ApiKey    = "replace-me-bearer-token-genwiz-uses"

# --- Ensure output folder exists ---
if (!(Test-Path $OutputDir)) { New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null }

# --- Verify input folders exist ---
if (!(Test-Path $InputDir1)) {
    Write-Host "[ERROR] Input folder not found: $InputDir1" -ForegroundColor Red
    Write-Host "        Create it and drop your business spec files (.json/.docx/.pdf/.html/.htm) first." -ForegroundColor Red
    exit 1
}

if (!(Test-Path $InputDir2)) {
    Write-Host "[ERROR] Data design folder not found: $InputDir2" -ForegroundColor Red
    Write-Host "        Run DE-03 first to generate the data design:" -ForegroundColor Yellow
    Write-Host "        .\test_local_de03.ps1 -ThreadId `"$ThreadId`" -Port $Port" -ForegroundColor Yellow
    exit 1
}

# Get input files from both folders
$inputFiles1 = Get-ChildItem -Path $InputDir1 -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in ".json",".docx",".pdf",".html",".htm" }

# data_design_response should only have JSON files (DE-03 output)
$inputFiles2 = Get-ChildItem -Path $InputDir2 -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -eq ".json" }

if ($inputFiles1.Count -eq 0) {
    Write-Host "[ERROR] No supported input files in: $InputDir1" -ForegroundColor Red
    Write-Host "        Supported extensions: .json .docx .pdf .html .htm" -ForegroundColor Red
    exit 1
}

if ($inputFiles2.Count -eq 0) {
    Write-Host "[WARN] No data design files found in: $InputDir2" -ForegroundColor Yellow
    Write-Host "       Run DE-03 first to generate the data model:" -ForegroundColor Yellow
    Write-Host "       .\test_local_de03.ps1 -ThreadId `"$ThreadId`" -Port $Port" -ForegroundColor Yellow
    Write-Host ""
}

Write-Host "`n=== DE-04 API Contracts Local Test ===" -ForegroundColor Cyan
Write-Host "Thread ID   : $ThreadId"
Write-Host "Format      : $Format"
Write-Host "Server      : $ServerUrl"
Write-Host "Input dir 1 : $InputDir1"
Write-Host "  Files     : $($inputFiles1.Name -join ', ')"
Write-Host "Input dir 2 : $InputDir2"
Write-Host "  Files     : $($inputFiles2.Name -join ', ')"
Write-Host "Output dir  : $OutputDir"
Write-Host ""

# --- Check if server is running ---
try {
    Invoke-RestMethod -Uri "$ServerUrl/health" -TimeoutSec 3 | Out-Null
    Write-Host "[OK] Server is running" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Server not running at $ServerUrl" -ForegroundColor Red
    Write-Host "        Start it first:  .\.venv\Scripts\python.exe run.py --host 127.0.0.1 --port $Port" -ForegroundColor Yellow
    exit 1
}

# --- Call the agent ---
Write-Host "`n--- Calling POST /agents/api-contracts?format=$Format ---" -ForegroundColor Cyan

try {
    $response = Invoke-WebRequest -Uri "$ServerUrl/agents/api-contracts?format=$Format" `
        -Method POST `
        -TimeoutSec 600 `
        -Headers @{
            "Authorization" = "Bearer $ApiKey"
            "X-Run-ID"      = "local-test-de04-001"
            "X-Thread-ID"   = $ThreadId
        } `
        -ContentType "application/json" `
        -Body "{}" `
        -UseBasicParsing

    Write-Host "`n[OK] Status: $($response.StatusCode)" -ForegroundColor Green

    if ($Format -eq "json") {
        $result = $response.Content | ConvertFrom-Json | ConvertTo-Json -Depth 10
        Write-Host $result
    } else {
        Write-Host "Response content-type: $($response.Headers['Content-Type'])" -ForegroundColor Gray
        Write-Host "Output written to shared folder: $OutputDir" -ForegroundColor Green
    }
} catch {
    $err = $_
    Write-Host "`n[FAIL] HTTP Error" -ForegroundColor Red
    if ($err.Exception.Response) {
        $statusCode = [int]$err.Exception.Response.StatusCode
        Write-Host "Status: $statusCode" -ForegroundColor Red
        $reader = [System.IO.StreamReader]::new($err.Exception.Response.GetResponseStream())
        $body = $reader.ReadToEnd()
        $reader.Close()
        Write-Host "Body:" -ForegroundColor Red
        try {
            $body | ConvertFrom-Json | ConvertTo-Json -Depth 5 | Out-String | Write-Host -ForegroundColor Red
        } catch {
            Write-Host $body -ForegroundColor Red
        }
    } else {
        Write-Host $err.Exception.Message -ForegroundColor Red
    }
}

# --- Show output folder contents ---
Write-Host "`n--- Output folder ---" -ForegroundColor Cyan
if (Test-Path $OutputDir) {
    $files = Get-ChildItem $OutputDir -ErrorAction SilentlyContinue
    if ($files) {
        $files | Format-Table Name, Length, LastWriteTime -AutoSize
    } else {
        Write-Host "(empty)" -ForegroundColor Gray
    }
} else {
    Write-Host "(not created yet)" -ForegroundColor Gray
}

Write-Host "`n=== Test Complete ===" -ForegroundColor Cyan
