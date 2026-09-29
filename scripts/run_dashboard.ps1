param([ValidateRange(1024, 65535)][int]$Port = 8501, [switch]$Headless)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "未找到.venv。请先运行 scripts\bootstrap.ps1。"
}
Set-Location -LiteralPath $ProjectRoot
& $Python -m streamlit run dashboard\app.py --server.address 127.0.0.1 --server.port $Port --server.headless $Headless.IsPresent.ToString().ToLower()
exit $LASTEXITCODE
