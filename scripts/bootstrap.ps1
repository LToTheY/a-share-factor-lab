param(
    [string]$Python = "py",
    [string]$Extras = "free-data,dashboard,dev"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $VenvPython) {
    Write-Host "复用项目已有虚拟环境：$VenvPython"
} elseif ($Python -eq "py") {
    & py -3.12 --version
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.12不可用。请安装Python 3.12，或用 -Python 指定python.exe绝对路径。"
    }
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败。" }
} else {
    & $Python --version
    if ($LASTEXITCODE -ne 0) { throw "指定的Python不可用。" }
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败。" }
}

& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip更新失败，未继续安装。" }
& $VenvPython -m pip install -e ".[${Extras}]"
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败，请检查网络和上方错误。" }
if ($Extras -match "dashboard") {
    & $VenvPython scripts\doctor.py --profile dashboard
} else {
    & $VenvPython scripts\doctor.py
}
if ($LASTEXITCODE -ne 0) { throw "环境检查失败，请先解决上方问题。" }
& $VenvPython scripts\run_tests.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "环境已完成：$VenvPython"
