@echo off
title Build - Extrator de Logs
cd /d "%~dp0"

echo ============================================================
echo  Build: Extrator de Logs
echo ============================================================
echo.

REM ── 1. Verifica Python ──────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado no PATH.
    pause & exit /b 1
)

REM ── Lê versão de version.py ──────────────────────────────────
for /f "tokens=*" %%i in ('python -c "from version import __version__; print(__version__)"') do set APP_VERSION=%%i
if not defined APP_VERSION set APP_VERSION=0.0.0
echo     Versao detectada: %APP_VERSION%

REM ── 2. Instala / atualiza dependencias de build ─────────────
echo [1/5] Instalando dependencias de build...
pip install pyinstaller --quiet
pip install pyinstaller-hooks-contrib --quiet
pip install pywebview --quiet

REM ── 3. Limpa builds anteriores ──────────────────────────────
echo [2/5] Limpando builds anteriores...
if exist build   rmdir /s /q build
if exist dist\ExtratordeLogs rmdir /s /q dist\ExtratordeLogs

REM ── 4. PyInstaller ──────────────────────────────────────────
echo [3/5] Empacotando aplicacao com PyInstaller...
python -m PyInstaller extrator_logs.spec --noconfirm

if not exist "dist\ExtratordeLogs\ExtratordeLogs.exe" (
    echo.
    echo [ERRO] PyInstaller falhou. Verifique as mensagens acima.
    pause & exit /b 1
)
echo      OK - Executavel gerado em dist\ExtratordeLogs\
echo.

REM ── 4b. Copia properties (config/secure/agent) para o exe rodar standalone ──
REM Aplicacao de uso interno: as chaves reais podem ir para os pacotes locais,
REM mas dist/ ja esta no .gitignore e nunca deve ser versionado.
echo [4/5] Copiando properties para dist\ExtratordeLogs\...
if not exist "dist\ExtratordeLogs\properties" mkdir "dist\ExtratordeLogs\properties"
copy /y "properties\config.properties" "dist\ExtratordeLogs\properties\" >nul
copy /y "properties\secure.properties" "dist\ExtratordeLogs\properties\" >nul
copy /y "properties\agent.properties"  "dist\ExtratordeLogs\properties\" >nul
echo      OK - properties copiados
echo.

REM ── 5. Inno Setup ───────────────────────────────────────────
echo [5/5] Gerando instalador com Inno Setup...

set ISCC=
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe"       set ISCC="C:\Program Files\Inno Setup 6\ISCC.exe"

if not defined ISCC (
    echo.
    echo [AVISO] Inno Setup 6 nao encontrado.
    echo         Baixe em: https://jrsoftware.org/isinfo.php
    echo         Apos instalar, execute build.bat novamente.
    echo.
    echo         O executavel standalone esta disponivel em:
    echo         dist\ExtratordeLogs\ExtratordeLogs.exe
    pause & exit /b 0
)

if not exist "dist\installer" mkdir "dist\installer"
%ISCC% /DAppVersion=%APP_VERSION% setup.iss

if not exist "dist\installer\ExtratordeLogs_Setup_v%APP_VERSION%.exe" (
    echo [ERRO] Inno Setup falhou. Verifique as mensagens acima.
    pause & exit /b 1
)

echo.
echo ============================================================
echo  Build concluido com sucesso!
echo  Versao: %APP_VERSION%
echo  Instalador: dist\installer\ExtratordeLogs_Setup_v%APP_VERSION%.exe
echo ============================================================
echo.
pause
