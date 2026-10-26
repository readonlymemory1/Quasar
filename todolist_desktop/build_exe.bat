@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
cd /d "%PROJECT_ROOT%"
python -m PyInstaller --onefile --windowed --clean --noconfirm --name QuasarTodo --paths "%PROJECT_ROOT%" --hidden-import todo_store --distpath "%~dp0" --workpath "%~dp0build" --specpath "%~dp0" "%~dp0app.py"
if errorlevel 1 (
  echo Executable build failed.
  pause
  exit /b 1
)
echo Built: %~dp0QuasarTodo.exe
pause
