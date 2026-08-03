@echo off
cd /d "%~dp0"
if not exist "log" mkdir log
set LOG=%~dp0log\instalacao.log

call :LOG_MSG "================================================"
call :LOG_MSG " Build: server_agent_sp.exe"
call :LOG_MSG "================================================"

:: Garante pyinstaller instalado
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    call :LOG_MSG "Instalando PyInstaller..."
    python -m pip install pyinstaller
)

:: Gera o executavel standalone
call :LOG_MSG "Compilando server_agent_sp.py..."
python -m PyInstaller --onefile --console --name server_agent_sp server_agent_sp.py

if not exist "%~dp0dist\server_agent_sp.exe" (
    call :LOG_MSG "[ERRO] Build falhou. Verifique os erros acima."
    pause & exit /b 1
)

:: Copia o exe para a raiz da pasta
copy /y "%~dp0dist\server_agent_sp.exe" "%~dp0server_agent_sp.exe" >nul
call :LOG_MSG "[OK] Executavel gerado: %~dp0server_agent_sp.exe"
call :LOG_MSG "Execute instalar_servico.bat para registrar o servico."
call :LOG_MSG "================================================"
pause
exit /b 0

:LOG_MSG
set MSG=%~1
echo [%DATE% %TIME%] %MSG%
echo [%DATE% %TIME%] %MSG% >> "%LOG%"
exit /b 0
