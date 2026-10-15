# Запуск бота на Windows з автоматичним перезапуском, якщо він упав.
# Його запускає Планувальник завдань (див. README, розділ «Запуск на Windows»).
# Файл збережено в UTF-8 з BOM — інакше Windows PowerShell 5.1 зіпсує кирилицю.

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$launcherLog = Join-Path $logDir "launcher.log"

$env:PYTHONUTF8 = "1"                            # кирилиця в Python без сюрпризів
$env:LOG_FILE = Join-Path $logDir "bot.log"      # лог самого бота пише Python (UTF-8, з ротацією)

function Write-Log([string]$message) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $launcherLog -Encoding UTF8 -Value "$stamp $message"
}

while ($true) {
    Write-Log "Оновлюю схему бази"
    & $python -m alembic upgrade head 2>&1 | ForEach-Object { Write-Log "$_" }

    Write-Log "Запускаю бота"
    & $python -m bot.main
    Write-Log "Бот зупинився (код $LASTEXITCODE). Перезапуск через 15 секунд"
    Start-Sleep -Seconds 15
}
