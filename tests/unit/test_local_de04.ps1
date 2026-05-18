<#
.SYNOPSIS
    Local test script for DE-04 API Contracts Agent.
    Reads inputs from multiple sources and writes to api_contracts_response.

.DESCRIPTION
    The API Contracts agent automatically reads from:
    1. C:\SharedFolderAdlc\<ThreadId>\bs_docs\ - Business requirements (.json/.docx/.pdf/.html)
    2. C:\SharedFolderAdlc\<ThreadId>\Business_Process_Agent_Interaction.html/md - Agent interaction diagram (REQUIRED)
    3. C:\SharedFolderAdlc\<ThreadId>\Business_Process_Agent_Network.html/md - Agent network diagram (optional)
    4. C:\SharedFolderAdlc\<ThreadId>\brd_response\agent_architecture.json - Architecture (optional)
    
    It writes output to:
    - C:\SharedFolderAdlc\<ThreadId>\api_contracts_response\api_contracts_design.json
    - C:\SharedFolderAdlc\<ThreadId>\api_contracts_response\api_contracts_design.docx

.USAGE
    # Step 1: Start server in a separate terminal:
    #   .\.venv\Scripts\python.exe run.py --host 127.0.0.1 --port 8080

    # Step 2: Ensure thread folder has required files (see test setup):
    .\test_local_de04.ps1 -ThreadId "thr-test-de04-new" -Port 8080

    # Step 3: Run this script:
    .\test_local_de04.ps1 -ThreadId "thr-test-de04-new" -Port 8080
    .\test_local_de04.ps1 -ThreadId "thr-test-de04-new" -Port 8080 -Format "docx"
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
$ThreadPath = Join-Path $BasePath $ThreadId
$InputDir = Join-Path $ThreadPath "bs_docs"
$OutputDir = Join-Path $ThreadPath "api_contracts_response"
$ServerUrl = "http://127.0.0.1:$Port"
$ApiKey    = "replace-me-bearer-token-genwiz-uses"

# --- Ensure output folder exists ---
if (!(Test-Path $OutputDir)) { New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null }

# --- Verify thread folder exists ---
if (!(Test-Path $ThreadPath)) {
    Write-Host "[ERROR] Thread folder not found: $ThreadPath" -ForegroundColor Red
    Write-Host "        Create it first:  New-Item -ItemType Directory -Path `"$ThreadPath`"" -ForegroundColor Yellow
    exit 1
}

# --- Verify bs_docs folder exists ---
if (!(Test-Path $InputDir)) {
    Write-Host "[ERROR] Input folder not found: $InputDir" -ForegroundColor Red
    Write-Host "        Create it and drop your business requirements (.json/.docx/.pdf/.html) first." -ForegroundColor Red
    exit 1
}

# Get input files from bs_docs
$inputFiles = Get-ChildItem -Path $InputDir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in ".json",".docx",".pdf",".html",".htm" }

if ($inputFiles.Count -eq 0) {
    Write-Host "[ERROR] No supported input files in: $InputDir" -ForegroundColor Red
    Write-Host "        Supported extensions: .json .docx .pdf .html .htm" -ForegroundColor Red
    exit 1
}

# --- Check for required agent interaction diagram ---
$interactionDiagram = Get-ChildItem -Path $ThreadPath -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in "Business_Process_Agent_Interaction.html","Business_Process_Agent_Interaction.md" } |
    Select-Object -First 1

if (-not $interactionDiagram) {
    Write-Host "[ERROR] Required file not found: Business_Process_Agent_Interaction.html or .md" -ForegroundColor Red
    Write-Host "        Expected in: $ThreadPath" -ForegroundColor Red
    Write-Host "        This file is MANDATORY for API Contract generation." -ForegroundColor Red
    exit 1
}

# --- Check for optional files ---
$networkDiagram = Get-ChildItem -Path $ThreadPath -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in "Business_Process_Agent_Network.html","Business_Process_Agent_Network.md" } |
    Select-Object -First 1

$brdResponseDir = Join-Path $ThreadPath "brd_response"
$architectureFile = $null
if (Test-Path $brdResponseDir) {
    $architectureFile = Get-ChildItem -Path $brdResponseDir -File -Filter "agent_architecture.json" -ErrorAction SilentlyContinue
}

Write-Host "`n=== DE-04 API Contracts Local Test ===" -ForegroundColor Cyan
Write-Host "Thread ID   : $ThreadId"
Write-Host "Format      : $Format"
Write-Host "Server      : $ServerUrl"
Write-Host "`nInput Files:" -ForegroundColor Yellow
Write-Host "  bs_docs/  : $($inputFiles.Name -join ', ')" -ForegroundColor Green
Write-Host "  Required  : $($interactionDiagram.Name)" -ForegroundColor Green
if ($networkDiagram) {
    Write-Host "  Optional  : $($networkDiagram.Name)" -ForegroundColor Green
}
if ($architectureFile) {
    Write-Host "  Optional  : brd_response/$($architectureFile.Name)" -ForegroundColor Green
}
Write-Host "Output dir  : $OutputDir" -ForegroundColor Yellow
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
