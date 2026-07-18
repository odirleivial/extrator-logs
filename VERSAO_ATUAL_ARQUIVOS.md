# Documentação da Versão Atual - Backoffice Equipe QA

**Data:** 17 de Julho de 2026  
**Versão:** 2.17.1

---

## 🔢 Critério de Versionamento (MAJOR.MINOR.PATCH)

| Nível | Quando incrementar | Exemplo |
|-------|-------------------|---------|
| **MAJOR** | Mudança de interface, arquitetura ou quebra de compatibilidade | Janela própria, redesign completo |
| **MINOR** | Nova funcionalidade mantendo compatibilidade | Nova aba, novo tipo de exportação |
| **PATCH** | Correção de bug ou ajuste sem nova funcionalidade | Fix de crash, texto errado |

A versão é definida em `version.py` e propagada automaticamente para o footer e tela Sobre.

---

## 📋 Histórico de Versões

### 2.17.1 — 17/07/2026
- Aba MDM (Alterar): corrige a geração de endereço novo, que gerava dados de uma UF aleatória mesmo com outra UF selecionada (CEP/rua/cidade incoerentes). Agora usa a mesma base real de CEPs do cadastro e sorteia um endereço válido de acordo com a UF (e o tipo de CEP) escolhidos no card; o payload só é gerado após a base de CEPs estar carregada

### 2.17.0 — 17/07/2026
- Aba MDM (Cadastrar): novo preset **UF (Estado)** na seção laranja, exibido tanto para PF quanto PJ, com **SP** como padrão. Ao escolher uma UF, os campos que usam estado são pré-configurados com ela: UF do endereço (`addressProvince`) e UF da Inscrição Estadual de PF e PJ (`...StateRegistrationFederatedUnit`). UFs sem CEP na base (ex.: AC) não alteram o campo de endereço, apenas os de IE

### 2.16.0 — 17/07/2026
- Aba MDM (Cadastrar e Alterar): geração de **Inscrição Estadual válida para as 27 UFs** (26 estados + DF), seguindo as regras de dígito verificador do Sintegra. O algoritmo foi portado fielmente da biblioteca js-brasil e validado gerando 200 IEs por UF (todas passam na verificação de idempotência do validador)
- Os comboboxes de UF da Inscrição Estadual (PF e PJ), no cadastro e na alteração, passam a listar todas as 27 UFs (antes só SP, DF, TO, MT, MG, RO)

### 2.15.1 — 17/07/2026
- Aba MDM (Cadastrar): ajustes de alinhamento — botão **Copiar** alinhado com o campo de status (status com largura fixa) na lista de payloads, e rótulos dos presets (Isento de Inscrição Estadual / Filial) centralizados verticalmente com os radio buttons

### 2.15.0 — 17/07/2026
- Aba MDM (Alterar): campos passam a usar a **mesma interface do cadastro** — combobox Automático/Manual, selects com Automático etc. Quando o cliente tem o valor, ele é preenchido em modo **Manual**; quando não tem, o campo aparece igual ao cadastro (modo Automático, mesmas opções). Vale para os campos escalares e para os campos dos itens de lista (e-mail, telefone, endereço)
- **CPF, CNPJ, RNE e PASSAPORTE** ficam somente-leitura (não podem ser alterados nem removidos)
- Itens de lista agora têm uma **flag de envio por campo** (igual ao cadastro): campo com flag marcada e valor alterado gera `replace`/`add`; campo com flag desmarcada que existia gera `remove` do subcampo; em item novo, só os campos com flag marcada entram. Campos em modo Automático são gerados na hora (telefone, e-mail, nomes, datas, endereço coerente a partir da base de CEPs)

### 2.14.3 — 17/07/2026
- Aba MDM (Alterar): corrige erro 500 (`NullPointerException`) ao editar/remover endereços. Endereço não tem chave natural (o `identifier` é só um hash do sistema que a API não indexa), então a edição de endereços passou a usar **índice do array** no path (JSON Patch padrão): `replace /fields/addresses/<índice>/<subcampo>` e `remove /fields/addresses/<índice>` (remoções em ordem decrescente para não deslocar os índices). Telefone e e-mail continuam por chave natural (`/fields/phones/<número>`, `/fields/emailAddresses/<email>`)

### 2.14.2 — 17/07/2026
- Aba MDM (Alterar): UF (`province`) de endereços novos passa a vir com `SP` por padrão

### 2.14.1 — 17/07/2026
- Aba MDM (Alterar): endereços **novos** já vêm com `country: "BR"` e `postalCodeType: "logradouro"` preenchidos por padrão

### 2.14.0 — 17/07/2026
- **Nova sub-aba MDM "Alterar"** — edição de clientes existentes via JSON Patch:
  - Campo para digitar CPF ou CNPJ; o BEC busca o cliente no MDM (mesma consulta da aba Consultar) e monta um formulário idêntico ao de cadastro já preenchido com os dados retornados (`data.fields`)
  - Campos que **não** vieram do MDM aparecem com o valor padrão, a flag desmarcada e o rótulo em cinza; marcar a flag adiciona o campo (`add`), desmarcar um campo existente remove (`remove`) e alterar o valor gera `replace`
  - Só é renderizada a seção Pessoa Física ou Jurídica correspondente ao tipo do cliente consultado
  - Listas (e-mail, telefone, endereço): cada item existente vira um card editável (só com os campos que o MDM retornou), com opção **Manter** (desmarcar = remover o item) e possibilidade de **+ Adicionar** novos itens. Edição de item existente usa `replace` em `/fields/<lista>/<chave>` (telefone=número, e-mail=e-mail, endereço=identifier) enviando apenas os campos alterados; item novo usa `add` em `/fields/<lista>/-` (endereço com `value` em array, telefone/e-mail com `value` em objeto, seguindo os exemplos válidos)
  - CNAE (atividades econômicas) editável como lista de códigos com indicador principal
  - Botão **Gerar Payload de Alteração** compara o formulário com os dados originais e produz o JSON Patch (RFC 6902) com apenas as diferenças; **Enviar Alteração** faz `PATCH .../items/{id}?jsonPatch=true`
  - Histórico das alterações gravado em `log/patch_mdm_<data>.csv` (um arquivo por dia), separado do histórico de cadastros (`post_mdm_<data>.csv`)
- Nova rota backend `/mdm-atualizar` e função `atualizar_cliente_mdm` em `mdm.py`

### 2.13.1 — 17/07/2026
- Aba MDM (Cadastrar): cada linha da lista de payloads gerados agora tem um botão **Copiar**, que copia o `administrativeIdentifier` (CPF, RNE, Passaporte ou CNPJ) daquele cliente para a área de transferência (usa a Clipboard API com fallback via `execCommand` para navegadores/webviews sem suporte)

### 2.13.0 — 08/07/2026
- **Agente — email de Status PDV reestruturado:**
  - Colunas Online e StatusServer consolidadas na coluna **Online**: exibe `true` somente quando `indicaOnLine` e `StatusServer` são verdadeiros na última ocorrência do CSIDebugFile **e** o PDV está ligado (porta 4000 respondendo); caso contrário exibe `false`
  - Novo bloco **Serviços** no cabeçalho (acima de Lidos/Não lidos) com cards ONLINE/OFFLINE por serviço, verificados via conexão TCP (timeout 3s). Serviços iniciais: SiTef (`10.206.112.34:4096`) e Proctrans (`10.56.62.140:4003`)
  - Serviços configuráveis no `agent.properties` no formato `servico.<Nome>=host:porta` — basta adicionar novas linhas para monitorar mais serviços
- Pacote de instalação do agente gerado em `server_agent/dist/AgentExtratarLog_v2.13.0.zip` (exe + agent.properties + nssm + bats de serviço)

### 2.12.1 — 08/07/2026
- Corrige a parametrização do `ext.properties` que falhava em todos os PDVs com o erro `"ext.proparmperties.arquivo" não definido no agent.properties`: o nome do parâmetro estava digitado errado (`ext.proparmperties`) na chave `PARAMETROS_PDV` do `config.properties` e no fallback do `configurar_pdv.html`
- Remove item duplicado `parametrosGeraisPDV.properties` da lista `PARAMETROS_PDV` (gerava checkbox repetido na tela Configurar PDV)

### 2.12.0 — 02/07/2026
- Aba MDM (Cadastrar), Pessoa Jurídica: unificado o campo CNPJ em um único controle (Automático/Alfanumérico/Manual) — ao escolher Manual, abre um campo de texto para digitar o CNPJ que vai para o JSON (antes havia dois campos separados e redundantes)
- Novos presets na área laranja (PF/PJ), visíveis apenas para Pessoa Jurídica:
  - **Isento de Inscrição Estadual** (Sim/Não): Sim desmarca Data Última Verificação IE, Número IE, UF IE e Status IE, e marca o indicador de isenção como verdadeiro; Não faz o inverso (marca os 4 campos e o indicador como falso)
  - **Filial** (Sim/Não): Sim faz o 1º cliente do lote gerar um CNPJ de matriz e os demais clientes gerarem CNPJs de filial da mesma raiz (0002, 0003...); Não mantém o comportamento padrão (todo cliente com CNPJ de matriz independente)

### 2.10.0 — 01/07/2026
- Aba MDM (Cadastrar): cada envio de cadastro (POST) agora é registrado em `log/post_mdm_<data>.csv` (um arquivo por dia, criado automaticamente), com as colunas `data_hora`, `administrativeIdentifier`, `payload`, `status_code` e `retorno` (CSV separado por `;`, mesmo padrão já usado nas exportações Oracle)
- O histórico registra tanto envios bem-sucedidos (status HTTP + corpo da resposta) quanto falhas de rede (status `ERRO` + mensagem da exceção)

### 2.9.1 — 01/07/2026
- Corrige caracteres especiais corrompidos nos endereços gerados (ex: "MinistÃ©rio" em vez de "Ministério"): a conversão do CSV de referência para `static/data/base_de_ceps.json` estava lendo o arquivo como Latin-1 quando na verdade ele já é UTF-8, causando "double-encoding". Os 3.431 registros foram regravados corretamente

### 2.9.0 — 01/07/2026
- Diversidade de e-mails automáticos muito maior: pool de nomes/sobrenomes ampliado (~8.400 combinações de nome+sobrenome), sufixo numérico agora sempre presente (antes saía vazio em 50% dos casos) e checagem para nunca repetir um e-mail já gerado na mesma sessão
- Endereços automáticos passam a usar a base real de CEPs da aplicação de referência (3.431 registros reais, convertidos para `static/data/base_de_ceps.json` e servidos pelo Flask), em vez de listas fixas de exemplo
- CEP, rua, bairro, cidade, UF e código IBGE agora vêm sempre do mesmo registro real (nunca são sorteados de forma independente uns dos outros)
- Ao escolher manualmente uma UF, apenas endereços reais dessa UF são gerados (campo UF do endereço virou uma lista fechada com as 26 UFs presentes na base)
- Ao escolher manualmente um tipo de CEP, apenas endereços desse tipo são gerados (quando existir na UF escolhida — a UF tem prioridade sobre o tipo nos raros casos em que a combinação não existe na base real)
- Aplicadas as regras reais por tipo de CEP: `localidade` (sem rua/bairro específicos reais — usa um logradouro plausível), `caixaPostalComunitaria` (sem bairro/número/complemento) e `grandeUsuario`/`unidadeOperacional` (sem número/complemento)

### 2.8.2 — 01/07/2026
- Aba MDM (Cadastrar), seção Pessoa Física: CPF, Nome Completo, Data de Nascimento, Maior de 18 anos e Profissão agora vêm pré-selecionados por padrão
- Ao voltar para PF (depois de ter escolhido PJ), o campo CPF é marcado automaticamente, no mesmo padrão já aplicado ao CNPJ ao escolher PJ

### 2.8.1 — 01/07/2026
- Aba MDM (Cadastrar): a lista de payloads agora exibe o `administrativeIdentifier` (CPF/RNE/Passaporte ou CNPJ) de cada cliente gerado, em vez de "Cliente N"
- Adicionados os links "Selecionar todos" e "Limpar seleção" acima da lista de payloads (mantendo o comportamento padrão de já vir tudo selecionado após gerar)
- Ao escolher PF ou PJ, a seção correspondente ("Pessoa Física" ou "Pessoa Jurídica") passa a ser exibida antes das demais seções do formulário
- O campo CNPJ volta a vir pré-selecionado automaticamente ao escolher PJ (antes ficava desmarcado, pois a seleção inicial padrão é PF)
- Os campos "E-mail Social Login" e "Origem Social Login" não vêm mais pré-selecionados por padrão

### 2.8.0 — 01/07/2026
- Aba MDM (Cadastrar): "Gerar Payloads" agora exibe os clientes gerados como uma lista (checkbox de envio, tipo PF/PJ e status), com cada item expansível ao clicar para mostrar o JSON completo
- Novo botão "Enviar Selecionados": envia (POST) apenas os payloads marcados, um de cada vez, atualizando o status de cada item (código HTTP ou erro) assim que a resposta da API chega
- Nova rota `/mdm-cadastrar` e função `cadastrar_cliente_mdm()` no backend, usando as credenciais já configuradas em `secure.properties`
- Corrige o campo `socialLogin`: a API espera uma lista (`ArrayList`), mas o payload estava enviando um objeto único quando o e-mail de social login estava marcado, causando erro 400 ("JSON inválido... Cannot deserialize... ArrayList")

### 2.7.2 — 01/07/2026
- Nova área no topo da aba Cadastrar (MDM) para escolher o tipo de cliente (Pessoa Física ou Pessoa Jurídica) e a quantidade de clientes a gerar
- Selecionar PF exibe a seção "Pessoa Física (Inhabitant)" e oculta "Pessoa Jurídica (Professional Organization)", e vice-versa; os campos-mestre da seção oculta são desmarcados automaticamente para não vazarem no payload
- O botão "Gerar Payload" agora respeita a quantidade informada, gerando um array com N payloads (cada um com seus próprios valores automáticos)

### 2.7.1 — 01/07/2026
- Corrige a montagem do payload da aba MDM (Cadastrar): o "Gerar Payload" agora produz o JSON aninhado no formato real da API (`schema`/`fields`, blocos `inhabitant`/`professionalOrganization`, listas `emailAddresses`/`addresses`/`phones`, objetos de optin, `loyaltyProgram`, `leroyMerlinCreditCard`), com base nos exemplos reais de PF e PJ fornecidos
- Campos "Automático" agora geram valores reais (CPF, CNPJ, nomes, e-mails, telefones, inscrição estadual) via geradores portados de `util.py`, em vez de gravar a string "Automatico" no payload
- Endereço automático usa um gerador aproximado no navegador (a base real de CEPs só existe no servidor da aplicação de referência)

### 2.7.0 — 01/07/2026
- Nova aba **MDM** (Cadastro e Consulta de clientes na API MDM/Facade), com sub-abas "Cadastrar" e "Consultar"
- Tela de Cadastro espelha a planilha `mdm_payloads.xlsm` (aplicação de referência), com checkbox de envio por campo
- Blocos em destaque com flag + quantidade para Telefone, E-mail e Endereço (campos tipo lista)
- Sub-grupos de Optin (Endereço, Telefone, SMS, Push, WhatsApp, E-mail) com auto-seleção ao marcar o bloco correspondente
- Grupo LoyaltyProgram (não é lista, possui flag único)
- Campos com opção Automático/Manual, preparados para preenchimento automático futuro (baseado nas funções de `util.py` da aplicação de referência)
- Dados de conexão com a API MDM adicionados em `secure.properties` (`mdm_api_apikey`, `mdm_api_url`, `mdm_api_schema`)
- Consulta de cliente (por CPF/CNPJ) já funcional via API Facade; envio de cadastro (POST) fica para uma próxima etapa

### 2.0.0 — 27/06/2026
- Aplicação migrada para janela própria (pywebview), fora do navegador
- Redesign completo com identidade visual dos e-mails do Agent (azul `#1e3a5f`)
- Header com logo, subtítulo e botão Sobre
- Abas horizontais com indicador de aba ativa
- Footer com versão dinâmica
- Tela "Sobre" com renderização do `VERSAO_ATUAL_ARQUIVOS.md`
- Sistema de versionamento MAJOR.MINOR.PATCH via `version.py`
- Endpoint `/api/versao` para consulta programática

### 1.0.0 — 28/11/2025
- Versão inicial: Solicitar Logs, Exportar Oracle, Configurações
- Interface via navegador (Flask + HTML)

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
