<#
.SYNOPSIS
    Local test script for VA-04 Compliance Agent.
    Only parameter needed: thread ID (folder must already exist with input files).

.USAGE
    # Step 1: Copy .env.example to .env and fill in real credentials:
    #   copy .env.example .env

    # Step 2: Start server in a separate terminal:
    #   .\.venv\Scripts\python.exe run.py

    # Step 3: Run this script:
    .\test_local_va04.ps1 -ThreadId "thread-va04-test"
    .\test_local_va04.ps1 -ThreadId "thread-va04-test" -Format "html"
    .\test_local_va04.ps1 -ThreadId "thread-va04-test" -SetupSample   # creates sample input first
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
$InputDir   = Join-Path (Join-Path $BasePath $ThreadId) "build_output"
$OutputDir  = Join-Path (Join-Path $BasePath $ThreadId) "compliance_response"
$ServerUrl  = "http://localhost:8000"
$ApiKey     = "adlc-local-dev-key-001"

# --- Create folder structure ---
if (!(Test-Path $InputDir))  { New-Item -ItemType Directory -Path $InputDir  -Force | Out-Null }
if (!(Test-Path $OutputDir)) { New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null }

# --- Optionally create a sample JSON input ---
if ($SetupSample) {
    $sampleFile = Join-Path $InputDir "release_input.json"
    if (!(Test-Path $sampleFile)) {
        @{
            release_artefacts = @(
                @{
                    artefact_id   = "SVC-001"
                    type          = "code_change"
                    description   = "Payment service v2.1 - adds multi-currency support"
                    changed_files = @("PaymentController.java", "CurrencyService.java")
                    data_fields   = @("amount", "currency_code", "account_id")
                },
                @{
                    artefact_id     = "DB-MIG-007"
                    type            = "database_migration"
                    description     = "Schema migration - adds currency_code and exchange_rate to transactions table"
                    changed_tables  = @("transactions")
                    data_fields     = @("currency_code", "exchange_rate", "user_id", "email")
                }
            )
            policy_rules = @(
                @{
                    rule_id     = "POL-TLS-01"
                    name        = "Transport Security"
                    requirement = "All services must enforce TLS 1.2 minimum on all inbound and outbound connections."
                },
                @{
                    rule_id     = "POL-PII-02"
                    name        = "PII Data Handling"
                    requirement = "All artefacts that process or store PII fields (email, user_id, account_id) must apply AES-256 encryption at rest."
                },
                @{
                    rule_id     = "POL-LOG-03"
                    name        = "Audit Logging"
                    requirement = "All payment and financial transaction services must log every transaction with transaction_id, timestamp, amount, and status."
                },
                @{
                    rule_id     = "POL-MIG-04"
                    name        = "Migration Rollback"
                    requirement = "All database migration scripts must include a reversible rollback procedure."
                }
            )
            project_context = @{
                squad        = "payments"
                domain       = "fintech"
                market       = "UK"
                project_name = "PayCore v2"
            }
            business_case = "Extend the PayCore payment platform to support multi-currency transactions for UK and EU markets, meeting PCI-DSS and GDPR requirements for data handling of PII fields."
            constraints   = @{
                regulatory_scope = @("PCI-DSS", "GDPR")
                data_residency   = "EU"
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

Write-Host "`n=== VA-04 Compliance Agent - Local Test ===" -ForegroundColor Cyan
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
$RunId = "va04-local-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Write-Host "`n--- Calling POST /agents/compliance?format=$Format ---" -ForegroundColor Cyan
Write-Host "Run ID    : $RunId"
Write-Host ""

try {
    $response = Invoke-WebRequest -Uri "$ServerUrl/agents/compliance?format=$Format" `
        -Method POST `
        -TimeoutSec 300 `
        -Headers @{
            "Authorization" = "Bearer $ApiKey"
            "X-Run-ID"      = $RunId
            "X-Thread-ID"   = $ThreadId
        } `
        -ContentType "application/json" `
        -Body "{}" `
        -UseBasicParsing

    Write-Host "[OK] Status: $($response.StatusCode)" -ForegroundColor Green

    if ($Format -eq "json") {
        $result = $response.Content | ConvertFrom-Json

        # --- Summary ---
        Write-Host "`n=== POLICY SIGN-OFF ===" -ForegroundColor Yellow
        $signoff = $result.policy_signoff
        Write-Host "  Recommendation   : $($signoff.recommendation)" -ForegroundColor $(if ($signoff.recommendation -eq 'proceed') { 'Green' } elseif ($signoff.recommendation -eq 'remediate') { 'Yellow' } else { 'Red' })
        Write-Host "  Overall Status   : $($signoff.overall_status)"
        Write-Host "  Signoff Authority: $($signoff.signoff_authority)"
        Write-Host "  Total Checks     : $($signoff.total_checks)"
        Write-Host "  Compliant        : $($signoff.compliant_count)"
        Write-Host "  Non-Compliant    : $($signoff.non_compliant_count)"

        Write-Host "`n=== AUDIT TRAIL ($($result.compliance_audit_trail.Count) checks) ===" -ForegroundColor Yellow
        foreach ($check in $result.compliance_audit_trail) {
            $color = switch ($check.status) {
                "compliant"               { "Green" }
                "non_compliant"           { "Red" }
                "conditionally_compliant" { "Yellow" }
                default                   { "Gray" }
            }
            $checkId    = if ($null -ne $check.check_id)    { $check.check_id }    else { "N/A" }
            $checkName  = if ($null -ne $check.check_name)  { $check.check_name }  else { "" }
            $artefact   = if ($null -ne $check.artefact_ref){ $check.artefact_ref } else { "N/A" }
            $policy     = if ($null -ne $check.policy_ref)  { $check.policy_ref }  else { "N/A" }
            $status     = if ($null -ne $check.status)      { $check.status }      else { "N/A" }
            $evidenceRaw = if ($null -ne $check.evidence)   { $check.evidence }    else { "(no evidence)" }
            $evidence   = $evidenceRaw.Substring(0, [Math]::Min(120, $evidenceRaw.Length))

            Write-Host "  [$checkId] $checkName" -ForegroundColor $color
            Write-Host "    Artefact : $artefact  |  Policy: $policy"
            Write-Host "    Status   : $status" -ForegroundColor $color
            if ($check.policy_violation) {
                Write-Host "    [!] POLICY VIOLATION FLAGGED" -ForegroundColor Red
            }
            Write-Host "    Evidence : $evidence..."
            Write-Host ""
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
Write-Host "`n--- Output folder: $OutputDir ---" -ForegroundColor Cyan
if (Test-Path $OutputDir) {
    $files = Get-ChildItem $OutputDir -ErrorAction SilentlyContinue
    if ($files) {
        $files | Format-Table Name, Length, LastWriteTime -AutoSize
    } else {
        Write-Host "(empty - output not yet written)" -ForegroundColor Gray
    }
} else {
    Write-Host "(folder not created yet)" -ForegroundColor Gray
}
