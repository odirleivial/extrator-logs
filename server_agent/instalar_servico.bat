@echo off
:: Deve ser executado como Administrador
setlocal enabledelayedexpansion
cd /d "%~dp0"
if not exist "log" mkdir log

set LOG=%~dp0log\instalacao.log
set SERVICE_NAME=AgentExtratarLog
set INSTALL_DIR=C:\AgentExtratarLog
set ERRO=0

call :L "================================================"
call :L " Instalando Agent Extrator Log como Servico"
call :L " Diretorio de instalacao: %INSTALL_DIR%"
call :L "================================================"

:: Validacoes
if not exist "%~dp0nssm.exe" (
    call :L "[ERRO] nssm.exe nao encontrado nesta pasta."
    call :L "       Baixe em: https://nssm.cc/download"
    call :L "       Extraia nssm.exe (pasta win64) para: %~dp0"
    set ERRO=1
    goto :fim
)
call :L "[OK] nssm.exe encontrado."

if not exist "%~dp0agent_extrator_log.exe" (
    call :L "[ERRO] agent_extrator_log.exe nao encontrado."
    call :L "       Execute build_exe.bat primeiro."
    set ERRO=1
    goto :fim
)
call :L "[OK] agent_extrator_log.exe encontrado."

:: Cria estrutura de diretorios
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
if not exist "%INSTALL_DIR%\log" mkdir "%INSTALL_DIR%\log"
call :L "[OK] Diretorio criado: %INSTALL_DIR%"

:: Copia todos os arquivos necessarios
copy /y "%~dp0agent_extrator_log.exe" "%INSTALL_DIR%\agent_extrator_log.exe" >nul
copy /y "%~dp0agent.properties"       "%INSTALL_DIR%\agent.properties"       >nul
copy /y "%~dp0nssm.exe"               "%INSTALL_DIR%\nssm.exe"               >nul
copy /y "%~dp0iniciar_servico.bat"    "%INSTALL_DIR%\iniciar_servico.bat"    >nul
copy /y "%~dp0parar_servico.bat"      "%INSTALL_DIR%\parar_servico.bat"      >nul
copy /y "%~dp0remover_servico.bat"    "%INSTALL_DIR%\remover_servico.bat"    >nul
call :L "[OK] Arquivos copiados para %INSTALL_DIR%"

:: Remove servico anterior se existir
nssm.exe stop %SERVICE_NAME% >nul 2>&1
nssm.exe remove %SERVICE_NAME% confirm >nul 2>&1
call :L "Servico anterior removido (se existia)."

:: Instala o servico
"%INSTALL_DIR%\nssm.exe" install %SERVICE_NAME% "%INSTALL_DIR%\agent_extrator_log.exe" >> "%LOG%" 2>&1
if !errorlevel! neq 0 (
    call :L "[ERRO] Falha ao instalar o servico via NSSM."
    set ERRO=1
    goto :fim
)

:: Configura o servico
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% DisplayName "Agent Extrator Log - PDV"                          >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% Description "Monitora caixa de e-mail e envia logs dos PDVs."   >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% AppDirectory "%INSTALL_DIR%"                                     >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% Start SERVICE_AUTO_START                                         >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% AppStdout "%INSTALL_DIR%\log\servico_stdout.log"                 >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% AppStderr "%INSTALL_DIR%\log\servico_stderr.log"                 >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% AppRotateFiles 1                                                  >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% AppRotateBytes 5242880                                           >> "%LOG%" 2>&1
call :L "[OK] Servico configurado com inicio automatico."

:: Inicia o servico
"%INSTALL_DIR%\nssm.exe" start %SERVICE_NAME% >> "%LOG%" 2>&1

:: Aguarda e valida status real
timeout /t 3 >nul
sc query %SERVICE_NAME% | findstr /i "RUNNING" >nul
if !errorlevel! neq 0 (
    call :L "[ERRO] Servico nao entrou em execucao."
    call :L "       Verifique: services.msc > %SERVICE_NAME%"
    call :L "       Log de erro: %INSTALL_DIR%\log\servico_stderr.log"
    set ERRO=1
    goto :fim
)

call :L "[OK] Servico iniciado e em execucao."
call :L "------------------------------------------------"
call :L "Scripts de gestao disponíveis em %INSTALL_DIR%:"
call :L "  iniciar_servico.bat  - Inicia o servico"
call :L "  parar_servico.bat    - Para o servico"
call :L "  remover_servico.bat  - Remove o servico"
call :L "------------------------------------------------"
call :L "Logs em %INSTALL_DIR%\log\"
call :L "  operacao.log         - Processamento de e-mails"
call :L "  servico_stdout.log   - Saida do processo"
call :L "  servico_stderr.log   - Erros do processo"

:fim
call :L "================================================"
if !ERRO! equ 1 (
    call :L "Instalacao finalizada COM ERROS."
) else (
    call :L "Instalacao finalizada com sucesso."
)
call :L "================================================"
pause
exit /b !ERRO!

:L
echo [%DATE% %TIME%] %~1
echo [%DATE% %TIME%] %~1 >> "%LOG%"
exit /b 0
