#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Install OrderFerry as an interactive Windows Scheduled Task.

.DESCRIPTION
    Synchronizes the locked Python environment, configures a narrowly scoped
    firewall rule when remote access is explicitly requested, registers the
    task for the current interactive user, starts it, and verifies the protocol.

.PARAMETER Port
    TCP listen port. Defaults to 18812.

.PARAMETER BindAddress
    Address to bind. Defaults to loopback. Use 0.0.0.0 only on a trusted host.

.PARAMETER RemoteAddress
    Windows Firewall remote-address scope used for non-loopback binds.
    Defaults to LocalSubnet. Prefer a narrower host or CIDR where possible.

.PARAMETER TaskName
    Scheduled Task and firewall-rule name. Defaults to OrderFerry.

.EXAMPLE
    .\setup-host.ps1

.EXAMPLE
    .\setup-host.ps1 -BindAddress 0.0.0.0 -RemoteAddress 172.16.0.0/12

.EXAMPLE
    .\setup-host.ps1 -Uninstall
#>

param(
    [ValidateRange(1, 65535)]
    [int]$Port = 18812,

    [ValidateNotNullOrEmpty()]
    [string]$BindAddress = "127.0.0.1",

    [ValidateNotNullOrEmpty()]
    [string]$RemoteAddress = "LocalSubnet",

    [ValidateNotNullOrEmpty()]
    [string]$TaskName = "OrderFerry",

    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$LogDir = Join-Path $ScriptDir "logs"
$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
$TaskUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name

function Write-Step { param([string]$Message) Write-Host "`n> $Message" -ForegroundColor Cyan }
function Write-OK { param([string]$Message) Write-Host "  [OK] $Message" -ForegroundColor Green }
function Write-Skip { param([string]$Message) Write-Host "  [--] $Message" -ForegroundColor DarkGray }
function Write-Warn { param([string]$Message) Write-Host "  [!!] $Message" -ForegroundColor Yellow }

function Wait-TaskState {
    param(
        [Parameter(Mandatory)] [string]$Name,
        [Parameter(Mandatory)] [string]$DesiredState,
        [int]$TimeoutSeconds = 10
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $task = Get-ScheduledTask -TaskName $Name
        if ($task.State -eq $DesiredState) {
            return $task
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)

    throw "Scheduled task $Name did not reach state $DesiredState within $TimeoutSeconds seconds"
}

function Test-OrderFerryProtocol {
    param([string]$HostName, [int]$HostPort)

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $client.Connect($HostName, $HostPort)
        $stream = $client.GetStream()
        $writer = [System.IO.StreamWriter]::new(
            $stream,
            [System.Text.UTF8Encoding]::new($false),
            1024,
            $true
        )
        $writer.NewLine = "`n"
        $writer.AutoFlush = $true
        $reader = [System.IO.StreamReader]::new(
            $stream,
            [System.Text.Encoding]::UTF8,
            $false,
            1024,
            $true
        )

        $writer.WriteLine('{"id":"setup","method":"__ping__"}')
        $response = $reader.ReadLine() | ConvertFrom-Json
        return $response.ok -and $response.result -eq "pong"
    } finally {
        $client.Dispose()
    }
}

function Test-TcpPortOpen {
    param([string]$HostName, [int]$HostPort, [int]$TimeoutMilliseconds = 750)

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.BeginConnect($HostName, $HostPort, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne($TimeoutMilliseconds)) {
            return $false
        }
        $client.EndConnect($pending)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

if ($Uninstall) {
    Write-Step "Uninstalling $TaskName"
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        if ($task.State -eq "Running") {
            Stop-ScheduledTask -TaskName $TaskName
        }
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-OK "Scheduled task removed"
    } else {
        Write-Skip "Scheduled task not found"
    }

    $rule = Get-NetFirewallRule -DisplayName $TaskName -ErrorAction SilentlyContinue
    if ($rule) {
        Remove-NetFirewallRule -DisplayName $TaskName
        Write-OK "Firewall rule removed"
    }
    exit 0
}

Write-Step "Checking uv"
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCommand) {
    Write-Host "  Installing uv from astral.sh..." -ForegroundColor Yellow
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = (
        [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [Environment]::GetEnvironmentVariable("Path", "User")
    )
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
}
if (-not $uvCommand) {
    throw "uv is required; install it from https://docs.astral.sh/uv/"
}
Write-OK "uv found: $($uvCommand.Source)"

Write-Step "Synchronizing locked dependencies"
Push-Location $ScriptDir
try {
    & $uvCommand.Source sync --locked --no-dev
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    throw "Synced Python interpreter not found: $VenvPython"
}
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
Write-OK "Dependencies installed"

Write-Step "Configuring firewall"
$loopbackAddresses = @("127.0.0.1", "::1", "localhost")
$isLoopback = $BindAddress -in $loopbackAddresses
$existingRule = Get-NetFirewallRule -DisplayName $TaskName -ErrorAction SilentlyContinue
if ($isLoopback) {
    if ($existingRule) {
        Remove-NetFirewallRule -DisplayName $TaskName
        Write-OK "Removed obsolete inbound firewall rule"
    } else {
        Write-Skip "Loopback bind needs no inbound firewall rule"
    }
} else {
    Write-Warn "Remote JSON/TCP access has no built-in authentication or TLS"
    Write-Warn "Restrict RemoteAddress and use a trusted private network or tunnel"
    if ($existingRule) {
        $existingRule |
            Set-NetFirewallRule `
                -Enabled True `
                -Profile Domain,Private `
                -Direction Inbound `
                -Action Allow `
                -RemoteAddress $RemoteAddress |
            Out-Null
        $existingRule |
            Get-NetFirewallPortFilter |
            Set-NetFirewallPortFilter -Protocol TCP -LocalPort $Port |
            Out-Null
        Write-OK "Firewall rule updated for $RemoteAddress"
    } else {
        New-NetFirewallRule `
            -DisplayName $TaskName `
            -Description "OrderFerry JSON/TCP relay" `
            -Direction Inbound `
            -Protocol TCP `
            -LocalPort $Port `
            -RemoteAddress $RemoteAddress `
            -Action Allow `
            -Profile Domain,Private |
        Out-Null
        Write-OK "Firewall rule created for $RemoteAddress"
    }
}

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$healthHost = if ($BindAddress -in @("0.0.0.0", "::")) { "127.0.0.1" } else { $BindAddress }
if (
    (-not $existingTask -or $existingTask.State -ne "Running") -and
    (Test-TcpPortOpen -HostName $healthHost -HostPort $Port)
) {
    throw (
        "${healthHost}:$Port is already in use. Stop the existing relay or " +
        "choose a different -Port before installing $TaskName."
    )
}

Write-Step "Testing MetaTrader connectivity"
if ($existingTask -and $existingTask.State -eq "Running") {
    Write-Skip "Existing relay is running; skipping competing MT5 self-test"
} else {
    & $VenvPython -u -m orderferry --test --log-dir $LogDir
    if ($LASTEXITCODE -eq 0) {
        Write-OK "MetaTrader connectivity test passed"
    } else {
        Write-Warn "MetaTrader is unavailable; the relay will start degraded and retry"
    }
}

Write-Step "Registering scheduled task"
$actionArguments = (
    "-u -m orderferry --bind `"$BindAddress`" --port $Port " +
    "--log-dir `"$LogDir`""
)
$action = New-ScheduledTaskAction `
    -Execute $VenvPython `
    -Argument $actionArguments `
    -WorkingDirectory $ScriptDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $TaskUser
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal `
    -UserId $TaskUser `
    -RunLevel Limited `
    -LogonType Interactive

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "OrderFerry market-data and trade-execution relay" `
    -Force |
Out-Null
Write-OK "Task registered for interactive user $TaskUser"

$registeredTask = Get-ScheduledTask -TaskName $TaskName
if ($registeredTask.State -eq "Running") {
    Stop-ScheduledTask -TaskName $TaskName
    Wait-TaskState -Name $TaskName -DesiredState "Ready" | Out-Null
}

Write-Step "Starting and verifying $TaskName"
Start-ScheduledTask -TaskName $TaskName
Wait-TaskState -Name $TaskName -DesiredState "Running" | Out-Null

$healthy = $false
$deadline = [DateTime]::UtcNow.AddSeconds(15)
do {
    try {
        $healthy = Test-OrderFerryProtocol -HostName $healthHost -HostPort $Port
    } catch {
        Start-Sleep -Milliseconds 500
    }
} while (-not $healthy -and [DateTime]::UtcNow -lt $deadline)

if (-not $healthy) {
    $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
    throw (
        "OrderFerry did not answer __ping__ " +
        "(taskResult=$($taskInfo.LastTaskResult)); see $LogDir\orderferry.log"
    )
}

$runningTask = Get-ScheduledTask -TaskName $TaskName
if ($runningTask.State -ne "Running") {
    $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
    throw (
        "Protocol health was answered, but $TaskName is not running " +
        "(state=$($runningTask.State), taskResult=$($taskInfo.LastTaskResult)). " +
        "Another process may own ${healthHost}:$Port."
    )
}

Write-OK "OrderFerry is listening on ${BindAddress}:$Port"
Write-Host "  Log: $LogDir\orderferry.log" -ForegroundColor DarkGray
