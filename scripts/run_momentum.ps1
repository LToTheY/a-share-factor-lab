param(
    [ValidateSet("synthetic", "real")]
    [string]$Mode = "real"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "未找到.venv。请先运行 scripts\bootstrap.ps1。"
}
Set-Location -LiteralPath $ProjectRoot
& $Python scripts\run_momentum_study.py --mode $Mode
