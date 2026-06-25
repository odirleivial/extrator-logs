@echo off
title Extrator de Logs - Iniciando...

REM Navega para a pasta onde este .bat esta localizado
cd /d "%~dp0"

REM Verifica se o arquivo principal existe
if not exist "extrator_logs.py" (
    echo Erro: extrator_logs.py nao encontrado em "%~dp0"
    pause
    exit /b 1
)

REM Verifica se o Python esta disponivel
python --version >nul 2>&1
if errorlevel 1 (
    echo Erro: Python nao encontrado. Verifique se esta instalado e no PATH.
    pause
    exit /b 1
)

echo Iniciando servidor Flask em http://localhost:5000 ...
echo Pressione Ctrl+C para encerrar.
echo.

REM Abre o browser apos 2 segundos (em paralelo com o servidor)
start "" cmd /c "timeout /t 2 >nul && start http://localhost:5000"

REM Inicia o servidor Flask (janela atual permanece aberta com os logs)
python extrator_logs.py

pause
