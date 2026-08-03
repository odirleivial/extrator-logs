@echo off
:: Deve ser executado como Administrador
setlocal enabledelayedexpansion
cd /d "%~dp0"
if not exist "log" mkdir log

set LOG=%~dp0log\servico.log
set SERVICE_NAME=ServerAgentSP

call :L "============================================"
call :L "Iniciando servico: %SERVICE_NAME%"
call :L "============================================"

:: Verifica se o servico existe
sc query %SERVICE_NAME% >nul 2>&1
if !errorlevel! neq 0 (
    call :L "[ERRO] Servico '%SERVICE_NAME%' nao encontrado."
    call :L "       Execute instalar_servico.bat primeiro."
    goto :fim
)

:: Verifica se ja esta rodando
sc query %SERVICE_NAME% | findstr /i "RUNNING" >nul
if !errorlevel! equ 0 (
    call :L "[AVISO] Servico ja esta em execucao."
    goto :fim
)

sc start %SERVICE_NAME% >> "%LOG%" 2>&1

set /a TENTATIVAS=0
:aguarda
timeout /t 2 /nobreak >nul
sc query %SERVICE_NAME% | findstr /i "RUNNING" >nul
if !errorlevel! neq 0 (
    set /a TENTATIVAS+=1
    if !TENTATIVAS! lss 10 (
        call :L "  Aguardando... tentativa !TENTATIVAS!/10"
        goto :aguarda
    )
    call :L "[ERRO] Servico nao entrou em execucao. Verifique services.msc"
    goto :fim
)

call :L "[OK] Servico iniciado e em execucao."

:fim
call :L "============================================"
pause
exit /b 0

:L
echo [%DATE% %TIME%] %~1
echo [%DATE% %TIME%] %~1 >> "%LOG%"
exit /b 0
