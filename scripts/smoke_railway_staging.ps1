[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [uri] $BaseUri,

    [Parameter(Mandatory = $true)]
    [string] $ExpectedRegion,

    [switch] $ConfirmStaging
)

$ErrorActionPreference = "Stop"

if (-not $ConfirmStaging) {
    throw "Pass -ConfirmStaging only after verifying this URL belongs to the isolated Railway staging environment."
}
if ($BaseUri.Scheme -ne "https") {
    throw "Staging smoke checks require an HTTPS URL."
}

$base = $BaseUri.AbsoluteUri.TrimEnd("/")
$ready = $false
for ($attempt = 1; $attempt -le 8; $attempt++) {
    try {
        $readyResponse = Invoke-RestMethod -Method Get -Uri "$base/ready" -TimeoutSec 10
        if ($readyResponse.status -eq "ready") {
            $ready = $true
            break
        }
    }
    catch {
        # Railway's free service may need time to wake. Do not print response bodies
        # or request headers while retrying.
    }
    if ($attempt -lt 8) {
        Start-Sleep -Seconds 5
    }
}
if (-not $ready) {
    throw "Staging /ready did not become ready within 120 seconds."
}
Write-Output "PASS /ready"

$runtime = Invoke-RestMethod -Method Get -Uri "$base/health/runtime" -TimeoutSec 15
if ($runtime.tenant_region_routing_enabled -ne $false) {
    throw "Staging must keep TENANT_REGION_ROUTING_ENABLED=false."
}
if ($runtime.deployment_region_id -ne $ExpectedRegion -or
    $runtime.database_region_id -ne $ExpectedRegion) {
    throw "Runtime deployment/database region does not match the expected staging region."
}
Write-Output "PASS /health/runtime: routing disabled; both regions match $ExpectedRegion"

$components = Invoke-RestMethod -Method Get -Uri "$base/health/components" -TimeoutSec 20
if ($components.status -ne "healthy" -or
    $components.components.database -ne "healthy" -or
    $components.components.worker -ne "healthy") {
    throw "Staging database or worker health check failed."
}
if ($components.components.providers -ne "not_probed") {
    throw "Expected provider health to remain not_probed during provider-free staging checks."
}
Write-Output "PASS /health/components: database and worker healthy; providers not_probed"
