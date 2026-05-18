<#
.SYNOPSIS
    Local test script for DE-07 Technology Selection Agent.

.DESCRIPTION
    The Technology Selection agent reads structured requirements (with NFRs and constraints)
    and agent architecture to recommend an optimal technology stack.
    
    It reads from:
    - C:\SharedFolderAdlc\<ThreadId>\bs_docs\ - Structured requirements
    - C:\SharedFolderAdlc\<ThreadId>\uploaded_files\brd\agent_architecture.json - Agent architecture
    
    It writes output to:
    - C:\SharedFolderAdlc\<ThreadId>\tech_selection_response\ (JSON + DOCX)

.USAGE
    # Step 1: Start server in a separate terminal:
    .\.venv\Scripts\python.exe run.py --host 127.0.0.1 --port 8080

    # Step 2: Run this script:
    .\test_local_de07.ps1 -ThreadId "thr-tech-test" -Port 8080
    .\test_local_de07.ps1 -ThreadId "thr-tech-test" -Port 8080 -Format "docx"
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
$BsDocsDir = Join-Path $ThreadPath "bs_docs"
$OutputDir = Join-Path $ThreadPath "tech_selection_response"
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
if (!(Test-Path $BsDocsDir)) {
    Write-Host "[ERROR] bs_docs folder not found: $BsDocsDir" -ForegroundColor Red
    Write-Host "        Create it and place structured requirements file." -ForegroundColor Red
    exit 1
}

# Get structured requirements files from bs_docs
$bsDocsFiles = Get-ChildItem -Path $BsDocsDir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in ".json",".docx",".pdf",".html",".htm" }

if ($bsDocsFiles.Count -eq 0) {
    Write-Host "[ERROR] No structured requirements files in: $BsDocsDir" -ForegroundColor Red
    Write-Host "        Supported extensions: .json .docx .pdf .html .htm" -ForegroundColor Red
    exit 1
}

# --- Check for required agent_architecture.json ---
$brdDir = Join-Path $ThreadPath "uploaded_files\brd"
$agentArchFile = Get-ChildItem -Path $brdDir -File -Filter "agent_architecture.json" -ErrorAction SilentlyContinue

if (-not $agentArchFile) {
    Write-Host "[ERROR] Required file not found: agent_architecture.json" -ForegroundColor Red
    Write-Host "        Expected in: $brdDir" -ForegroundColor Red
    Write-Host "        This file is MANDATORY for Technology Selection." -ForegroundColor Red
    exit 1
}

Write-Host "`n=== DE-07 Technology Selection Test ===" -ForegroundColor Cyan
Write-Host "Thread ID   : $ThreadId"
Write-Host "Format      : $Format"
Write-Host "Server      : $ServerUrl"
Write-Host "`nInput Files:" -ForegroundColor Yellow
Write-Host "  bs_docs/              : $($bsDocsFiles.Name -join ', ')" -ForegroundColor Green
Write-Host "  agent_architecture.json: $($agentArchFile.Name)" -ForegroundColor Green
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
Write-Host "`n--- Calling POST /agents/technology-selection?format=$Format ---" -ForegroundColor Cyan

try {
    $response = Invoke-WebRequest -Uri "$ServerUrl/agents/technology-selection?format=$Format" `
        -Method POST `
        -TimeoutSec 600 `
        -Headers @{
            "Authorization" = "Bearer $ApiKey"
            "X-Run-ID"      = "local-test-de07-001"
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
    
    # Show generated files
    Write-Host "`n--- Generated Files ---" -ForegroundColor Cyan
    Get-ChildItem $OutputDir | Format-Table Name, Length, LastWriteTime -AutoSize
    
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
