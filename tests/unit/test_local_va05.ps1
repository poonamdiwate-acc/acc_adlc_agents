<#
.SYNOPSIS
    Local test script for VA-05 QA Assurance Auditor Agent.
    Reads inputs from C:\SharedFolderAdlc\<ThreadId>\qa_inputs\ and writes
    the assurance sign-off + exception log to
    C:\SharedFolderAdlc\<ThreadId>\qa_assurance\.

.DESCRIPTION
    The QA Assurance Auditor reads from:
        C:\SharedFolderAdlc\<ThreadId>\qa_inputs\
            ├── compliance_audit_trail.json   (audit_trail            - required, >=1 entry)
            ├── exception_flags.json          (exception_flags        - required, may be [])
            ├── project_context.json          (project_context + business_case - required)
            └── checkpoint_expectations.json  (checkpoint_expectations - optional)

    It writes to:
        C:\SharedFolderAdlc\<ThreadId>\qa_assurance\
            ├── VA-05_assurance_signoff.json    (when format=json)
            ├── VA-05_exception_log.json        (when format=json)
            └── VA-05_output_<run_id>.{docx|pdf|html}  (when format!=json)

.USAGE
    # Step 1: Start server in a separate terminal:
    #   python run.py --host 127.0.0.1 --port 8080

    # Step 2: Run this script. By default it provisions a clean-cycle sample input set.
    .\test_local_va05.ps1 -ThreadId "thr-va05-001" -Port 8080
    .\test_local_va05.ps1 -ThreadId "thr-va05-001" -Port 8080 -Format "html"
    .\test_local_va05.ps1 -ThreadId "thr-va05-001" -Port 8080 -NoSample   # use whatever is already in qa_inputs/
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
$BasePath   = "C:\SharedFolderAdlc"
$ThreadPath = Join-Path $BasePath $ThreadId
$InputDir   = Join-Path $ThreadPath "qa_inputs"
$OutputDir  = Join-Path $ThreadPath "qa_assurance"
$ServerUrl  = "http://127.0.0.1:$Port"
$ApiKey     = "replace-me-bearer-token-genwiz-uses"
$RunId      = "local-test-va05-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

# --- Ensure folders exist ---
if (!(Test-Path $InputDir))  { New-Item -ItemType Directory -Path $InputDir  -Force | Out-Null }
if (!(Test-Path $OutputDir)) { New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null }

# --- Provision sample input files (clean cycle) unless -NoSample ---
if (!$NoSample) {
    $auditFile = Join-Path $InputDir "compliance_audit_trail.json"
    if (!(Test-Path $auditFile)) {
        $auditPayload = @{
            audit_trail = @(
                @{
                    entry_id      = "AUD-2026-05-12-001"
                    timestamp     = "2026-05-12T08:00:00Z"
                    control_ref   = "CTRL-SEC-001"
                    actor         = "compliance-agent"
                    signature     = "sig:9b2f4e1c7a3d5b6e8f0a1c2d3e4f5061"
                    action        = "control_executed"
                    outcome       = "pass"
                    evidence_refs = @("EVID-SEC-001-A")
                },
                @{
                    entry_id      = "AUD-2026-05-12-002"
                    timestamp     = "2026-05-12T09:15:00Z"
                    control_ref   = "CTRL-SEC-002"
                    actor         = "compliance-agent"
                    signature     = "sig:1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d"
                    action        = "control_executed"
                    outcome       = "pass"
                    evidence_refs = @("EVID-SEC-002-A", "EVID-SEC-002-B")
                },
                @{
                    entry_id      = "AUD-2026-05-12-003"
                    timestamp     = "2026-05-12T10:30:00Z"
                    control_ref   = "CTRL-DATA-001"
                    actor         = "compliance-agent"
                    signature     = "sig:5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"
                    action        = "control_executed"
                    outcome       = "pass_with_exception"
                    evidence_refs = @("EVID-DATA-001-A")
                },
                @{
                    entry_id      = "AUD-2026-05-12-004"
                    timestamp     = "2026-05-12T13:00:00Z"
                    control_ref   = "CTRL-QE-001"
                    actor         = "quality-engineering-agent"
                    signature     = "sig:3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d"
                    action        = "test_suite_executed"
                    outcome       = "pass_with_exception"
                    evidence_refs = @("EVID-QE-001-A")
                }
            )
        }
        $auditPayload | ConvertTo-Json -Depth 10 | Set-Content -Path $auditFile -Encoding UTF8
        Write-Host "[OK] Created sample $auditFile" -ForegroundColor Green
    }

    $exceptionsFile = Join-Path $InputDir "exception_flags.json"
    if (!(Test-Path $exceptionsFile)) {
        $exceptionsPayload = @{
            exception_flags = @(
                @{
                    exception_id     = "EXC-001"
                    severity         = "medium"
                    source_control   = "CTRL-DATA-001"
                    audit_trail_ref  = "AUD-2026-05-12-003"
                    evidence_refs    = @("EVID-DATA-001-A")
                    description      = "Data freshness SLA exceeded by 4 minutes. Within documented 10-minute tolerance."
                    within_tolerance = $true
                },
                @{
                    exception_id     = "EXC-002"
                    severity         = "low"
                    source_control   = "CTRL-QE-001"
                    audit_trail_ref  = "AUD-2026-05-12-004"
                    evidence_refs    = @("EVID-QE-001-A")
                    description      = "Non-critical test flaked on first run, passed on retry."
                    within_tolerance = $true
                }
            )
        }
        $exceptionsPayload | ConvertTo-Json -Depth 10 | Set-Content -Path $exceptionsFile -Encoding UTF8
        Write-Host "[OK] Created sample $exceptionsFile" -ForegroundColor Green
    }

    $checkpointsFile = Join-Path $InputDir "checkpoint_expectations.json"
    if (!(Test-Path $checkpointsFile)) {
        $checkpointsPayload = @{
            checkpoint_expectations = @{
                cycle_id    = "CYCLE-2026-05"
                issued_by   = "validate-orchestrator"
                checkpoints = @(
                    @{ control_ref = "CTRL-SEC-001";  description = "Authentication boundary verified"; required = $true },
                    @{ control_ref = "CTRL-SEC-002";  description = "Authorization policy applied";    required = $true },
                    @{ control_ref = "CTRL-DATA-001"; description = "Data freshness within SLA";       required = $true },
                    @{ control_ref = "CTRL-QE-001";   description = "Functional test suite executed";  required = $true }
                )
            }
        }
        $checkpointsPayload | ConvertTo-Json -Depth 10 | Set-Content -Path $checkpointsFile -Encoding UTF8
        Write-Host "[OK] Created sample $checkpointsFile" -ForegroundColor Green
    }

    $contextFile = Join-Path $InputDir "project_context.json"
    if (!(Test-Path $contextFile)) {
        $contextPayload = @{
            project_context = @{
                cycle_id          = "CYCLE-2026-05"
                domain            = "payments"
                market            = "EU"
                project_name      = "Real-time Settlement Reconciliation"
                sla_seconds       = 600
                orchestrator      = "validate-orchestrator"
                phase             = "validate"
                cycle_started_at  = "2026-05-12T07:30:00Z"
            }
            business_case = "Replace the legacy nightly reconciliation job with a real-time stream so settlement breaks are surfaced within 10 minutes of trade execution. The Validate phase must produce an immutable assurance sign-off proving that all security, data-quality, and latency controls were exercised in this cycle and that every raised exception was dispositioned with attributable evidence before the release is cleared for production."
        }
        $contextPayload | ConvertTo-Json -Depth 10 | Set-Content -Path $contextFile -Encoding UTF8
        Write-Host "[OK] Created sample $contextFile" -ForegroundColor Green
    }
}

# --- Verify required input files exist ---
$inputFiles = Get-ChildItem -Path $InputDir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in ".json",".docx",".pdf",".html",".htm" }

if ($inputFiles.Count -eq 0) {
    Write-Host "[ERROR] No supported input files in: $InputDir" -ForegroundColor Red
    Write-Host "        Supported extensions: .json .docx .pdf .html .htm" -ForegroundColor Red
    Write-Host "        Re-run without -NoSample to provision a clean-cycle sample." -ForegroundColor Yellow
    exit 1
}

Write-Host "`n=== VA-05 QA Assurance Auditor Local Test ===" -ForegroundColor Cyan
Write-Host "Thread ID   : $ThreadId"
Write-Host "Run ID      : $RunId"
Write-Host "Format      : $Format"
Write-Host "Server      : $ServerUrl"
Write-Host "Input dir   : $InputDir" -ForegroundColor Yellow
Write-Host "  Files     : $($inputFiles.Name -join ', ')" -ForegroundColor Green
Write-Host "Output dir  : $OutputDir" -ForegroundColor Yellow
Write-Host ""

# --- Check if server is running ---
try {
    Invoke-RestMethod -Uri "$ServerUrl/health" -TimeoutSec 3 | Out-Null
    Write-Host "[OK] Server is running" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Server not running at $ServerUrl" -ForegroundColor Red
    Write-Host "        Start it first:  python run.py --host 127.0.0.1 --port $Port" -ForegroundColor Yellow
    exit 1
}

# --- Call the agent ---
Write-Host "`n--- Calling POST /agents/qa-assurance-auditor?format=$Format ---" -ForegroundColor Cyan

try {
    $response = Invoke-WebRequest -Uri "$ServerUrl/agents/qa-assurance-auditor?format=$Format" `
        -Method POST `
        -TimeoutSec 600 `
        -Headers @{
            "Authorization" = "Bearer $ApiKey"
            "X-Run-ID"      = $RunId
            "X-Thread-ID"   = $ThreadId
        } `
        -ContentType "application/json" `
        -Body "{}" `
        -UseBasicParsing

    Write-Host "`n[OK] Status: $($response.StatusCode)" -ForegroundColor Green

    if ($Format -eq "json") {
        $resultObj = $response.Content | ConvertFrom-Json
        $resultObj | ConvertTo-Json -Depth 10 | Write-Host

        Write-Host "`n--- Assurance summary ---" -ForegroundColor Cyan
        $summary = $resultObj.assurance_summary
        Write-Host ("  overall_assurance     : {0}" -f $summary.overall_assurance)
        Write-Host ("  recommendation        : {0}" -f $summary.recommendation)
        Write-Host ("  audit_entries_reviewed: {0}" -f $summary.audit_entries_reviewed)
        Write-Host ("  audit_findings_raised : {0}" -f $summary.audit_findings_raised)
        Write-Host ("  exceptions_reviewed   : {0}" -f $summary.exceptions_reviewed)
        if ($resultObj.assurance_signoff) {
            Write-Host ("  signoff_id            : {0}" -f $resultObj.assurance_signoff.signoff_id) -ForegroundColor Green
            Write-Host ("  immutable             : {0}" -f $resultObj.assurance_signoff.immutable) -ForegroundColor Green
        } else {
            Write-Host "  signoff               : NOT ISSUED (blocked)" -ForegroundColor Red
        }
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
