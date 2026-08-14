@echo off
:: Deve ser executado como Administrador
setlocal enabledelayedexpansion
cd /d "%~dp0"
if not exist "log" mkdir log

set LOG=%~dp0log\instalacao.log
set SERVICE_NAME=ServerAgentSP
set INSTALL_DIR=C:\ServerAgentSP
set NSSM_SRC=%~dp0nssm.exe
set PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe
set ERRO=0

call :L "================================================"
call :L " Instalando Server Agent SP como Servico"
call :L " Diretorio de instalacao: %INSTALL_DIR%"
call :L " Executa via: %PS_EXE%"
call :L "================================================"

:: Validacoes
if not exist "%NSSM_SRC%" (
    call :L "[ERRO] nssm.exe nao encontrado nesta pasta."
    set ERRO=1 & goto :fim
)
if not exist "%~dp0server_agent_sp.ps1" (
    call :L "[ERRO] server_agent_sp.ps1 nao encontrado nesta pasta."
    set ERRO=1 & goto :fim
)
if not exist "%PS_EXE%" (
    call :L "[ERRO] powershell.exe nao encontrado em %PS_EXE%"
    set ERRO=1 & goto :fim
)
call :L "[OK] Arquivos necessarios encontrados."

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
        call :L "[AVISO] Servico nao parou apos 20s. Encerrando o processo do servico..."
        call :MATA_PROCESSO_SERVICO
    )
    call :L "[OK] Servico parado."

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
copy /y "%~dp0server_agent_sp.ps1" "%INSTALL_DIR%\server_agent_sp.ps1" >nul
if !errorlevel! neq 0 (
    call :L "[ERRO] Falha ao copiar server_agent_sp.ps1"
    set ERRO=1 & goto :fim
)
copy /y "%~dp0version.ps1" "%INSTALL_DIR%\version.ps1" >nul
if !errorlevel! neq 0 (
    call :L "[AVISO] version.ps1 nao copiado. O agente reportara versao 0.0.0."
)
copy /y "%~dp0agent.properties" "%INSTALL_DIR%\agent.properties" >nul
copy /y "%NSSM_SRC%"                   "%INSTALL_DIR%\nssm.exe"              >nul
copy /y "%~dp0iniciar_servico.bat"     "%INSTALL_DIR%\iniciar_servico.bat"   >nul
copy /y "%~dp0parar_servico.bat"       "%INSTALL_DIR%\parar_servico.bat"     >nul
copy /y "%~dp0remover_servico.bat"     "%INSTALL_DIR%\remover_servico.bat"   >nul
copy /y "%~dp0testar_agente.bat"       "%INSTALL_DIR%\testar_agente.bat"     >nul
call :L "[OK] Arquivos copiados."

:: ---- PASSO 4: Remover a marca de "arquivo da internet" do script ----
:: A politica do dominio e RemoteSigned: scripts locais rodam normalmente, mas
:: arquivos vindos de download/zip ficam marcados e sao recusados.
"%PS_EXE%" -NoProfile -Command "Get-ChildItem -LiteralPath '%INSTALL_DIR%' -Recurse -Include *.ps1,*.bat | Unblock-File" >> "%LOG%" 2>&1
call :L "[OK] Scripts desbloqueados (Unblock-File)."

:: ---- PASSO 5: Instalar e configurar o servico ----
call :L "Instalando servico via NSSM..."
"%INSTALL_DIR%\nssm.exe" install %SERVICE_NAME% "%PS_EXE%" >> "%LOG%" 2>&1
if !errorlevel! neq 0 (
    call :L "[ERRO] Falha ao instalar o servico via NSSM."
    set ERRO=1 & goto :fim
)

"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% AppParameters "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File %INSTALL_DIR%\server_agent_sp.ps1" >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% DisplayName  "Server Agent SP - Extrator de Logs"                    >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% Description  "Monitora caixa de e-mail e envia logs dos servidores." >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% AppDirectory "%INSTALL_DIR%"                                         >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% Start        SERVICE_AUTO_START                                       >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% AppStdout    "%INSTALL_DIR%\log\servico_stdout.log"                  >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% AppStderr    "%INSTALL_DIR%\log\servico_stderr.log"                  >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% AppRotateFiles 1                                                     >> "%LOG%" 2>&1
"%INSTALL_DIR%\nssm.exe" set %SERVICE_NAME% AppRotateBytes 5242880                                               >> "%LOG%" 2>&1
call :L "[OK] Servico configurado com inicio automatico."

:: ---- PASSO 6: Iniciar o servico ----
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
    call :L "       Verifique: services.msc ^> %SERVICE_NAME%"
    call :L "       Log de erro: %INSTALL_DIR%\log\servico_stderr.log"
    set ERRO=1 & goto :fim
)

call :L "[OK] Servico iniciado e em execucao."
call :L "------------------------------------------------"
call :L "Scripts de gestao em %INSTALL_DIR%:"
call :L "  iniciar_servico.bat  - Inicia o servico"
call :L "  parar_servico.bat    - Para o servico"
call :L "  remover_servico.bat  - Remove o servico"
call :L "  testar_agente.bat    - Testa conexao e caminhos dos logs"
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

:: Encerra apenas o processo DESTE servico (nunca todos os powershell.exe da maquina)
:MATA_PROCESSO_SERVICO
set SVCPID=
for /f "tokens=3" %%a in ('sc queryex %SERVICE_NAME% ^| findstr /i "PID"') do set SVCPID=%%a
if defined SVCPID if not "!SVCPID!"=="0" (
    taskkill /f /pid !SVCPID! >nul 2>&1
    call :L "  Processo !SVCPID! encerrado."
)
timeout /t 2 /nobreak >nul
exit /b 0

:L
echo [%DATE% %TIME%] %~1
echo [%DATE% %TIME%] %~1 >> "%LOG%"
exit /b 0
