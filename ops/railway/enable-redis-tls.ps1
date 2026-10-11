# Requires PowerShell 7.4+ (.NET 8), Railway CLI login, and the project's Python venv.
# Secret values flow through stdin and memory, never command arguments or files.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ProjectId,
    [Parameter(Mandatory)][string]$EnvironmentId,
    [Parameter(Mandatory)][string]$RedisServiceId,
    [Parameter(Mandatory)][string[]]$ClientServiceIds,
    [string]$RailwayCommand = 'railway',
    [string]$PythonCommand = (Join-Path $PSScriptRoot '../../.venv/Scripts/python.exe'),
    [ValidateRange(7,365)][int]$ValidityDays = 90,
    [switch]$ClientUsePublicEndpoint,
    [switch]$Plan,
    [switch]$Rotate
)
$ErrorActionPreference = 'Stop'

function Invoke-RailwayJson([string[]]$Arguments) {
    $result = & $RailwayCommand @Arguments 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Railway command failed; raw output suppressed to protect credentials.' }
    return (($result -join "`n") | ConvertFrom-Json -AsHashtable)
}

function Invoke-GraphQL([string]$Query, [hashtable]$Variables) {
    # --variables @- keeps private keys and URLs out of the process command line.
    $result = ($Variables | ConvertTo-Json -Depth 20 -Compress) |
        & $RailwayCommand api $Query --variables '@-' --compact 2>$null
    $apiExit = $LASTEXITCODE
    if ($apiExit -ne 0) {
        try {
            $failed = ($result -join "`n") | ConvertFrom-Json -AsHashtable
            $message = [string]$failed.errors[0].message
            $message = $message -replace '[a-z]+://\S+','<url>' -replace '"[^"\r\n]*"','<quoted value>' -replace '[A-Za-z0-9+/=_-]{32,}','<opaque value>'
            Write-Warning ('Railway API error: ' + $message.Substring(0,[Math]::Min(300,$message.Length)))
        } catch { Write-Warning 'Railway API returned no readable error response.' }
        throw 'Railway API request failed; raw output suppressed to protect credentials.'
    }
    $response = ($result -join "`n") | ConvertFrom-Json -AsHashtable
    if ($response.errors) { throw 'Railway API returned errors; raw output suppressed to protect credentials.' }
    return $response
}

function Set-ServiceVariables([string]$ServiceId, [hashtable]$Values) {
    $query = 'mutation($input: VariableCollectionUpsertInput!) { variableCollectionUpsert(input: $input) }'
    Invoke-GraphQL $query @{input=@{
        projectId=$ProjectId; environmentId=$EnvironmentId; serviceId=$ServiceId
        variables=$Values; skipDeploys=$true; replace=$false
    }} | Out-Null
}

function Set-RedisCommand([string]$Command) {
    $query = 'mutation($service: String!, $environment: String!, $input: ServiceInstanceUpdateInput!) { serviceInstanceUpdate(serviceId: $service, environmentId: $environment, input: $input) }'
    Invoke-GraphQL $query @{service=$RedisServiceId;environment=$EnvironmentId;input=@{
        startCommand=$Command; sleepApplication=$redisSleepEnabled
    }} | Out-Null
}

function Deploy-Service([string]$ServiceId, [string]$Commit) {
    $query = 'mutation($service: String!, $environment: String!, $commit: String) { serviceInstanceDeploy(serviceId: $service, environmentId: $environment, commitSha: $commit, latestCommit: false) }'
    Invoke-GraphQL $query @{service=$ServiceId;environment=$EnvironmentId;commit=$Commit} | Out-Null
}

function Redeploy-Client([hashtable]$Instance) {
    $query = 'mutation($id: String!) { deploymentRedeploy(id: $id, usePreviousImageTag: true) { id status } }'
    $result = Invoke-GraphQL $query @{id=$Instance.activeDeployments[0].id}
    $deploymentId = $result.data.deploymentRedeploy.id
    if (-not $deploymentId) { throw 'Client redeploy returned no deployment ID.' }
    for ($attempt=0; $attempt -lt 60; $attempt++) {
        $deployments = Invoke-RailwayJson @('deployment','list','--project',$ProjectId,'--environment',$EnvironmentId,'--service',$Instance.serviceId,'--limit','5','--json')
        $deployment = @($deployments | Where-Object {$_.id -eq $deploymentId})[0]
        if ($deployment.status -in @('SUCCESS','SLEEPING')) { return }
        if ($deployment.status -in @('FAILED','CRASHED','REMOVED')) { throw 'Client redeploy failed.' }
        Start-Sleep -Seconds 5
    }
    throw 'Client redeploy did not become ready within the deployment window.'
}

function Read-Variables([string]$ServiceId) {
    return Invoke-RailwayJson @('variable','list','--project',$ProjectId,'--environment',$EnvironmentId,'--service',$ServiceId,'--json')
}

function Test-RedisTls([string]$Url) {
    $check = @'
import json, ssl, sys
import redis
try:
    with redis.Redis.from_url(sys.stdin.read().strip(), socket_connect_timeout=5, socket_timeout=5) as client:
        if not client.ping():
            raise RuntimeError()
except Exception as error:
    reason = type(error).__name__
    while error:
        if isinstance(error, ssl.SSLCertVerificationError):
            reason = error.verify_message
        error = error.__cause__ or error.__context__
    print(json.dumps({'reason': reason}))
    sys.exit(1)
'@
    $probe = $Url | & $PythonCommand -c $check 2>$null
    $script:LastTlsProbeReason = if ($probe) { ($probe | ConvertFrom-Json).reason } else { $null }
    return $LASTEXITCODE -eq 0
}

$project = Invoke-RailwayJson @('status','--project',$ProjectId,'--environment',$EnvironmentId,'--json')
if (@($project.environments.edges).Count -ne 1) {
    throw 'This rollout requires a single-environment project: Railway service updates can affect other non-fork environments.'
}
$environment = @($project.environments.edges | Where-Object {$_.node.id -eq $EnvironmentId})[0].node
if (-not $environment) { throw 'Requested environment was not found in this project.' }
$instances = @($environment.serviceInstances.edges | ForEach-Object {$_.node})
$server = @($instances | Where-Object {$_.serviceId -eq $RedisServiceId})[0]
if (-not $server -or $server.source.image -notmatch '^redis:') { throw 'Redis service must use the official redis image.' }
$redisSleepEnabled = if ($server.Contains('sleepApplication')) {
    [bool]$server.sleepApplication
} else {
    [bool]$server.activeDeployments[0].meta.serviceManifest.deploy.sleepApplication
}
if ($ClientServiceIds -contains $RedisServiceId) { throw 'Redis cannot be one of its own client services.' }
if (($ClientServiceIds | Select-Object -Unique).Count -ne $ClientServiceIds.Count) { throw 'Client service IDs must be unique.' }
$clientInstances = @{}
$clientVariables = @{}
foreach ($id in $ClientServiceIds) {
    $instance = @($instances | Where-Object {$_.serviceId -eq $id})[0]
    if (-not $instance) { throw 'A client service was not found in the requested environment.' }
    if (-not $instance.activeDeployments[0].id) { throw 'A client has no active image to redeploy.' }
    $clientInstances[$id] = $instance
    $clientVariables[$id] = Read-Variables $id
    if ($clientVariables[$id]['REDIS_URL_FILE']) {
        throw 'A client already uses REDIS_URL_FILE; update its protected secret mount instead of overriding REDIS_URL.'
    }
    foreach ($name in $clientVariables[$id].Keys) {
        if ($name -ieq 'DATABASE_URL' -and [string]$clientVariables[$id][$name] -notmatch '^postgresql(?:\+[a-z0-9]+)?://[^/]+/[^?]+') {
            throw 'A client has an invalid staged DATABASE_URL. Restore its working setting before redeploying.'
        }
    }
}
$serverVariables = Read-Variables $RedisServiceId
if ($serverVariables.Contains('REDIS_TLS_KEY_B64') -and -not $Rotate) {
    throw 'TLS is already configured. Use -Rotate to replace the certificate and client trust together.'
}
$privateHost = [string]$serverVariables['RAILWAY_PRIVATE_DOMAIN']
$publicHost = [string]$serverVariables['RAILWAY_TCP_PROXY_DOMAIN']
$publicPort = [int]$serverVariables['RAILWAY_TCP_PROXY_PORT']
$password = [string]$serverVariables['REDIS_PASSWORD']
if (-not $password) { $password = [string]$serverVariables['REDISPASSWORD'] }
if (-not $password -or -not $privateHost -or -not $publicHost -or -not $publicPort) {
    throw 'Redis password, private DNS, and an existing public TCP proxy are required for verified preflight checks.'
}
if (-not $serverVariables['RAILWAY_VOLUME_MOUNT_PATH']) { throw 'Redis must have an existing data volume.' }
if ($password -match '[\r\n]') { throw 'Redis password must not contain line breaks.' }
if (-not (Test-Path -LiteralPath $PythonCommand)) { throw 'Python venv is required for verified Redis PING.' }

$now = [DateTimeOffset]::UtcNow
$expires = $now.AddDays($ValidityDays)
$caKey = [System.Security.Cryptography.RSA]::Create(3072)
$serverKey = [System.Security.Cryptography.RSA]::Create(3072)
try {
    $caRequest = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new(
        'CN=Agent Platform Redis TLS CA', $caKey,
        [System.Security.Cryptography.HashAlgorithmName]::SHA256,
        [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
    $caRequest.CertificateExtensions.Add([System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($true,$false,0,$true))
    $caRequest.CertificateExtensions.Add([System.Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new(
        [System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::KeyCertSign,$true))
    $caRequest.CertificateExtensions.Add([System.Security.Cryptography.X509Certificates.X509SubjectKeyIdentifierExtension]::new($caRequest.PublicKey,$false))
    $ca = $caRequest.CreateSelfSigned($now.AddMinutes(-5),$expires.AddDays(1))
    $request = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new(
        "CN=$privateHost", $serverKey,
        [System.Security.Cryptography.HashAlgorithmName]::SHA256,
        [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
    $request.CertificateExtensions.Add([System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($false,$false,0,$true))
    $request.CertificateExtensions.Add([System.Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new(
        ([System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature -bor
         [System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::KeyEncipherment),$true))
    $request.CertificateExtensions.Add([System.Security.Cryptography.X509Certificates.X509SubjectKeyIdentifierExtension]::new($request.PublicKey,$false))
    $caSki = [Convert]::FromHexString($ca.Extensions['2.5.29.14'].SubjectKeyIdentifier)
    # AuthorityKeyIdentifier ::= SEQUENCE { keyIdentifier [0] OCTET STRING }.
    $aki = [byte[]](@(0x30, (2+$caSki.Length), 0x80, $caSki.Length) + $caSki)
    $request.CertificateExtensions.Add([System.Security.Cryptography.X509Certificates.X509Extension]::new('2.5.29.35',$aki,$false))
    $usage = [System.Security.Cryptography.OidCollection]::new()
    $usage.Add([System.Security.Cryptography.Oid]::new('1.3.6.1.5.5.7.3.1')) | Out-Null
    $request.CertificateExtensions.Add([System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]::new($usage,$true))
    $san = [System.Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
    $san.AddDnsName($privateHost)
    $san.AddDnsName($publicHost)
    $request.CertificateExtensions.Add($san.Build())
    $serial = [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(16)
    $certificate = $request.Create($ca,$now.AddMinutes(-5),$expires,$serial)
    $caPem = $ca.ExportCertificatePem()
    $serverPem = $certificate.ExportCertificatePem()
    $privateKeyPem = $serverKey.ExportPkcs8PrivateKeyPem()
} finally {
    $caKey.Dispose()
    $serverKey.Dispose()
}
$queryString = 'ssl_cert_reqs=required&ssl_check_hostname=true&ssl_ca_data=' + [uri]::EscapeDataString($caPem)
$userInfo = 'default:' + [uri]::EscapeDataString($password)
$privateUrl = "rediss://${userInfo}@${privateHost}:6379/0?${queryString}"
$publicUrl = "rediss://${userInfo}@${publicHost}:${publicPort}/0?${queryString}"
$script = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'redis-tls-start.sh') -Raw).Replace("`r`n","`n")
$serverValues = [Collections.Hashtable]::new([StringComparer]::Ordinal)
$initialValues = @{
    REDIS_TLS_CERT_B64=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($serverPem))
    REDIS_TLS_KEY_B64=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($privateKeyPem))
    REDIS_TLS_CA_CERT_B64=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($caPem))
    REDIS_TLS_START_SCRIPT_B64=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($script))
    REDIS_TLS_CERT_EXPIRES_AT=$expires.ToString('o')
    REDIS_URL=$privateUrl
    REDIS_PUBLIC_URL=$publicUrl
    REDIS_PASSWORD=$password
}
foreach ($entry in $initialValues.GetEnumerator()) {$serverValues[$entry.Key]=$entry.Value}
# Preserve existing differently cased aliases, giving all aliases the same TLS URL.
foreach ($name in $serverVariables.Keys) {
    if ($name -ieq 'REDIS_URL' -and $name -cne 'REDIS_URL') { $serverValues[$name]=$privateUrl }
}
$startCommand = 'sh -c ''umask 077; p=$(mktemp); printf "%s" "$REDIS_TLS_START_SCRIPT_B64" | base64 -d > "$p"; exec sh "$p"'''
if ($Plan) {
    [pscustomobject]@{
        Project=$project.name; Environment=$environment.name; Redis=$server.serviceName
        ClientCount=$ClientServiceIds.Count; TLSOnlyPort=6379; CertificateExpires=$expires
        CertificateVerification='required'; HostnameVerification=$true; SecretFilesWritten=$false
        ClientEndpoint=if ($ClientUsePublicEndpoint) {'Existing TLS TCP proxy'} else {'Private network'}
    }
    return
}

$serverChanged = $false
$clientsChanged = @()
try {
    Set-ServiceVariables $RedisServiceId $serverValues
    $serverChanged = $true
    Write-Output 'TLS certificate and connection variables staged; Redis has not been restarted yet.'
    Set-RedisCommand $startCommand
    Deploy-Service $RedisServiceId $null
    Write-Output 'Redis TLS deployment requested. Waiting for certificate-verified PING.'
    $ready = $false
    for ($attempt=0; $attempt -lt 60; $attempt++) {
        if (Test-RedisTls $publicUrl) { $ready=$true; break }
        if ($attempt -eq 0 -or $attempt -eq 10) { Write-Output ('TLS probe is waiting: ' + $script:LastTlsProbeReason) }
        Start-Sleep -Seconds 5
    }
    if (-not $ready) { throw 'Redis TLS PING did not succeed within the deployment window.' }
    Write-Output 'Certificate-verified Redis TLS PING succeeded.'
    foreach ($id in $ClientServiceIds) {
        $targetVariable = if ($ClientUsePublicEndpoint) {'REDIS_PUBLIC_URL'} else {'REDIS_URL'}
        $reference = '${{'+$server.serviceName+'.'+$targetVariable+'}}'
        $values = [Collections.Hashtable]::new([StringComparer]::Ordinal)
        $values['REDIS_URL']=$reference
        foreach ($name in $clientVariables[$id].Keys) {
            if ($name -ieq 'REDIS_URL' -and $name -cne 'REDIS_URL') { $values[$name]=$reference }
        }
        Set-ServiceVariables $id $values
        $clientsChanged += $id
        Redeploy-Client $clientInstances[$id]
    }
    Write-Output ('Redis TLS enabled; client images redeployed. Certificate expires ' + $expires.ToString('u'))
} catch {
    # Restore old connection variables and command. The data volume is never replaced.
    Write-Warning 'TLS rollout failed; restoring the previous service configuration.'
    $rollbackComplete=$true
    foreach ($id in $clientsChanged) {
        $restore = [Collections.Hashtable]::new([StringComparer]::Ordinal)
        foreach ($name in $clientVariables[$id].Keys) {
            if ($name -ieq 'REDIS_URL') {$restore[$name]=$clientVariables[$id][$name]}
        }
        try {
            Set-ServiceVariables $id $restore
            Redeploy-Client $clientInstances[$id]
        } catch { $rollbackComplete=$false; Write-Warning 'A client rollback failed; verify its deployment status.' }
    }
    if ($serverChanged) {
        $restore = [Collections.Hashtable]::new([StringComparer]::Ordinal)
        foreach ($name in $serverVariables.Keys) {
            if ($name -ieq 'REDIS_URL' -or $name -eq 'REDIS_PUBLIC_URL' -or $name -like 'REDIS_TLS_*') {
                $restore[$name]=$serverVariables[$name]
            }
        }
        try {
            Set-ServiceVariables $RedisServiceId $restore
            Set-RedisCommand $server.startCommand
            Deploy-Service $RedisServiceId $null
        } catch { $rollbackComplete=$false; Write-Warning 'Redis rollback failed; verify its startup configuration.' }
    }
    if (-not $rollbackComplete) { throw 'Redis TLS rollout failed and rollback was incomplete. Inspect service status; secret values were suppressed.' }
    throw 'Redis TLS rollout failed; rollback deployments requested. New unused TLS variables may remain. Inspect status and redacted logs before retrying with -Rotate.'
}
