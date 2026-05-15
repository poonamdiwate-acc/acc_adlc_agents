<#
.SYNOPSIS
    Local test script for DE-03 Data Design Agent.
    Reads inputs from C:\SharedFolderAdlc\<ThreadId>\bs_docs\ and writes
    the result to C:\SharedFolderAdlc\<ThreadId>\data_design_response\.

.USAGE
    # Step 1: Start server in a separate terminal:
    #   .\.venv\Scripts\python.exe run.py --host 127.0.0.1 --port 8080

    # Step 2: Drop one or more .json/.docx/.pdf/.html/.htm files into
    #         C:\SharedFolderAdlc\<ThreadId>\bs_docs\

    # Step 3: Run this script:
    .\test_local_de03.ps1 -ThreadId "thr-001" -Port 8080
    .\test_local_de03.ps1 -ThreadId "thr-001" -Port 8080 -Format "html"
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
$InputDir  = Join-Path (Join-Path $BasePath $ThreadId) "bs_docs"
$OutputDir = Join-Path (Join-Path $BasePath $ThreadId) "data_design_response"
$ServerUrl = "http://127.0.0.1:$Port"
$ApiKey    = "replace-me-bearer-token-genwiz-uses"

# --- Ensure output folder exists ---
if (!(Test-Path $OutputDir)) { New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null }

# --- Verify input folder + supported files ---
if (!(Test-Path $InputDir)) {
    Write-Host "[ERROR] Input folder not found: $InputDir" -ForegroundColor Red
    Write-Host "        Create it and drop your business spec files (.json/.docx/.pdf/.html/.htm) first." -ForegroundColor Red
    exit 1
}

$inputFiles = Get-ChildItem -Path $InputDir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in ".json",".docx",".pdf",".html",".htm" }

if ($inputFiles.Count -eq 0) {
    Write-Host "[ERROR] No supported input files in: $InputDir" -ForegroundColor Red
    Write-Host "        Supported extensions: .json .docx .pdf .html .htm" -ForegroundColor Red
    exit 1
}

Write-Host "`n=== DE-03 Local Test ===" -ForegroundColor Cyan
Write-Host "Thread ID : $ThreadId"
Write-Host "Format    : $Format"
Write-Host "Server    : $ServerUrl"
Write-Host "Input dir : $InputDir"
Write-Host "Output dir: $OutputDir"
Write-Host "Files     : $($inputFiles.Name -join ', ')"
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
Write-Host "`n--- Calling POST /agents/data-design?format=$Format ---" -ForegroundColor Cyan

try {
    $response = Invoke-WebRequest -Uri "$ServerUrl/agents/data-design?format=$Format" `
        -Method POST `
        -TimeoutSec 600 `
        -Headers @{
            "Authorization" = "Bearer $ApiKey"
            "X-Run-ID"      = "local-test-de03-001"
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
