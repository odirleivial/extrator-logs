# Backoffice Equipe QA (BEC)

Aplicativo desktop (Windows) que centraliza ferramentas do dia a dia da equipe de QA: solicitação de logs de PDV, exportação de dados Oracle, requisições a APIs externas, manutenção remota de PDV, operações de PinPad e consultas MDM.

## 🇧🇷 Português

### O que é
BEC é uma aplicação Python (Flask + [pywebview](https://pywebview.flowrl.com/)) empacotada como executável Windows via PyInstaller. Roda localmente na máquina do usuário, com uma janela nativa renderizando a interface web.

### Principais funcionalidades
- **Solicitar Logs** — pede logs de um PDV ao Agent Extrator de Log (por e-mail ou pelo relay Cloudflare) e baixa o resultado.
- **Exportar Oracle** — consultas SQL pré-cadastradas, consulta livre (SQL colado na hora) e geração de *Explain Plan* visual; suporta múltiplas conexões Oracle nomeadas, exportação em CSV/XLSX, envio por e-mail e download direto.
- **Requisição API** — dispara chamadas a APIs externas cadastradas (com token/apikey) e salva ou envia o retorno.
- **Manutenção PDV** — parametrização, verificação, relatório, status, fechar/reiniciar PDV remotamente.
- **PinPad** — comandos ao PinPad (direto, e-mail ou túnel).
- **MDM** — consulta, cadastro e atualização de clientes.
- **Configurações** — abas visíveis, e-mails de destino, lojas/PDVs, consultas Oracle, conexões Oracle, APIs e (opcional) credenciais sensíveis via aba Administrador.

### Stack
Python 3, Flask, pywebview, `python-oracledb`, `openpyxl`, Pillow. Empacotado com PyInstaller + Inno Setup.

### Executando localmente
Instale as dependências listadas em `extrator_logs.spec` (Flask, pywebview, python-oracledb, openpyxl, Pillow, requests etc.) e rode:
```
python extrator_logs.py
```
Configuração em `properties/config.properties` (versionado) e `properties/secure.properties` (credenciais, **não versionado**).

### Aviso
Projeto de uso interno. Nenhuma credencial real deve ser commitada — `secure.properties` fica fora do controle de versão.

---

## 🇺🇸 English

### What it is
BEC is a Python (Flask + [pywebview](https://pywebview.flowrl.com/)) desktop application packaged as a Windows executable with PyInstaller. It runs locally on the user's machine, with a native window rendering the web UI.

### Main features
- **Solicitar Logs (Request Logs)** — requests PDV logs from the Agent Extrator de Log (via email or the Cloudflare relay) and downloads the result.
- **Exportar Oracle (Oracle Export)** — pre-registered SQL queries, free-form ad-hoc SQL, and visual Explain Plan generation; supports multiple named Oracle connections, CSV/XLSX export, email delivery and direct download.
- **Requisição API (API Request)** — triggers calls to registered external APIs (with token/apikey) and saves or emails the response.
- **Manutenção PDV (PDV Maintenance)** — remote PDV parameterization, verification, reporting, status, close/restart.
- **PinPad** — sends commands to the PinPad device (direct, email, or tunnel).
- **MDM** — customer lookup, registration and update.
- **Configurações (Settings)** — visible tabs, destination emails, stores/PDVs, Oracle queries, Oracle connections, APIs, and (optionally) sensitive credentials via the Admin tab.

### Stack
Python 3, Flask, pywebview, `python-oracledb`, `openpyxl`, Pillow. Packaged with PyInstaller + Inno Setup.

### Running locally
Install the dependencies listed in `extrator_logs.spec` (Flask, pywebview, python-oracledb, openpyxl, Pillow, requests, etc.) and run:
```
python extrator_logs.py
```
Configuration lives in `properties/config.properties` (versioned) and `properties/secure.properties` (credentials, **not versioned**).

### Notice
Internal-use project. No real credentials should ever be committed — `secure.properties` is kept out of version control.
