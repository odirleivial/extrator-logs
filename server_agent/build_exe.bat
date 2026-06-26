@echo off
cd /d "%~dp0"
if not exist "log" mkdir log
set LOG=%~dp0log\instalacao.log

call :LOG_MSG "================================================"
call :LOG_MSG " Build: agent_extrator_log.exe"
call :LOG_MSG "================================================"

:: Garante pyinstaller instalado
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    call :LOG_MSG "Instalando PyInstaller..."
    python -m pip install pyinstaller
)

:: Gera o executavel standalone
call :LOG_MSG "Compilando agent_extrator_log.py..."
python -m PyInstaller --onefile --console --name agent_extrator_log agent_extrator_log.py

if not exist "%~dp0dist\agent_extrator_log.exe" (
    call :LOG_MSG "[ERRO] Build falhou. Verifique os erros acima."
    pause & exit /b 1
)

:: Copia o exe para a raiz da pasta
copy /y "%~dp0dist\agent_extrator_log.exe" "%~dp0agent_extrator_log.exe" >nul
call :LOG_MSG "[OK] Executavel gerado: %~dp0agent_extrator_log.exe"
call :LOG_MSG "Execute instalar_servico.bat para registrar o servico."
call :LOG_MSG "================================================"
pause
exit /b 0

:LOG_MSG
set MSG=%~1
echo [%DATE% %TIME%] %MSG%
echo [%DATE% %TIME%] %MSG% >> "%LOG%"
exit /b 0
