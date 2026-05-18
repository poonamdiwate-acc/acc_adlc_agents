<#
.SYNOPSIS
    Local test script for PL-05 FinOps Architect Agent.
    Reads inputs from C:\SharedFolderAdlc\<ThreadId>\finops_docs\ and writes
    the result to C:\SharedFolderAdlc\<ThreadId>\finops_architect_response\.

.USAGE
    # Step 1: Start server in a separate terminal:
    #   python run.py

    # Step 2: Run this script (creates sample input automatically):
    .\test_local_pl05.ps1 -ThreadId "thr-finops-001"
    .\test_local_pl05.ps1 -ThreadId "thr-finops-001" -Format "html"
    .\test_local_pl05.ps1 -ThreadId "thr-finops-001" -NoSample   # skip sample creation
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$ThreadId,

    [ValidateSet("json","docx","pdf","html")]
    [string]$Format = "json",

    [int]$Port = 8000,

    [switch]$NoSample
)

$ErrorActionPreference = "Stop"

# --- Config ---
$BasePath  = "C:\SharedFolderAdlc"
$InputDir  = Join-Path (Join-Path $BasePath $ThreadId) "finops_docs"
$OutputDir = Join-Path (Join-Path $BasePath $ThreadId) "finops_architect_response"
$ServerUrl = "http://127.0.0.1:$Port"
$ApiKey    = "replace-me-bearer-token-genwiz-uses"

# --- Ensure folders exist ---
if (!(Test-Path $InputDir))  { New-Item -ItemType Directory -Path $InputDir  -Force | Out-Null }
if (!(Test-Path $OutputDir)) { New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null }

# --- Create sample input file unless -NoSample ---
if (!$NoSample) {
    $sampleFile = Join-Path $InputDir "finops_input.json"
    if (!(Test-Path $sampleFile)) {
        $samplePayload = @{
            project_identity = @{
                project_name            = "Customer Portal"
                project_id              = "CP-2026-001"
                owner_name              = "Ramesh Kumar"
                owner_email             = "ramesh.kumar@company.com"
                team                    = "Digital Experience"
                cost_centre_code        = "CC-4500"
                business_unit           = "Consumer Tech"
                project_type            = "greenfield"
                criticality             = "high"
                compliance_requirements = @("SOC2", "PCI-DSS")
            }
            budget_definition = @{
                total_amount   = 1000000
                currency       = "INR"
                period         = "monthly"
                start_date     = "2026-06-01"
                end_date       = "2026-06-30"
                renewal_policy = "auto_renew"
            }
            budget_allocation_split = @{
                compute_pct          = 40
                storage_pct          = 20
                network_pct          = 15
                managed_services_pct = 20
                reserve_buffer_pct   = 5
            }
            cloud_environment = @{
                cloud_provider          = "AWS"
                account_id              = "123456789012"
                primary_region          = "ap-south-1"
                environment_tag         = "production"
                purchase_types_allowed  = @("on_demand", "spot", "reserved")
            }
        } | ConvertTo-Json -Depth 5
        # Write without BOM (PowerShell default adds BOM which breaks JSON parsers)
        [System.IO.File]::WriteAllText($sampleFile, $samplePayload, [System.Text.UTF8Encoding]::new($false))
        Write-Host "[OK] Sample input created: $sampleFile" -ForegroundColor Green
    } else {
        Write-Host "[SKIP] Sample already exists: $sampleFile" -ForegroundColor Yellow
    }
}

# --- Verify input files ---
$inputFiles = Get-ChildItem -Path $InputDir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in ".json",".docx",".pdf",".html",".htm" }

if ($inputFiles.Count -eq 0) {
    Write-Host "[ERROR] No supported input files in: $InputDir" -ForegroundColor Red
    exit 1
}

Write-Host "`n=== PL-05 FinOps Architect Local Test ===" -ForegroundColor Cyan
Write-Host "Thread ID : $ThreadId"
Write-Host "Format    : $Format"
Write-Host "Server    : $ServerUrl"
Write-Host "Input dir : $InputDir"
Write-Host "Output dir: $OutputDir"
Write-Host "Files     : $($inputFiles.Name -join ', ')"
Write-Host ""

# --- Check server ---
try {
    Invoke-RestMethod -Uri "$ServerUrl/health" -TimeoutSec 3 | Out-Null
    Write-Host "[OK] Server is running" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Server not running at $ServerUrl" -ForegroundColor Red
    Write-Host "        Start it first:  python run.py" -ForegroundColor Yellow
    exit 1
}

# --- Call the agent ---
Write-Host "`n--- Calling POST /agents/finops-architect?format=$Format ---" -ForegroundColor Cyan

try {
    $response = Invoke-WebRequest -Uri "$ServerUrl/agents/finops-architect?format=$Format" `
        -Method POST `
        -TimeoutSec 120 `
        -Headers @{
            "Authorization" = "Bearer $ApiKey"
            "X-Run-ID"      = "local-test-pl05-001"
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
    Write-Host "`n[FAIL] $($err.Exception.Message)" -ForegroundColor Red
    if ($err.Exception.Response) {
        $reader = [System.IO.StreamReader]::new($err.Exception.Response.GetResponseStream())
        $body = $reader.ReadToEnd()
        Write-Host $body -ForegroundColor Yellow
    }
    exit 1
}

Write-Host "`n[DONE] Check output at: $OutputDir" -ForegroundColor Cyan
