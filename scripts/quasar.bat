@echo off
setlocal
set "LOG_FILE=%TEMP%\Quasar-launch.log"
echo Starting Quasar... > "%LOG_FILE%"
cd /d "%~dp0.."
python "main.py" %* >> "%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo Quasar exited with code %EXIT_CODE%.
    echo Diagnostic output: "%LOG_FILE%"
    type "%LOG_FILE%"
    pause
)
exit /b %EXIT_CODE%
