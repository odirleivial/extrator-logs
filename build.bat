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

REM ── 2. Instala / atualiza dependencias de build ─────────────
echo [1/4] Instalando dependencias de build...
pip install pyinstaller --quiet
pip install pyinstaller-hooks-contrib --quiet

REM ── 3. Limpa builds anteriores ──────────────────────────────
echo [2/4] Limpando builds anteriores...
if exist build   rmdir /s /q build
if exist dist\ExtratordeLogs rmdir /s /q dist\ExtratordeLogs

REM ── 4. PyInstaller ──────────────────────────────────────────
echo [3/4] Empacotando aplicacao com PyInstaller...
python -m PyInstaller extrator_logs.spec --noconfirm

if not exist "dist\ExtratordeLogs\ExtratordeLogs.exe" (
    echo.
    echo [ERRO] PyInstaller falhou. Verifique as mensagens acima.
    pause & exit /b 1
)
echo      OK - Executavel gerado em dist\ExtratordeLogs\
echo.

REM ── 5. Inno Setup ───────────────────────────────────────────
echo [4/4] Gerando instalador com Inno Setup...

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
%ISCC% setup.iss

if not exist "dist\installer\ExtratordeLogs_Setup.exe" (
    echo [ERRO] Inno Setup falhou. Verifique as mensagens acima.
    pause & exit /b 1
)

echo.
echo ============================================================
echo  Build concluido com sucesso!
echo  Instalador: dist\installer\ExtratordeLogs_Setup.exe
echo ============================================================
echo.
pause
