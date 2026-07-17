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

:: Para o servico se estiver rodando
sc query %SERVICE_NAME% >nul 2>&1
if !errorlevel! equ 0 (
    sc query %SERVICE_NAME% | findstr /i "RUNNING" >nul
    if !errorlevel! equ 0 (
        call :L "Parando servico antes de remover..."
        sc stop %SERVICE_NAME% >nul 2>&1

        set /a TENTATIVAS=0
        :aguarda_parada
        timeout /t 2 /nobreak >nul
        sc query %SERVICE_NAME% | findstr /i "STOPPED" >nul
        if !errorlevel! neq 0 (
            set /a TENTATIVAS+=1
            if !TENTATIVAS! lss 10 goto :aguarda_parada
            call :L "[AVISO] Forcando encerramento do processo..."
            taskkill /f /im agent_extrator_log.exe >nul 2>&1
            timeout /t 2 /nobreak >nul
        )
        call :L "[OK] Servico parado."
    )

    "%NSSM%" remove %SERVICE_NAME% confirm >> "%LOG%" 2>&1
    if !errorlevel! neq 0 (
        call :L "[ERRO] Falha ao remover o servico."
    ) else (
        call :L "[OK] Servico removido com sucesso."
    )
) else (
    call :L "[AVISO] Servico '%SERVICE_NAME%' nao encontrado."
)

:fim
call :L "============================================"
pause
exit /b 0

:L
echo [%DATE% %TIME%] %~1
echo [%DATE% %TIME%] %~1 >> "%LOG%"
exit /b 0
