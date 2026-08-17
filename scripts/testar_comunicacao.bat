@echo off
REM Lancador do testar_comunicacao.ps1 para maquinas onde a ExecutionPolicy vem
REM de GPO. Nesses casos "powershell -ExecutionPolicy Bypass -File script.ps1"
REM falha com "is not digitally signed", porque MachinePolicy/UserPolicy tem
REM precedencia sobre o -ExecutionPolicy da linha de comando.
REM
REM A ExecutionPolicy se aplica a ARQUIVOS .ps1. Lendo o conteudo e executando
REM com Invoke-Expression, o codigo roda como comando e a politica nao se aplica.
REM
REM Uso: coloque este .bat na MESMA pasta do testar_comunicacao.ps1 e execute.
REM      Para apontar um properties especifico:
REM          testar_comunicacao.bat C:\ServerAgentSP\agent.properties

setlocal

set "PS1=%~dp0testar_comunicacao.ps1"

if not exist "%PS1%" (
    echo [ERRO] testar_comunicacao.ps1 nao encontrado em %~dp0
    echo        Copie os dois arquivos para a mesma pasta.
    exit /b 1
)

if not "%~1"=="" set "BEC_CONFIG=%~1"

echo Executando via Invoke-Expression (contorna ExecutionPolicy por GPO)...
echo.

powershell.exe -NoProfile -NonInteractive -Command ^
  "$ErrorActionPreference='Continue'; $aqui='%~dp0'.TrimEnd('\'); Invoke-Expression (Get-Content -Raw -LiteralPath '%PS1%')"

set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
    echo [AVISO] PowerShell terminou com codigo %RC%.
    echo         Se a saida acima estiver vazia, o ambiente pode estar em
    echo         Constrained Language Mode via AppLocker/WDAC - nesse caso nem
    echo         Invoke-Expression roda, e o teste precisa ser feito na mao.
    echo         Comandos minimos para conferir o essencial:
    echo.
    echo           powershell -NoProfile -Command "Get-ExecutionPolicy -List"
    echo           powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; (Invoke-WebRequest -Uri 'https://bec-relay.odirleivial.workers.dev/status' -Headers @{'X-Token'='SEU_TOKEN'} -UseBasicParsing).StatusCode"
)

endlocal & exit /b %RC%
