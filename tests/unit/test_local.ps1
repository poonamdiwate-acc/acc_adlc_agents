<#
.SYNOPSIS
    Local test script for PL-01 Gap Detection Agent.
    Only parameter needed: thread ID (folder must already exist with input files).

.USAGE
    # Step 1: Start server in a separate terminal:
    #   .\.venv\Scripts\python.exe run.py

    # Step 2: Run this script:
    .\test_local.ps1 -ThreadId "threadid100"
    .\test_local.ps1 -ThreadId "threadid100" -Format "html"
    .\test_local.ps1 -ThreadId "threadid100" -SetupSample   # creates sample input file first
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$ThreadId,

    [ValidateSet("json","docx","pdf","html")]
    [string]$Format = "json",

    [switch]$SetupSample
)

$ErrorActionPreference = "Stop"

# --- Config ---
$BasePath   = "C:\SharedFolderAdlc"
$InputDir   = Join-Path (Join-Path $BasePath $ThreadId) "bs_docs"
$OutputDir  = Join-Path (Join-Path $BasePath $ThreadId) "gap_response"
$ServerUrl  = "http://localhost:8000"
$ApiKey     = "replace-me-bearer-token-genwiz-uses"

# --- Create folder structure ---
if (!(Test-Path $InputDir))  { New-Item -ItemType Directory -Path $InputDir  -Force | Out-Null }
if (!(Test-Path $OutputDir)) { New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null }

# --- Optionally create a sample JSON input ---
if ($SetupSample) {
    $sampleFile = Join-Path $InputDir "sample_requirements.json"
    if (!(Test-Path $sampleFile)) {
        @{
            structured_requirements = @(
                @{
                    req_id       = "REQ-001"
                    title        = "User Authentication"
                    description  = "The system must provide user authentication"
                    type         = "functional"
                    priority     = "high"
                    acceptance_criteria = @("Users can log in with email and password")
                },
                @{
                    req_id       = "REQ-002"
                    title        = "System Performance"
                    description  = "The system should be fast"
                    type         = "non-functional"
                    priority     = "high"
                    acceptance_criteria = @()
                },
                @{
                    req_id       = "REQ-003"
                    title        = "Data Export"
                    description  = "Users should be able to export data easily"
                    type         = "functional"
                    priority     = "medium"
                    acceptance_criteria = @()
                }
            )
            business_case = "Build a customer portal that enables self-service account management, real-time billing visibility, and automated support ticket creation. The portal must reduce call-center volume by 40%."
            project_context = @{
                project_name = "Customer Self-Service Portal"
                squad        = "Digital Experience"
                domain       = "customer-engagement"
            }
            scope_boundaries = @{
                in_scope     = @("authentication", "billing dashboard", "ticket creation", "data export")
                out_of_scope = @("payment processing", "CRM integration")
            }
        } | ConvertTo-Json -Depth 5 | Set-Content -Path $sampleFile -Encoding UTF8
        Write-Host "[OK] Sample input created: $sampleFile" -ForegroundColor Green
    } else {
        Write-Host "[SKIP] Sample already exists: $sampleFile" -ForegroundColor Yellow
    }
}

# --- Verify input files exist ---
$inputFiles = Get-ChildItem -Path $InputDir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in ".json",".docx",".pdf",".html",".htm" }

if ($inputFiles.Count -eq 0) {
    Write-Host "[ERROR] No input files found in: $InputDir" -ForegroundColor Red
    Write-Host "        Place .json, .docx, .pdf, or .html files there, or use -SetupSample" -ForegroundColor Red
    exit 1
}

Write-Host "`n=== PL-01 Local Test ===" -ForegroundColor Cyan
Write-Host "Thread ID : $ThreadId"
Write-Host "Format    : $Format"
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
    Write-Host "        Start it first:  .\.venv\Scripts\python.exe run.py" -ForegroundColor Yellow
    exit 1
}

# --- Call the agent ---
Write-Host "`n--- Calling POST /agents/gap-detection?format=$Format ---" -ForegroundColor Cyan

try {
    $response = Invoke-WebRequest -Uri "$ServerUrl/agents/gap-detection?format=$Format" `
        -Method POST `
        -TimeoutSec 600 `
        -Headers @{
            "Authorization" = "Bearer $ApiKey"
            "X-Run-ID"      = "local-test-001"
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
        try {
            $body | ConvertFrom-Json | ConvertTo-Json -Depth 5
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
