@echo off
:: ===========================================================================
:: Executa o teste_powershell_gmail.ps1 mesmo quando a politica de execucao do
:: dominio impede rodar arquivos .ps1 (-ExecutionPolicy Bypass ignorado).
::
:: O conteudo do script e enviado ao PowerShell pela entrada padrao: a politica
:: de execucao se aplica a ARQUIVOS de script, nao a comandos. Isso e o
:: comportamento documentado pela Microsoft (a politica nao e um controle de
:: seguranca, e sim uma protecao contra execucao acidental de scripts).
::
:: Uso: clique duplo, ou execute em um prompt como Administrador.
:: ===========================================================================
cd /d "%~dp0"

if not exist "%~dp0teste_powershell_gmail.ps1" (
    echo [ERRO] teste_powershell_gmail.ps1 nao encontrado nesta pasta.
    pause & exit /b 1
)

echo Executando teste de conexao PowerShell -^> Gmail...
echo.
type "%~dp0teste_powershell_gmail.ps1" | powershell -NoProfile -Command -

echo.
pause
