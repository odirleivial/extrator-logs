@echo off
:: Deve ser executado como Administrador
setlocal enabledelayedexpansion
cd /d "%~dp0"
if not exist "log" mkdir log

set LOG=%~dp0log\instalacao.log
set SERVICE_NAME=AgentExtratarLog
set NSSM=%~dp0nssm.exe

call :L "============================================"
call :L "Removendo servico: %SERVICE_NAME%"
call :L "============================================"

if not exist "%NSSM%" (
    call :L "[ERRO] nssm.exe nao encontrado em %~dp0"
    goto :fim
)

"%NSSM%" stop %SERVICE_NAME% >nul 2>&1
call :L "Sinal de parada enviado."

"%NSSM%" remove %SERVICE_NAME% confirm >> "%LOG%" 2>&1
if !errorlevel! neq 0 (
    call :L "[ERRO] Falha ao remover o servico."
) else (
    call :L "[OK] Servico removido com sucesso."
)

:fim
call :L "============================================"
pause
exit /b 0

:L
echo [%DATE% %TIME%] %~1
echo [%DATE% %TIME%] %~1 >> "%LOG%"
exit /b 0
