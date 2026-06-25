# Documentação da Versão Atual - Extrator de Logs e Dados Oracle

**Data:** 28 de Novembro de 2025  
**Versão:** 1.0 Final

---

## 📋 Resumo das Funcionalidades Implementadas

### ✅ Extrator de Logs
- Envio de solicitações de logs por email
- Seleção de múltiplos arquivos de log
- Geração automática de PID para rastreamento
- Alerta de sucesso com PID na interface

### ✅ Extrator de Dados Oracle
- Conexão com banco Oracle 12c
- Suporte a múltiplas consultas com variáveis ($LOJA, $PDV, $NSU, $DATA)
- Exportação em CSV ou XLSX
- Separador decimal configurável (. ou ,)
- Compactação automática em ZIP para múltiplas consultas
- Filtros dinâmicos (Loja, PDV, NSU, Data)

### ✅ Interface
- Sistema de abas (Solicitar Logs, Exportar Dados Oracle, Configurações)
- Tela de configuração para gerenciar consultas e lojas/PDVs
- Design responsivo com largura de 910px

### ✅ Sistema de Logs
- Arquivo de log centralizado em `log/extrator_logs.log`
- Registros com data/hora, nível (DEBUG, INFO, WARNING, ERROR)
- Rastreamento completo de operações

---

## 📁 Estrutura de Arquivos Principais

### Backend

#### `extrator_logs.py` (v22264 bytes)
**Funcionalidades:**
- Servidor Flask com todas as rotas
- Integração com Oracle via oracledb
- Sistema de logging completo
- Leitura/escrita de properties
- Exportação para CSV e XLSX
- Compactação de múltiplos arquivos
- Tratamento de LOBs (Large Objects)

**Rotas principais:**
- `/` - Página principal com abas
- `/solicitar-logs` - Página de solicitar logs
- `/exportar-oracle` - Página de exportar dados
- `/config` - Página de configuração
- `/solicitar` (POST) - Submissão de solicitação de logs
- `/oracle_export` (POST) - Submissão de exportação Oracle

#### `logger.py`
**Funcionalidades:**
- Configuração centralizada de logging
- Output para arquivo e console
- Formato com data/hora e nível de severidade

#### `config.properties` (v2529 bytes)
**Conteúdo:**
- Configurações de conexão Oracle
- Credenciais de email
- Lojas e PDVs disponíveis
- Definições de consultas SQL com variáveis
- Emails de envio

**Exemplo de consulta:**
```properties
oracle_query.P2K_ITEM_TRANSACAO=select * from DBCSI_P2K.P2K_ITEM_TRANSACAO where data_transacao = $DATA and codigo_loja = $LOJA and NSU_TRANSACAO = $NSU and NUMERO_COMPONENTE = $PDV
```

---

### Frontend

#### `index.html` (v2423 bytes)
**Funcionalidades:**
- Página principal com sistema de abas
- Navegação entre: Solicitar Logs, Exportar Dados Oracle, Configurações
- Carregamento de sub-páginas via iframe
- CSS integrado para abas

#### `solicitar_logs.html` (v4258 bytes)
**Funcionalidades:**
- Formulário de solicitação de logs
- Dropdowns dinâmicos para loja e PDV
- Checkboxes para seleção múltipla de logs
- Envio via fetch com tratamento de resposta
- Validação de campos obrigatórios

#### `exportar_oracle.html` (v10715 bytes)
**Funcionalidades:**
- Filtros: Loja, PDV, NSU, Data
- Data pré-preenchida com data atual (DD/MM/YYYY)
- Seleção múltipla de consultas via checkboxes
- Radio buttons para formato (CSV/XLSX)
- Radio buttons para separador decimal (./,)
- Envio via fetch com múltiplos parâmetros
- Logs de debug no console do navegador

#### `config.html`
**Funcionalidades:**
- Formulário para configurar lojas
- Campos dinâmicos para PDVs por loja
- Configuração de arquivos de log
- Gerenciamento de consultas Oracle (adicionar/remover)
- Edição de SQL das consultas

#### `styles.css`
**Funcionalidades:**
- Design responsivo
- Max-width: 910px (30% maior que padrão)
- Estilos para formulários, botões, dropdowns
- Estilos para checkboxes e radio buttons
- Tema azul (#4a90e2) consistente

---

## 🔧 Dependências Python

```bash
pip install flask
pip install oracledb
pip install openpyxl
```

---

## 📊 Fluxo de Dados

### Solicitar Logs
1. Usuário acessa `/solicitar-logs`
2. Seleciona Loja, PDV, Logs e Email
3. Clica em "Solicitar Logs"
4. Flask envia email com PID
5. Alerta exibe PID para rastreamento
6. Registro salvo em `historico_envio_logs.csv`

### Exportar Dados Oracle
1. Usuário acessa `/exportar-oracle`
2. Seleciona Filtros (Loja, PDV, NSU, Data)
3. Escolhe Formato (CSV/XLSX)
4. Define Separador Decimal
5. Marca Consultas desejadas
6. Clica em "Exportar"
7. Backend executa queries com variáveis substituídas
8. Dados exportados em formato selecionado
9. Se múltiplas consultas: compacta em ZIP e remove originais
10. Arquivo salvo em `output/`

---

## 📝 Variáveis de Consulta Suportadas

- `$LOJA` - Código da loja (ex: 0019)
- `$PDV` - Número do PDV (ex: 192)
- `$NSU` - NSU da transação
- `$DATA` - Data no formato YYYYMMDD (convertida de DD/MM/YYYY)

**Exemplo de uso:**
```sql
SELECT * FROM transacoes 
WHERE loja_codigo = $LOJA 
AND pdv_numero = $PDV 
AND data_transacao = $DATA
```

---

## 📂 Estrutura de Pastas

```
C:\Users\odirl\OneDrive\Leroy\SELF_PDV\extrator_logs\
├── extrator_logs.py          # Servidor Flask principal
├── logger.py                 # Sistema de logging
├── config.properties         # Configurações
├── templates/
│   ├── index.html           # Página principal com abas
│   ├── solicitar_logs.html  # Formulário de logs
│   ├── exportar_oracle.html # Formulário de exportação
│   └── config.html          # Página de configuração
├── static/
│   └── styles.css           # Estilos CSS
├── log/
│   └── extrator_logs.log    # Arquivo de log
├── output/                  # Arquivos exportados (CSV, XLSX, ZIP)
└── Iniciar_Extrator_Logs.bat     # Atalho para iniciar
```

---

## 🚀 Como Executar

### Opção 1: Via Bat
```batch
Duplo clique em: Iniciar_Extrator_Logs.bat
```

### Opção 2: Via Python
```bash
cd C:\Users\odirl\OneDrive\Leroy\SELF_PDV\extrator_logs
python extrator_logs.py
```

### Acessar
```
http://localhost:5000
```

---

## 🔒 Segurança e Boas Práticas

- ✅ Credenciais Oracle armazenadas em arquivo properties
- ✅ Emails com autenticação Gmail SMTP
- ✅ Sanitização de entrada para consultas
- ✅ Tratamento robusto de erros
- ✅ Logs detalhados para auditoria
- ✅ Validação de campos obrigatórios
- ✅ Conversão segura de tipos de dados

---

## 📖 Recursos de Logging

Cada operação importante é registrada com:
- **Timestamp** - Data e hora exata
- **Level** - DEBUG, INFO, WARNING, ERROR
- **Module** - Nome do módulo (ExtratrorLogs)
- **Mensagem** - Descrição detalhada

Exemplo de log:
```
[2025-11-28 11:30:45] - INFO - ExtratrorLogs - Requisição de exportação Oracle recebida
[2025-11-28 11:30:45] - DEBUG - ExtratrorLogs - Consultas selecionadas: ['P2K_ITEM_TRANSACAO', 'P2K_CAB_TRANSACAO']
[2025-11-28 11:30:46] - INFO - ExtratrorLogs - Conexão Oracle estabelecida: qas100.lmbr.int.adeq.com
[2025-11-28 11:30:48] - INFO - ExtratrorLogs - Exportação concluída: output/P2K_ITEM_TRANSACAO_20251128113048.xlsx (1250 linhas)
[2025-11-28 11:30:49] - INFO - ExtratrorLogs - ZIP criado com sucesso: output/exportacao_20251128113049.zip
[2025-11-28 11:30:49] - DEBUG - ExtratrorLogs - Arquivo removido: output/P2K_ITEM_TRANSACAO_20251128113048.xlsx
```

---

## 🆘 Troubleshooting

### Erro: "Cannot convert LOB to Excel"
**Solução:** O código já trata LOBs automaticamente. Verificar se está usando a versão mais recente de `extrator_logs.py`

### Erro: "Consulta não encontrada"
**Solução:** Verificar se o nome da consulta está correto em `config.properties` e sem espaços extras

### Arquivo ZIP vazio
**Solução:** Verificar se as consultas retornam dados. Ver logs em `log/extrator_logs.log`

### Conexão Oracle recusada
**Solução:** Verificar credenciais em `config.properties` e disponibilidade do servidor Oracle

---

## 🎯 Próximos Passos Possíveis

- [ ] Dashboard com histórico de exportações
- [ ] Agendamento automático de consultas
- [ ] Notificações por email com arquivo anexado
- [ ] Autenticação de usuários
- [ ] Histórico com filtros avançados
- [ ] Integração com SFTP para envio de arquivos
- [ ] API REST para integração com outros sistemas

---

**Versão Final:** 1.0  
**Desenvolvido em:** Python 3.12, Flask, oracledb, openpyxl  
**Última atualização:** 28/11/2025 17:59
