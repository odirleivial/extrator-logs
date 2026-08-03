@echo off
:: ===========================================================================
:: Testa o agente sem mexer na caixa de e-mail:
::  - conexao TCP e login IMAP no Gmail
::  - resolucao dos caminhos/nomes de cada log configurado, indicando quais
::    arquivos existem hoje nos servidores
:: ===========================================================================
setlocal
cd /d "%~dp0"
set PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe

if not exist "%~dp0server_agent_sp.ps1" (
    echo [ERRO] server_agent_sp.ps1 nao encontrado nesta pasta.
    pause & exit /b 1
)

:: Remove a marca de "arquivo da internet", caso a pasta tenha vindo de um zip
"%PS_EXE%" -NoProfile -Command "Unblock-File -Path '%~dp0server_agent_sp.ps1' -ErrorAction SilentlyContinue"

"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0server_agent_sp.ps1" -TesteConexao

echo.
pause
