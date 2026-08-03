@echo off
:: Deve ser executado como Administrador
setlocal enabledelayedexpansion
cd /d "%~dp0"
if not exist "log" mkdir log

set LOG=%~dp0log\servico.log
set SERVICE_NAME=ServerAgentSP

call :L "============================================"
call :L "Parando servico: %SERVICE_NAME%"
call :L "============================================"

sc query %SERVICE_NAME% >nul 2>&1
if !errorlevel! neq 0 (
    call :L "[AVISO] Servico '%SERVICE_NAME%' nao encontrado ou ja removido."
    goto :fim
)

sc query %SERVICE_NAME% | findstr /i "STOPPED" >nul
if !errorlevel! equ 0 (
    call :L "[AVISO] Servico ja esta parado."
    goto :fim
)

sc stop %SERVICE_NAME% >> "%LOG%" 2>&1

set /a TENTATIVAS=0
:aguarda
timeout /t 2 /nobreak >nul
sc query %SERVICE_NAME% | findstr /i "STOPPED" >nul
if !errorlevel! neq 0 (
    set /a TENTATIVAS+=1
    if !TENTATIVAS! lss 10 (
        call :L "  Aguardando parada... tentativa !TENTATIVAS!/10"
        goto :aguarda
    )
    call :L "[AVISO] Servico nao parou no tempo esperado. Encerrando o processo do servico..."
    :: Encerra apenas o PID deste servico — nunca todos os powershell.exe da maquina
    set SVCPID=
    for /f "tokens=3" %%a in ('sc queryex %SERVICE_NAME% ^| findstr /i "PID"') do set SVCPID=%%a
    if defined SVCPID if not "!SVCPID!"=="0" (
        taskkill /f /pid !SVCPID! >nul 2>&1
        call :L "[OK] Processo !SVCPID! encerrado."
    )
    goto :fim
)

call :L "[OK] Servico parado com sucesso."

:fim
call :L "============================================"
pause
exit /b 0

:L
echo [%DATE% %TIME%] %~1
echo [%DATE% %TIME%] %~1 >> "%LOG%"
exit /b 0
