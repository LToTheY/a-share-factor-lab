@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Project virtual environment is missing. See docs\USER_GUIDE.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -X utf8 -m streamlit run "dashboard\app.py" --server.address 127.0.0.1 %*
set "DashboardExit=%errorlevel%"
if not "%DashboardExit%"=="0" pause
exit /b %DashboardExit%
