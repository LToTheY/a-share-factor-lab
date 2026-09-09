param(
    [string]$Python = "py",
    [string]$Extras = "free-data,dev"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if ($Python -eq "py") {
    & py -3.12 --version
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.12不可用。请安装Python 3.12，或用 -Python 指定python.exe绝对路径。"
    }
    & py -3.12 -m venv .venv
} else {
    & $Python --version
    & $Python -m venv .venv
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -e ".[${Extras}]"
& $VenvPython scripts\doctor.py
& $VenvPython scripts\run_tests.py
Write-Host "环境已完成：$VenvPython"
