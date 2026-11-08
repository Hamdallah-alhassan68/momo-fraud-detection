$ErrorActionPreference = "Stop"

function Set-ProcessSecret {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$Prompt
    )

    $secureValue = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
    try {
        $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        if ([string]::IsNullOrWhiteSpace($value)) {
            throw "$Name cannot be empty."
        }
        [Environment]::SetEnvironmentVariable($Name, $value, "Process")
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv-py313\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found at $python. Create .venv-py313 using backend\README.md first."
}

[Environment]::SetEnvironmentVariable("MTN_DISBURSEMENT_ENVIRONMENT", "production", "Process")
Write-Warning (
    "This production-only launcher uses Ghana cedis and can send real " +
    "transfers from your MTN business wallet. Verify the recipient before confirming."
)
$confirmation = Read-Host "Type REAL to continue"
if ($confirmation -cne "REAL") {
    throw "Production startup was not confirmed."
}

Set-ProcessSecret "MTN_PRODUCTION_DISBURSEMENT_SUBSCRIPTION_KEY" "MTN production Disbursement subscription key"
Set-ProcessSecret "MTN_PRODUCTION_DISBURSEMENT_API_USER" "MTN production Disbursement API User ID"
Set-ProcessSecret "MTN_PRODUCTION_DISBURSEMENT_API_KEY" "MTN production Disbursement API key"
$null = Read-Host "Copy your admin token to the clipboard, then press Enter"
$adminToken = (Get-Clipboard -Raw).Trim()
if ($adminToken.Length -lt 32) {
    throw "MOMO_ADMIN_TOKEN must be at least 32 characters; clipboard contains $($adminToken.Length) characters. Copy the full token and run the launcher again."
}
[Environment]::SetEnvironmentVariable("MOMO_ADMIN_TOKEN", $adminToken, "Process")
$adminToken = $null

Set-Location $PSScriptRoot
& $python -m uvicorn app:app --host 127.0.0.1 --port 5001
