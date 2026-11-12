param(
    [string]$TaskName = "AlphaQuantAIPro",
    [switch]$ForceUserStartup
)

$ErrorActionPreference = "Stop"
$WorkingDir = "C:\bot"
$PythonPath = "C:\bot\.venv\Scripts\python.exe"
if (-not (Test-Path $PythonPath)) {
    $PythonPath = (Get-Command python.exe).Source
}
$SupervisorPath = Join-Path $WorkingDir "scripts\launch\alphaquant_supervisor.py"
$StartupFolder = [Environment]::GetFolderPath('Startup')
$VbsPath = Join-Path $StartupFolder "AlphaQuantAIPro.vbs"

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "   Installing AlphaQuant AI Pro 24/7 Autostart System" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " * Task Name:        $TaskName"
Write-Host " * Python Path:      $PythonPath"
Write-Host " * Supervisor:       $SupervisorPath"
Write-Host " * Working Dir:      $WorkingDir"

$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($IsAdmin -and (-not $ForceUserStartup)) {
    Write-Host " * Mode:             Windows Task Scheduler (Elevated Administrator)" -ForegroundColor Yellow
    $Action = New-ScheduledTaskAction -Execute $PythonPath -Argument "`"$SupervisorPath`"" -WorkingDirectory $WorkingDir
    $Trigger = New-ScheduledTaskTrigger -AtStartup
    $Settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Days 0 -Hours 0 -Minutes 0 -Seconds 0) `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew
    $Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest

    try {
        Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
        Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "AlphaQuant AI Pro 24/7 Continuous Trading Supervisor"
        Write-Host "[SUCCESS] Windows Task Scheduler '$TaskName' registered successfully!" -ForegroundColor Green
        Write-Host "  Start manually anytime via: Start-ScheduledTask -TaskName `"$TaskName`""
    } catch {
        Write-Host "[WARNING] Task Scheduler registration failed: $_" -ForegroundColor Red
        Write-Host "Falling back to User Startup Folder..." -ForegroundColor Yellow
        $IsAdmin = $false
    }
}

if (-not $IsAdmin -or $ForceUserStartup) {
    Write-Host " * Mode:             Windows User Startup Folder (No Admin Required)" -ForegroundColor Yellow
    # Create a headless VBS launcher in Startup that runs without flashing a black CMD window
    $PythonW = Join-Path $WorkingDir ".venv\Scripts\pythonw.exe"
    $TargetExe = if (Test-Path $PythonW) { $PythonW } else { $PythonPath }
    $VbsContent = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "$WorkingDir"
WshShell.Run """$TargetExe"" ""$SupervisorPath""", 0, False
"@
    Set-Content -Path $VbsPath -Value $VbsContent -Encoding ASCII
    Write-Host "[SUCCESS] Autostart VBScript installed to: $VbsPath" -ForegroundColor Green
    Write-Host "AlphaQuant AI Pro will start automatically whenever Windows boots or you log in."
}
