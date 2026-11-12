param(
    [string]$TaskName = "AlphaQuantAIPro"
)

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "   Removing AlphaQuant AI Pro Autostart Configurations" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

# 1. Remove Task Scheduler task if present
try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "[SUCCESS] Scheduled Task '$TaskName' removed successfully." -ForegroundColor Green
    }
} catch {
    # Ignore if not admin or not present
}

# 2. Remove User Startup VBS if present
$StartupFolder = [Environment]::GetFolderPath('Startup')
$VbsPath = Join-Path $StartupFolder "AlphaQuantAIPro.vbs"
if (Test-Path $VbsPath) {
    Remove-Item $VbsPath -Force
    Write-Host "[SUCCESS] User Startup entry removed: $VbsPath" -ForegroundColor Green
} else {
    Write-Host "[INFO] User Startup entry not present." -ForegroundColor Yellow
}
