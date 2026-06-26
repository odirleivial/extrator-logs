@echo off
:: Deve ser executado como Administrador
setlocal enabledelayedexpansion
cd /d "%~dp0"
if not exist "log" mkdir log

set LOG=%~dp0log\servico.log
set SERVICE_NAME=AgentExtratarLog
set NSSM=%~dp0nssm.exe

call :L "============================================"
call :L "Parando servico: %SERVICE_NAME%"
call :L "============================================"

if not exist "%NSSM%" (
    call :L "[ERRO] nssm.exe nao encontrado em %~dp0"
    goto :fim
)

"%NSSM%" stop %SERVICE_NAME% >> "%LOG%" 2>&1

timeout /t 2 >nul
sc query %SERVICE_NAME% | findstr /i "RUNNING" >nul
if !errorlevel! equ 0 (
    call :L "[ERRO] Servico ainda esta em execucao."
) else (
    call :L "[OK] Servico parado com sucesso."
)

:fim
call :L "============================================"
pause
exit /b 0

:L
echo [%DATE% %TIME%] %~1
echo [%DATE% %TIME%] %~1 >> "%LOG%"
exit /b 0
