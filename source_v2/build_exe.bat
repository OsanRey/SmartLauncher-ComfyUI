@echo off
setlocal EnableExtensions

set "SOURCE_DIR=%~dp0"
set "PYTHON_EXE=%SOURCE_DIR%.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo ERROR: source_v2 development environment is missing.
    echo Create it before building Smart Launcher 2.0.
    pause
    exit /b 1
)

"%PYTHON_EXE%" "%SOURCE_DIR%build_exe.py"
set "BUILD_RESULT=%ERRORLEVEL%"

if not "%BUILD_RESULT%"=="0" (
    echo.
    echo ERROR: Smart Launcher 2.0 build failed.
    pause
    exit /b %BUILD_RESULT%
)

echo.
echo DONE: ..\release_v2\SmartLauncher2.exe
pause
