@echo off
:: Deve ser executado como Administrador
setlocal enabledelayedexpansion
cd /d "%~dp0"
if not exist "log" mkdir log

set LOG=%~dp0log\instalacao.log
set SERVICE_NAME=AgentExtratarLog
set INSTALL_DIR=C:\AgentExtratarLog
set NSSM_SRC=%~dp0nssm.exe
set ERRO=0

call :L "================================================"
call :L " Instalando Agent Extrator Log como Servico"
call :L " Diretorio de instalacao: %INSTALL_DIR%"
call :L "================================================"

:: Validacoes
if not exist "%NSSM_SRC%" (
    call :L "[ERRO] nssm.exe nao encontrado nesta pasta."
    set ERRO=1 & goto :fim
)
call :L "[OK] nssm.exe encontrado."

if not exist "%~dp0agent_extrator_log.exe" (
    call :L "[ERRO] agent_extrator_log.exe nao encontrado."
    set ERRO=1 & goto :fim
)
call :L "[OK] agent_extrator_log.exe encontrado."

:: ---- PASSO 1: Parar o servico se estiver em execucao ----
sc query %SERVICE_NAME% >nul 2>&1
if !errorlevel! equ 0 (
    call :L "Servico existente detectado. Parando..."
    sc stop %SERVICE_NAME% >nul 2>&1

    set /a TENTATIVAS=0
    :aguarda_parada
    timeout /t 2 /nobreak >nul
    sc query %SERVICE_NAME% | findstr /i "STOPPED" >nul
    if !errorlevel! neq 0 (
        set /a TENTATIVAS+=1
        if !TENTATIVAS! lss 10 (
            call :L "  Aguardando servico parar... tentativa !TENTATIVAS!/10"
            goto :aguarda_parada
        )
        call :L "[AVISO] Servico nao parou apos 20s. Forcando encerramento..."
        taskkill /f /im agent_extrator_log.exe >nul 2>&1
        timeout /t 2 /nobreak >nul
    )
    call :L "[OK] Servico parado."

    :: Remove o servico antes de copiar
    "%NSSM_SRC%" remove %SERVICE_NAME% confirm >> "%LOG%" 2>&1
    call :L "[OK] Servico anterior removido."
) else (
    call :L "Nenhum servico anterior encontrado."
)

:: ---- PASSO 2: Criar estrutura de diretorios ----
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
if not exist "%INSTALL_DIR%\log" mkdir "%INSTALL_DIR%\log"
call :L "[OK] Diretorio: %INSTALL_DIR%"

:: ---- PASSO 3: Copiar arquivos ----
call :L "Copiando arquivos..."

copy /y "%~dp0agent_extrator_log.exe" "%INSTALL_DIR%\agent_extrator_log.exe"
if !errorlevel! neq 0 (
    call :L "[ERRO] Falha ao copiar agent_extrator_log.exe. O arquivo pode estar em uso."
    set ERRO=1 & goto :fim
)
call :L "[OK] agent_extrator_log.exe copiado."

copy /y "%~dp0agent.properties" "%INSTALL_DIR%\agent.properties" >nul
call :L "[OK] agent.properties copiado."

copy /y "%NSSM_SRC%"                   "%INSTALL_DIR%\nssm.exe"              >nul
copy /y "%~dp0iniciar_servico.bat"     "%INSTALL_DIR%\iniciar_servico.bat"   >nul
copy /y "%~dp0parar_servico.bat"       "%INSTALL_DIR%\parar_servico.bat"     >nul
copy /y "%~dp0remover_servico.bat"     "%INSTALL_DIR%\remover_servico.bat"   >nul
call :L "[OK] Arquivos auxiliares copiados."

:: ---- PASSO 4: Instalar e configurar o servico ----
call :L "Instalando servico via NSSM..."
"%INSTALL_DIR%\nssm.exe" install %SERVICE_NAME% "%INSTALL_DIR%\agent_extrator_log.exe" >> "%LOG%" 2>&1
if !errorlevel! neq 0 (
    call :L "[ERRO] Falha ao instalar o servico via NSSM."
    set ERRO=1 & goto :fim
)

"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% DisplayName  "Agent Extrator Log - PDV"                        >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% Description  "Monitora caixa de e-mail e envia logs dos PDVs." >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% AppDirectory "%INSTALL_DIR%"                                   >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% Start        SERVICE_AUTO_START                                 >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% AppStdout    "%INSTALL_DIR%\log\servico_stdout.log"            >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% AppStderr    "%INSTALL_DIR%\log\servico_stderr.log"            >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% AppRotateFiles 1                                               >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% AppRotateBytes 5242880                                         >> "%LOG%" 2>&1
call :L "[OK] Servico configurado com inicio automatico."

:: ---- PASSO 5: Iniciar o servico ----
call :L "Iniciando servico..."
sc start %SERVICE_NAME% >> "%LOG%" 2>&1

set /a TENTATIVAS=0
:aguarda_inicio
timeout /t 2 /nobreak >nul
sc query %SERVICE_NAME% | findstr /i "RUNNING" >nul
if !errorlevel! neq 0 (
    set /a TENTATIVAS+=1
    if !TENTATIVAS! lss 10 (
        call :L "  Aguardando servico iniciar... tentativa !TENTATIVAS!/10"
        goto :aguarda_inicio
    )
    call :L "[ERRO] Servico nao entrou em execucao."
    call :L "       Verifique: services.msc > %SERVICE_NAME%"
    call :L "       Log de erro: %INSTALL_DIR%\log\servico_stderr.log"
    set ERRO=1 & goto :fim
)

call :L "[OK] Servico iniciado e em execucao."
call :L "------------------------------------------------"
call :L "Scripts de gestao em %INSTALL_DIR%:"
call :L "  iniciar_servico.bat  - Inicia o servico"
call :L "  parar_servico.bat    - Para o servico"
call :L "  remover_servico.bat  - Remove o servico"
call :L "------------------------------------------------"
call :L "Logs em %INSTALL_DIR%\log\"

:fim
call :L "================================================"
if !ERRO! equ 1 (
    call :L "Instalacao finalizada COM ERROS."
) else (
    call :L "Instalacao finalizada com SUCESSO."
)
call :L "================================================"
pause
exit /b !ERRO!

:L
echo [%DATE% %TIME%] %~1
echo [%DATE% %TIME%] %~1 >> "%LOG%"
exit /b 0
