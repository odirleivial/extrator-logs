@echo off
:: Deve ser executado como Administrador
setlocal enabledelayedexpansion
cd /d "%~dp0"
if not exist "log" mkdir log

set LOG=%~dp0log\servico.log
set SERVICE_NAME=AgentExtratarLog
set NSSM=%~dp0nssm.exe

call :L "============================================"
call :L "Iniciando servico: %SERVICE_NAME%"
call :L "============================================"

if not exist "%NSSM%" (
    call :L "[ERRO] nssm.exe nao encontrado em %~dp0"
    goto :fim
)

"%NSSM%" start %SERVICE_NAME% >> "%LOG%" 2>&1

timeout /t 2 >nul
sc query %SERVICE_NAME% | findstr /i "RUNNING" >nul
if !errorlevel! neq 0 (
    call :L "[ERRO] Servico nao entrou em execucao. Verifique services.msc"
) else (
    call :L "[OK] Servico iniciado e em execucao."
)

:fim
call :L "============================================"
pause
exit /b 0

:L
echo [%DATE% %TIME%] %~1
echo [%DATE% %TIME%] %~1 >> "%LOG%"
exit /b 0
