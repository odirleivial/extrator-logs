# Documentação da Versão Atual - Backoffice Equipe QA

**Data:** 17 de Agosto de 2026  
**Versão:** 2.45.0

---

## 🔢 Critério de Versionamento (MAJOR.MINOR.PATCH)

| Nível | Quando incrementar | Exemplo |
|-------|-------------------|---------|
| **MAJOR** | Mudança de interface, arquitetura ou quebra de compatibilidade | Janela própria, redesign completo |
| **MINOR** | Nova funcionalidade mantendo compatibilidade | Nova aba, novo tipo de exportação |
| **PATCH** | Correção de bug ou ajuste sem nova funcionalidade | Fix de crash, texto errado |

A versão é definida em `version.py` e propagada automaticamente para o footer e tela Sobre.

---

## 📦 Como gerar o pacote de instalação do agente

O BEC distribui o Agent Extrator de Log pela aba **Administrador → Atualizar Agente**,
que envia por e-mail o arquivo `AgentExtratarLog_instalacao.zip` da raiz do projeto.
Para regerar esse pacote depois de alterar `server_agent/agent_extrator_log.py`:

```
powershell -ExecutionPolicy Bypass -File scripts\gerar_pacote_agente.ps1
```

O script compila o exe com PyInstaller, copia para `server_agent\` e monta o ZIP.
Use `-SemCompilar` para apenas remontar o ZIP com o exe já compilado.

Regras que o pacote precisa respeitar:

| Regra | Motivo |
|-------|--------|
| ZIP **plano**, sem subpastas | `atualizacao_agente.py` não inspeciona ZIPs aninhados, e o `instalar_servico.bat` espera os arquivos ao lado do exe |
| Exatamente estes 7 arquivos | `agent.properties`, `agent_extrator_log.exe`, `nssm.exe`, `iniciar_servico.bat`, `instalar_servico.bat`, `parar_servico.bat`, `remover_servico.bat` |
| Versão em `server_agent/version.py` atualizada | É a versão que o agente informa no e-mail de resultado da atualização, confirmando qual build ficou em execução |

No envio, o BEC renomeia as entradas `.exe` dentro do ZIP (o Gmail recusa esse tipo
de anexo) e o agente desfaz a renomeação ao aplicar a atualização.

> O fonte do relay está versionado em `cloudflare_worker/worker.js`, com as
> instruções de deploy e a lista de endpoints no `README.md` da mesma pasta.

---

## 📡 Canais de comunicação BEC → agentes

| Funcionalidade | Flag | Canal |
|---|---|---|
| PinPad | `pinpad_modo_comunicacao` | relay |
| Solicitar Logs (lojas de PDV) | `logs_modo_comunicacao` | relay — resposta por e-mail **ou download via R2**, escolhido no botão |
| Manutenção PDV (6 funcionalidades) | `pdv_modo_comunicacao` | relay |
| Registro de Execução (trilha de auditoria) | `registro_modo_comunicacao` | relay |
| Atualizar Agente — **só o Agent Extrator** | `atualizacao_modo_comunicacao` | relay (limite 18 MB) |
| **Solicitar Logs do Server Agent SP** | — | **e-mail** (rede de SP não alcança o relay) |
| **Atualizar o Server Agent SP** | — | **e-mail** (mesma razão) |

As **respostas** dos agentes ao solicitante continuam sempre por e-mail — o relay
transporta apenas o pedido. Padrão de fábrica das cinco flags: `tunnel`.

Com isso o Agent Extrator não recebe mais nada por e-mail; só o Server Agent SP
continua dependendo da caixa, porque a rede de SP não alcança o relay.

Na atualização, o instalador **preserva o valor que a máquina já tem** e só aplica
`tunnel` quando a chave ainda não existe lá (`MergeProperties` no `setup.iss`:
chave com linha ativa na máquina vence; ausente recebe o valor do build). Uma
máquina que veio da 2.41.0 com `logs_modo_comunicacao=email` continua em `email`
até ser trocado pela interface.

---

## 📡 Fila do relay — defeitos corrigidos no Worker

Levantados em 17/08/2026 por sondagem e depois confirmados no fonte, quando ele
foi recuperado do painel da Cloudflare e versionado:

| Defeito | Como era | Como ficou |
|---|---|---|
| **Fila sobrescrevia** | uma chave `cmd:<loja>:<pdv>`; o segundo `POST /comando` apagava o pedido anterior sem erro | uma chave **por item** (`cmd:<loja>:<pdv>:<ts>:<rnd>`), entregue na ordem de chegada |
| **Ack apagava o item errado** | `POST /resultado/<pid>` limpava a chave do comando qualquer que fosse o item ali | o PID aponta para a chave exata; só aquele item é removido |
| **Item lento travava a fila** | o `GET /pendente` devolvia sempre o mesmo item até ele ser respondido | reserva de entrega (10 min): o próximo poll passa ao item seguinte |
| **Tudo expirava em 2 min** | agente offline por mais de 2 min perdia o pedido; o BEC tinha 2 min para ler um resultado | 15 min para comandos e resultados; PinPad segue em 2 min, porque comando velho de PinPad não serve |
| **Cota do KV estourada** | `KV.list()` a cada poll: ~43.200/dia contra 1.000 do plano gratuito | fila indexada em uma chave; poll ocioso custa 1 leitura e nenhuma listagem |
| **Relay aberto sem TOKEN** | `if (token && ...)` — sem a variável, servia a internet inteira | sem `TOKEN` configurado recusa tudo com 503 |

O contrato HTTP não mudou, então BEC e agentes já instalados funcionam com as duas
versões — a troca não precisa ser sincronizada.

Como conferir: `python scripts\conferir_worker.py` antes de subir e
`python scripts\testar_worker_relay.py` depois. O segundo **reprova a versão
antiga** (enfileira três pedidos e cobra que os três sejam entregues), então serve
para confirmar que o deploy pegou.

Limitação que permanece: o KV é eventualmente consistente, então um item recém
gravado pode levar um instante para aparecer na listagem — o agente pega no ciclo
seguinte de 2 s. Consistência forte exigiria migrar a fila para Durable Objects.

---

## 📋 Histórico de Versões

### 2.45.0 — 18/08/2026
- **Solicitar Logs ganhou o botão Download**, no mesmo padrão do Exportar Oracle: dois
  botões, **Enviar por E-mail** (o fluxo de sempre, com o ZIP anexado) e **Download**
  (o agente sobe o arquivo e o BEC o entrega ao navegador)
- O arquivo trafega pelo **R2**, não pelo KV. O KV tem teto de 25 MB por objeto e exige
  base64 (+33%); o R2 aceita binário puro, tem 10 GB no plano gratuito e não impõe esse
  teto. Isso importa porque as solicitações **históricas** compactam a pasta `debug_P2K`
  do dia inteiro, de tamanho imprevisível — era o caso que ficava sem margem
- Fluxo do download: o BEC enfileira com `Entrega: download` → o agente extrai, sobe o
  ZIP em `PUT /arquivo/<pid>` e publica no resultado o **nome, tamanho, sha256 e o resumo
  de arquivos incluídos/faltando** → a tela consulta `/solicitar/status/<pid>` de 3 em 3
  segundos → quando fica pronto, baixa por `/solicitar/baixar/<pid>`
- O BEC **apaga o objeto no R2 depois de baixar**, então o bucket não acumula
- O resultado do relay é consumido na leitura, então o BEC **memoriza** o status por PID:
  o polling pode consultar quantas vezes precisar sem perder a informação
- O resumo por arquivo, que no e-mail ia no HTML, aparece agora na própria tela
  ("3 incluído(s), 1 não encontrado(s)")
- O botão Download exige o modo Tunnel e **não existe para o `SERVERS_EP_SP`**, que não
  alcança o relay — a tela recusa com mensagem explicando
- Sem o campo `Entrega`, o agente mantém exatamente o comportamento histórico (e-mail),
  então um agente que receba um pedido antigo não muda de conduta
- Novo `scripts/testar_download_logs.py`: valida o ramo de entrega dentro do agente
  **sem rede** (confere que o download sobe o arquivo e não manda e-mail, e que a
  ausência de `Entrega` mantém o e-mail) e, com o worker publicado, o ciclo completo até
  o arquivo chegar byte a byte igual
- **Requer configuração nova na Cloudflare:** bucket `bec-relay-arquivos` e binding `R2`.
  Sem o binding, as rotas `/arquivo/*` respondem 503 dizendo o que falta e o resto do
  relay segue funcionando
- Agente Extrator em v1.7.0

### 2.44.1 — 18/08/2026
- **Corrige o estouro da cota do Cloudflare KV, que derrubou o relay.** O worker
  publicado na 2.44.0 chamava `KV.list()` a cada `GET /pendente`. Com o agente
  buscando a cada 2s são ~43.200 listagens por dia contra as **1.000** do plano
  gratuito: a cota estourava em cerca de uma hora e `/pendente` e `/fila` passavam a
  responder **erro 1101** (o `/status` seguia de pé, porque não toca no KV)
- **A fila deixou de ser varrida e passou a ser indexada.** Uma chave por agente
  (`fila:<loja>:<pdv>`) guarda a lista de pendências, então o poll ocioso — a
  esmagadora maioria — custa **uma leitura e nenhuma escrita**. Não existe mais
  nenhum `KV.list()` no worker, nem no endpoint de diagnóstico
- A reserva de entrega passou para dentro da entrada do índice, então entregar um
  item não gasta escrita extra. O corpo de cada pedido continua em chave própria: o
  pacote de atualização não é reserializado a cada mexida na fila
- **Janela de atendimento no agente** (`polling_janela`, `polling_dias`): fora dela o
  agente **não chama o relay**, que é o que de fato reduz o consumo. Aceita janela
  cruzando a meia-noite (`22:00-06:00`) e cai para 24h se a configuração for inválida
- **Intervalo adaptativo** (`polling_intervalo_seg`, `polling_intervalo_ocioso_seg`,
  `polling_ocioso_apos_seg`): 2s logo após receber trabalho, para quem está testando
  não sentir diferença, e 15s quando não há movimento
- Efeito somado, com janela 07:00–20:00 em dias úteis: de **43.200 listagens/dia
  (estourando a cota)** para **~3.100 leituras/dia, 3% da cota** — cabem cerca de 30
  agentes nesse ritmo. Só o intervalo ocioso, sem janela, já leva a 5.760/dia
- Como o `agent.properties` é preservado na atualização, um agente que já está em
  campo sobe sem as chaves novas e usa os **padrões do código**: 24h de janela e 15s
  de intervalo ocioso, que sozinhos já cortam o consumo em 7,5x. A janela de horário
  exige editar o `agent.properties` da máquina
- `scripts/conferir_worker.py` passa a cobrar que não exista `KV.list()` e que o
  caminho ocioso saia com uma leitura, sem escrita
- Agente Extrator em v1.6.0

### 2.44.0 — 17/08/2026
- **Atualização do agente pelo relay.** Nova flag **Atualizar Agente**
  (`atualizacao_modo_comunicacao`). O pacote vai em **base64** dentro do payload, e o
  resultado da instalação continua chegando por e-mail — enviado pela nova versão do
  agente depois de reiniciar, como sempre foi
- **Só para o Agent Extrator.** O Server Agent SP fica de fora por dois motivos somados:
  a rede de SP não alcança o relay, e a fila é endereçada por `bec_loja`/`bec_pdv`, que é
  a do extrator — publicar o pacote do SP ali entregaria o build errado. A restrição é
  explícita em `AGENTES_COM_RELAY`, não implícita na configuração
- **Limite de 18 MB no modo relay**, contra 25 MB no e-mail: o corpo JSON precisa ficar
  abaixo de ~25 MB (limite de valor do KV) e o base64 ocupa 1/3 a mais. Medido contra o
  Worker em produção — 18 MB de binário (24,0 MB de JSON) passa, 19 MB (25,3 MB) é
  recusado com HTTP 500. O pacote atual, de 8,4 MB, usa menos da metade da margem
- **Sem a neutralização de executáveis** no caminho do relay: renomear `.exe` para
  `.exe.becpkg` existe só para driblar o filtro de anexo do Gmail, que não está no
  caminho. O ZIP segue exatamente como o usuário escolheu
- **A autorização passa a ser o token do relay.** A checagem de remetente do e-mail não
  se aplica: só quem tem o token consegue publicar na fila do agente
- **O agente responde ao relay ANTES de aplicar a atualização.** É o único tipo assim
  (`ack_antes`): aplicar significa parar o serviço e trocar o executável, então o
  processo morre no meio e a resposta nunca sairia. Sem esse ack o item ficaria preso no
  relay e voltaria a cada poll depois do reinício — e a reserva de PID, que é em memória,
  se perde no restart. A proteção final continua sendo o `atualizacoes_aplicadas.txt`,
  gravado antes de disparar o script
- Também aqui o BEC **não** envia `[Registro Execucao]` no caminho do relay: seria um
  segundo item na mesma fila, com o mesmo PID, sobrescrevendo o pacote — o mesmo defeito
  corrigido no PinPad na 2.43.1
- `processar_atualizacao` ganhou o parâmetro `dados_zip`: quando presente, grava o pacote
  a partir dos bytes recebidos em vez de extrair o anexo. Conferência de tamanho, SHA256,
  extração e checagem do executável seguem idênticas nos dois canais
- Agente Extrator em v1.5.0

### 2.43.1 — 17/08/2026
- **Corrige o PinPad em modo relay, que não gravava usuário/PID no log do agente nem
  aparecia na trilha de ações.** Eram duas causas somadas:
  - o payload do comando **não levava o campo `usuario`**, então o agente não tinha o
    dado para carimbar as linhas nem para registrar a ação. As linhas saíam como
    `- [-] - [-] -`
  - o ramo do PinPad no polling era o único tratado inline no laço e **não chamava
    `definir_contexto` nem `registrar_acao_usuario`** — os demais tipos passam por
    `_tratar_item_relay`, que já fazia as duas coisas
- **Elimina um envio duplo que fazia o pedido se perder:** o BEC enfileirava o comando
  do PinPad e, logo depois, o registro de execução com o **mesmo PID**. Como o relay
  guarda um item só por loja/PDV, o registro sobrescrevia o comando; e o ack do comando
  apagava o registro, porque o resultado não é casado por PID. O `registrar_execucao`
  foi removido do caminho tunnel do PinPad — quem registra a ação agora é o agente, ao
  consumir o comando, como já acontece nas demais funcionalidades pelo relay
- Verificado que nenhuma outra funcionalidade tinha o problema: todas as demais passam
  por `_tratar_item_relay` (contexto + trilha) e fazem um único envio à fila
- Novos testes cobrindo a regressão e a varredura dos tipos em `scripts/testar_relay.py`
- Agente Extrator em v1.4.1

### 2.43.0 — 17/08/2026
- **Registro de Execução pelo relay.** Nova flag **Registro de Execução**
  (`registro_modo_comunicacao`) na tela de Configurações. É a trilha de auditoria das
  funcionalidades que o BEC executa sem agente nenhum (Exportar Oracle, Requisição API,
  MDM, PinPad direto, Solicitar Logs SP, Atualizar Agente)
- O `execucao.py` recebe o canal do relay por um **hook** (`definir_canal_relay`),
  instalado pelo `extrator_logs` na subida — importar o `extrator_logs` de dentro do
  `execucao` fecharia um ciclo, já que o primeiro importa o segundo. O envio segue em
  thread separada, então registrar nunca atrasa a operação do usuário
- No agente, o item é despachado para o mesmo `processar_registro_execucao` do e-mail.
  Duas particularidades desse tipo, ambas tratadas no despachante:
  - **o próprio handler registra a ação na trilha** (o nome vem do corpo, não do tipo),
    então o despachante não pré-registra — se registrasse, gravaria o nome errado e a
    dedução por PID engoliria o registro correto que viria em seguida
  - **não pega o lock** das operações pesadas: só acrescenta uma linha em arquivo, e
    ficar atrás de uma extração de minutos o atrasaria sem motivo
- `_TIPOS_RELAY` passou a descrever também a **assinatura** do handler
  (`completo`/`props`/`simples`), já que `processar_registro_execucao` recebe três
  argumentos enquanto os demais recebem quatro ou seis
- **Sem fallback para e-mail quando o relay falha**, de propósito: o modo existe para
  não usar e-mail. A operação do usuário não é afetada, mas o registro se perde — o
  log traz o aviso explícito, e a tela de Configurações avisa disso
- Agente Extrator em v1.4.0

### 2.42.0 — 17/08/2026
- **Manutenção PDV pelo relay.** Nova flag **Manutenção PDV** (`pdv_modo_comunicacao`)
  em Configurações → *Modo de comunicação com o Agente*, cobrindo as seis
  funcionalidades de uma vez: Parametrização PDV, Verificar Parametrização,
  Relatório Parametrização, Status PDV, Fechar PDV e Reiniciar PDV
- Com isso, **os únicos envios do BEC por e-mail passam a ser os logs do Server Agent SP
  e a atualização de versão dos agentes**
- O roteamento é feito **pelo assunto** que cada funcionalidade já montava
  (`_TIPOS_RELAY_POR_ASSUNTO`), então nenhuma das seis precisou ter o corpo alterado:
  elas recebem `enviar_ao_agente` no lugar de `enviar_email_gmail` e não sabem qual
  canal está em uso. O corpo é **byte a byte o mesmo** nos dois canais
- O payload passa a levar o campo `corpo` (formato chave/valor idêntico ao do e-mail),
  e o agente entrega esse corpo ao **mesmo handler** que atende o e-mail equivalente —
  inclusive o e-mail de resposta ao solicitante sai idêntico. Os campos soltos da
  solicitação de log continuam sendo enviados, para um agente ainda na v1.2.0
- `imap.store` trocado por `_marcar_lido(imap, num)` nos 14 pontos dos handlers: no
  caminho do relay não existe mensagem para marcar como lida, e o helper vira no-op
- O PID do payload é o **mesmo** que a tela mostrou (extraído do corpo, não gerado de
  novo), senão a trilha de ações registraria um PID diferente do informado ao usuário
- Tudo que mexe em PDV pelo relay é serializado pelo mesmo lock e roda em thread
  própria, com a guarda de reapresentação por PID introduzida na 2.41.0
- `scripts/testar_solicitacao_log_relay.py` renomeado para **`scripts/testar_relay.py`**
  e ampliado: exercita as 6 rotas novas, confere que o agente resolve cada tipo para o
  handler certo, que o nome da funcionalidade na trilha é igual pelos dois canais, e
  que com a flag em `email` nada vai para o relay
- Agente Extrator em v1.3.0

### 2.41.0 — 17/08/2026
- **Solicitar Logs via relay (Cloudflare Worker), sem e-mail no pedido.** Nova flag
  **Solicitar Logs** em Configurações → *Modo de comunicação com o Agente*, no mesmo
  padrão da flag do PinPad (E-mail | Cloudflare Tunnel). No modo relay o BEC enfileira
  a solicitação por HTTPS e **a resposta com o zip dos logs continua sendo por e-mail**
- Implementado **apenas para o Agente Extrator**. `SERVERS_EP_SP` e `linx-webservices`
  continuam por e-mail — a rede de SP não alcança o relay (verificado em 17/08/2026:
  `ConnectFailure` na máquina do agente SP, enquanto o SMTP do Gmail funciona)
- O payload leva `tipo=solicitacao_log`, PID, **usuário do Windows**, destino, loja, PDV,
  lista de logs e data. O agente reconstrói o corpo chave/valor e reaproveita
  integralmente o `processar_solicitacao_log`, então histórico, pastas, MFDE e o HTML
  do e-mail de resposta seguem idênticos ao fluxo por e-mail
- **Registro da ação na trilha por usuário** (`log/acoes_usuarios.log`) feito pelo próprio
  agente ao consumir a fila, com usuário do Windows e PID — no modo relay não existe
  e-mail para o agente classificar. O BEC **não** envia `[Registro Execucao]` nesse caso,
  o que anularia o propósito do modo. A garantia de uma linha por PID continua valendo
- Contexto de log (`usuario`/`PID`) é thread-local, então as linhas da extração saem
  carimbadas com quem pediu, sem se misturar ao polling que roda em paralelo
- **Polling único para todas as funcionalidades:** a thread sobe quando `pinpad_modo_comunicacao`
  **ou** `logs_modo_comunicacao` está em `tunnel`, e o item da fila é despachado pelo campo
  `tipo` (ausente = PinPad, preservando o comportamento anterior)
- Extração roda em thread própria (leva minutos entre SMB e zip) para não travar o polling,
  e é serializada por lock — duas extrações simultâneas competiriam pela mesma rede e disco
- **Guarda contra reapresentação do mesmo pedido:** o relay só descarta o item pendente
  quando recebe o `POST /resultado/<pid>` — o `GET /pendente` apenas lê. Como a extração
  demora, o mesmo pedido reaparece em todos os polls de 2s; sem essa guarda cada ciclo
  dispararia uma nova extração e um novo e-mail. O agente reserva o PID enquanto trata e
  só libera depois de responder, e o envio do resultado tem 3 tentativas
- Padrão de fábrica é `logs_modo_comunicacao=email` — o modo relay é ativado pela interface
- Novo script de teste do relay (hoje `scripts/testar_relay.py`), que exercita a rota do
  BEC com fila fictícia (9999/999), confere o payload no relay, a reconstrução do corpo
  no agente e a guarda de reapresentação, sem tocar em nenhum PDV real
- Agente Extrator em v1.2.0

### 2.40.2 — 14/08/2026
- Mesma correção da 2.40.1 aplicada aos outros dois e-mails de parametrização, que usavam o mesmo padrão de estilo repetido por célula e cresciam do mesmo jeito: **Relatório Parametrização** e **Parametrização PDV**
- **Nenhuma coluna foi alterada** — só a forma de aplicar o estilo. O Relatório mantém o cabeçalho por PDV (PDV, IP, versão e os badges OK/DIV/ERR) com as linhas de parâmetro e status; o Parametrização PDV mantém as quatro colunas Parâmetro, Esperado, Atual e Status, com a constante como sublinha do parâmetro
- Tamanhos após a correção, na seleção de 12 PDVs: Relatório Parametrização em **28,1 KB** e Parametrização PDV (um e-mail por PDV) em **7,5 KB** — ambos com folga larga para o limite de ~102 KB do Gmail
- Agente Extrator em v1.1.2

### 2.40.1 — 14/08/2026
- **Corrige o e-mail "Verificar Parametrização" chegando cortado.** Com 12 PDVs o HTML gerado tinha 124 KB e o Gmail **corta a exibição de mensagens acima de ~102 KB** — o e-mail saía completo do agente (confirmado no `.eml`: todos os PDVs presentes e HTML terminando em `</html>`), mas o Gmail parava de exibir no meio do PDV 277, escondendo o restante
- Os estilos repetidos em cada célula respondiam por **56% do arquivo** (71 KB em 749 atributos `style`). Passaram para classes CSS num bloco `<style>`, mantendo o visual idêntico
- Resultado: **124 KB → 57,6 KB** para a mesma seleção de 12 PDVs. O limite de PDVs exibidos por inteiro subiu de ~9 para **21**
- Agente Extrator em v1.1.1

### 2.40.0 — 14/08/2026
- **Agente Extrator — usuário e PID em cada linha do log.** O formato passou de `2026-08-14 12:09:26 [INFO] mensagem` para `2026-08-14 12:09:26 - [USUARIO] - [PID] - [INFO] mensagem`, com o usuário do Windows e o PID lidos do corpo do e-mail em tratamento. Linhas fora do contexto de um e-mail (subida do agente, polling do PinPad) usam `-` nos dois campos
- O contexto é por thread, então o polling do PinPad, que roda em paralelo, nunca carimba o usuário/PID de um e-mail sendo tratado pela thread principal
- **Nova trilha de ações por usuário — `log/acoes_usuarios.log`.** Arquivo cumulativo (sem rotação diária) com uma linha por ação, no formato `2026-08-14 11:33:00 - [odirl] - [HvRiORiQcj] - [Requisição API]`. Registra data/hora, usuário, PID e funcionalidade
- Garantia de **uma única linha por PID**: o agente carrega os PIDs já gravados na subida e ignora repetições. Isso cobre o caso em que o BEC envia dois e-mails para a mesma ação com o mesmo PID (ex.: Atualizar Agente, que gera `[Atualizacao Agente]` e `[Registro Execucao]`) e também o reprocessamento de um e-mail relido após reinício
- **Novo e-mail tratado: `[Registro Execucao]`.** As funcionalidades que o BEC executa sozinho (Exportar Oracle, Requisição API, MDM, PinPad em modo direto) não passam por agente nenhum; o BEC avisa por esse e-mail, e o agente registra a ação na trilha e marca a mensagem como lida. A data/hora usada é a do corpo (`DataHora`), que é quando o BEC de fato executou
- Agente Extrator em v1.1.0

### 2.34.3 — 06/08/2026
- `LEIA-ME.txt` do agente SP atualizado: a lista de logs passou a ser agrupada por servidor (14 logs, indicando quais também atendem datas anteriores) e ganhou a seção descrevendo o `portal-big-retail` e o `communication-big-retail`

### 2.34.2 — 06/08/2026
- **Dois novos logs do SERVERS_EP_SP, no servidor 10.56.62.152** (`\linx-wildfly\standalone\data\portal-big-retail\logs`), disponíveis na tela Solicitar Logs: **`portal-big-retail`** e **`communication-big-retail`** (este último na subpasta `communication`)
- Nos dois, o arquivo do dia tem nome fixo (`portal-big-retail.log`, `communication.log`) e os de dias anteriores são zips rotacionados na mesma pasta (`portal-big-retail_2026-08-02.0.zip`, `communication_2026-08-02.0.zip`) — configurados com o curinga `(xxx)`, que traz todos os arquivos daquele dia
- Alteração apenas de configuração: `agent.properties` do agente SP e chave `logs_sp` do `config.properties`. Nenhuma mudança no código dos agentes

### 2.34.1 — 06/08/2026
- **Dois novos logs do SERVERS_EP_SP** (10.56.62.140, em `\linx-tesouraria\logsTesouraria`), disponíveis na tela Solicitar Logs: **`TesourariaDebugFile`** (`TesourariaDebugFile.txt`) e **`tesourariaJava`** (`tesourariaJava.log`)
- Nos dois, o arquivo do dia não tem data no nome e os de dias anteriores levam a data como sufixo **depois da extensão**, no formato `dd-mm-yyyy` — `TesourariaDebugFile.txt.24-07-2026`, `tesourariaJava.log.17-07-2026`. Configurados como `{TesourariaDebugFile.txt}.[dd-mm-yyyy]` e `{tesourariaJava.log}.[dd-mm-yyyy]`
- **Agente SP — o `caminho` também aceita os tokens de data**, não só o `formato`. Serve para logs em que a data está na pasta e não no nome do arquivo (ex.: `...\logsTesouraria\[yyyymmdd]`). Nenhum caminho já configurado usa tokens, então o comportamento atual não muda
- Agente SP em v2.3.0 (PowerShell) e v1.4.0 (Python)

### 2.33.0 — 06/08/2026
- **Novo log do SERVERS_EP_SP — `logsTesouraria`** (10.56.62.140), disponível na tela Solicitar Logs (chave `logs_sp`). Ele muda de forma conforme a data pedida: no **dia corrente** é a pasta do dia (`\linx-tesouraria\logsTesouraria\<yyyymmdd>`), que vai compactada no anexo; em **dias anteriores** o próprio servidor já zipou a pasta, e o agente busca o arquivo `logsTesouraria_<yyyymmdd>.zip` na pasta acima
- **Agente SP — `log.<nome>.tipo` (arquivo | pasta) também na configuração do dia corrente.** Antes o `tipo` só existia no histórico e o dia corrente sempre assumia arquivo, o que impedia compactar uma pasta do dia. Chave opcional: sem ela o comportamento continua sendo `arquivo`
- **Resolvedor de formato unificado:** o dia corrente e o histórico passam a usar o mesmo resolvedor, então `(xxx)` e a classificação por conteúdo (`[..]`/`{..}` equivalentes) valem também para `log.<nome>.formato`. Antes o dia corrente reconhecia só `{fixo}`/`[data]` pelo delimitador — configurar `{yyyymmdd}` numa chave e `[yyyymmdd]` na outra dava resultados diferentes. Verificado que os seis formatos já em uso resolvem exatamente igual nos dois resolvedores
- **Pastas no anexo vão sob o nome do log** (`logsTesouraria/20260806/…`, `ProcTrans_CSIDebugFile/20260803/…`), igual ao que já acontecia com os arquivos. Antes entravam na raiz do zip com o nome da pasta, que é só a data — não dava para saber de qual log cada uma veio
- Agente SP em v2.2.0 (PowerShell) e v1.3.0 (Python)

### 2.32.1 — 04/08/2026
- **Instalador — configurações do projeto voltam a chegar nas máquinas:** o merge do `config.properties` só adicionava chaves novas, então qualquer alteração de **valor** feita em `properties/config.properties` (lojas, logs, `logs_sp`, consultas Oracle, APIs) era descartada na atualização, pois a chave já existia na máquina
- Agora as chaves de **catálogo** — `stores`, `*_pdvs`, `logs`, `logs_sp`, `ignorar_lojas`, `emails_destino`, `PARAMETROS_PDV`, `oracle_query_names`, `oracle_query.*`, `api_order`, `api.*` — são sempre atualizadas com o conteúdo do build (inclusive removendo consultas/APIs excluídas do projeto), enquanto as chaves específicas da máquina (`pinpad_*`, `bec_loja`, `bec_pdv`, `modo_instalacao`, `bec_tunnel_url`, `log.*`, `tab.*`) continuam preservadas
- O arquivo resultante passa a seguir a ordem e os comentários de seção do build; chaves locais que não existem no build são mantidas num bloco no final, e um backup `config.properties.bkp` é gravado antes de cada atualização
- Removida a chave duplicada `SERVERs_EP_SP_pdvs` (grafia divergente de `SERVERS_EP_SP_pdvs`) do `config.properties`

### 2.32.0 — 04/08/2026
- **Server Agent SP — consulta a logs históricos (dias anteriores):** o agente SP passa a tratar o campo `Data: dd/mm/yyyy` do e-mail da mesma forma que o agente de PDV. Data de hoje (ou ausente) extrai os logs do dia como sempre; data anterior monta os caminhos daquele dia pela nova configuração `historico.<log>.*`; data futura ou inválida cai no dia atual com aviso no log
- Nova configuração por log no `agent.properties` do agente SP: `historico.<nome>.caminho` (pasta base), `.formato` (nome do arquivo **ou** da pasta) e `.tipo` (`arquivo` ou `pasta`), cobrindo os 8 logs do padrão de servidores SP
- No formato, `(xxx)` é **curinga**: casa com qualquer texto e traz todos os arquivos do dia de uma vez — é o que resolve os sufixos de rotação do wildfly e do communication (`linx-webservices_2026-08-03.0.zip`, `.1.zip`, …). `[..]` e `{..}` são classificados pelo **conteúdo** (só marcadores de data = data, caso contrário texto fixo), então tanto faz qual dos dois a configuração usar
- Logs do tipo `pasta` (a pasta diária `debug_P2K\<yyyymmdd>`) têm a pasta inteira compactada no anexo. Quando vários logs apontam para a mesma pasta — `ProcTrans_CSIDebugFile`, `...RT` e `...SO` — ela é incluída **uma única vez**, e os três aparecem como incluídos no e-mail
- E-mail de resposta do agente SP: o card da data fica destacado em âmbar com o rótulo "Data (histórico)" nas consultas retroativas, e o anexo recebe nome próprio (`LOG-SP-HIST<yyyymmdd>-<timestamp>.zip`)
- `testar_agente.bat` passa a validar também os caminhos históricos (usando a data de ontem) e a listar quais logs só atendem à data de hoje
- A mesma lógica foi portada para a versão Python do agente SP (`server_agent_sp.py` v1.2.0), mantida para redes sem a restrição do EDR

### 2.31.1 — 03/08/2026
- Solicitar Logs: o ícone que abre o popup de calendário do campo "Data dos logs" passa a ficar à **esquerda** da data, no início do campo (antes ficava no canto direito)

### 2.31.0 — 03/08/2026
- **Agente — consulta a logs históricos (dias anteriores):** o agente passa a ler o campo `Data: dd/mm/yyyy` do e-mail de solicitação. Se a data for a atual, extrai os logs do dia como sempre; se for anterior, monta os caminhos dos arquivos daquele dia a partir da nova configuração `historico.<log>.*` do `agent.properties`
- Nova configuração por log no `agent.properties`: `historico.<log>.caminho` (pasta base), `.formato` (nome do arquivo/pasta) e `.tipo` (`arquivo` ou `pasta`). No formato, `[..]` é formato de data (ex.: `[yyyy-mm-dd]`), `(..)` são as variáveis `LOJA`/`PDV` e `{..}` é texto fixo — ex.: `{MFDE}(LOJA)(PDV)[yyyymmdd].zip` resolve para `MFDE004545020260802.zip`
- Logs do tipo `pasta` (CSIDebugFile, CSIDebugFileRT e CSIDebugFileSO, que ficam na pasta diária `\p2k\Bin\debug_P2K\<yyyymmdd>\`) têm a pasta inteira compactada no anexo. Quando vários logs apontam para a mesma pasta, ela é compactada **uma única vez**
- E-mail de resposta passa a exibir a **data dos logs** nos cards do cabeçalho, destacada em âmbar quando é uma consulta histórica; o anexo histórico recebe nome próprio (`LOG-<loja>-<pdv>-HIST<yyyymmdd>-<timestamp>.zip`)
- Logs sem configuração de histórico (ex.: `promo-client`) são reportados como "Sem configuração" na tabela do e-mail, sem interromper os demais

### 2.30.1 — 03/08/2026
- Solicitar Logs: validação para **não permitir data futura** no campo Data dos logs — o popup de calendário bloqueia datas após hoje (atributo `max`), o formulário exibe alerta se uma data futura for digitada, e o backend recusa a solicitação (HTTP 400) como proteção final

### 2.30.0 — 03/08/2026
- **Solicitar Logs — campo "Data dos logs":** novo campo de data no formulário (com popup de calendário nativo), sempre preenchido com a data do dia ao carregar a tela, ao limpar e após cada envio. A data escolhida vai no corpo de todos os e-mails de solicitação como `Data: dd/mm/yyyy` (Solicitação Log, linx-webservices e Log SP)
- O Server Agent SP já interpreta o campo `Data:` (usa a data nos formatos de nome de arquivo); o agente de PDV ignora a linha por enquanto — a extração retroativa nos agentes fica para um desenvolvimento futuro

### 2.29.2 — 03/08/2026
- Solicitar Logs: a seção **Arquivos de Logs** ganhou o mesmo padrão visual das **Consultas Disponíveis** do Exportar Dados Oracle — container com borda e fundo cinza, título azul, e cada checkbox dentro de uma caixinha branca com borda (hover com fundo claro e borda azul)

### 2.29.1 — 03/08/2026
- **Solicitar Logs — seção Arquivos de Logs:** colunas alargadas (mínimo de 150px para 190px, largura da grade de 640px para 800px) para que os nomes longos caibam por inteiro. Antes, `integrador_nfeio_client`, `integrador_idb_client` e `ProcTrans_CSIDebugFile` ultrapassavam a coluna e ficavam cortados

### 2.29.0 — 03/08/2026
- **Solicitar Logs — integração com o Server Agent SP:** nova loja **SERVERS_EP_SP** no combobox de lojas. Ao selecioná-la, o campo PDV é ocultado e a seção **Arquivos de Logs** passa a listar os logs dos servidores EP SP extraídos pelo agente (`integrador_idb`, `webservices`, `ProcTrans_CSIDebugFile`, `lgComandosSQL`, `csi_ws-safe`, `csi_safe-retaguarda`), cada checkbox com **hint (tooltip) do ip do servidor** de origem
- A solicitação envia e-mail no formato do agente SP — assunto `[Solicitação Log SP] - [PID]` e corpo com `PID`, `Destino` e `Logs` (nomes separados por vírgula), sem Loja/PDV
- Nova configuração **Logs SP** na aba Configurações (chave `logs_sp` no `config.properties`): um log por linha no formato `nome;ip`, editável pela interface

### 2.28.0 — 29/07/2026
- **Requisição API — encadeamento de token (OAuth client_credentials):** no cadastro de API há um novo combobox **API geradora de token**. Ao indicar uma API geradora, a funcionalidade chama essa API **antes** da consulta, extrai o token do retorno (JSON OAuth: `access_token`/`token`/`id_token` com `token_type`, ou token cru no corpo) e injeta automaticamente no `Authorization: Bearer` da requisição de consulta
- **Requisição API — campo Headers por API:** novo campo (multi-linha, `Nome: Valor` ou `Nome=Valor`, um por linha) para enviar headers arbitrários na chamada (ex.: `Authorization: Basic ...`, `x-adeo-bu-id`, `adeo-operator`, `Cookie`). Precedência: defaults (Accept/Content-Type) < APIKEY < Headers < Authorization do token. As quebras de linha são gravadas codificadas (`\n`) para caber em uma linha do `.properties`
- Na funcionalidade, quando a API usa geração de token, o painel de detalhes exibe **"Token via: <API>"**
- Aba MDM (Alterar): reordenação das seções — a seção do tipo do cliente (**Pessoa Física (Inhabitant)** ou **Pessoa Jurídica (Professional Organization)**) passa a ser exibida primeiro, com **Dados Gerais** logo abaixo, seguida das demais seções (igual ao comportamento do Cadastro)

### 2.25.1 — 27/07/2026
- Aba MDM (Cadastrar e Alterar): os campos de **Optin de Adesão** do Programa de Fidelidade (Optin de Adesão, Data do Optin de Adesão e App Responsável) passam a ser destacados num subgrupo próprio — no Cadastro, com a mesma caixa de borda azul tracejada usada no Optin de E-mail; na Alteração, como uma caixa própria "Optin de Adesão", igual aos demais optins

### 2.25.0 — 27/07/2026
- Aba MDM (Cadastrar): novo preset **Programa de Fidelidade**, exibido **apenas para PF**, com combobox: Não, 1 - LMCV, 2 - Lead, 4 - PRO/EXECUTOR e 7 - PRO/EAD. Ao escolher uma opção, marca e preenche os campos da seção Loyalty conforme o mapeamento da planilha `preset_fidelidade.xlsx`; campos de loyalty não previstos na opção ficam desmarcados (não vão no payload). "Não" limpa a seção
- Como o programa de fidelidade é só para PF: ao marcar o cliente como **PJ**, todos os campos da seção Loyalty são desmarcados e o preset é ocultado/zerado
- `montarPayload` deixa de enviar os sub-objetos `adhesionOptin`/`professionalAssociation` quando vazios (campos sem valor não são enviados)

### 2.22.1 — 22/07/2026
- Recompilação do agente com o log de nome fixo (2.22.0) e novo modo de autoteste `agent_extrator_log.exe --selftest-log`, que cria/anexa no `agente_extrator.log` e sai sem conectar em e-mail nem tocar nos PDVs — permite validar rapidamente o caminho do log na máquina

### 2.22.0 — 22/07/2026
- **Agente — arquivo de log com nome fixo e rotação diária por renomeação:**
  - O dia corrente é sempre gravado em `log/agente_extrator.log` (nome fixo), em vez de `operacao_<data>.log`
  - Na virada do dia (ou no primeiro registro após reinício em outra data), o arquivo fixo é renomeado para `agente_extrator_<data-anterior>.log` e um novo arquivo fixo é iniciado para o dia — evita que o log cresça indefinidamente e mantém o histórico datado
  - Reinícios no mesmo dia continuam anexando ao arquivo fixo; se já houver arquivo arquivado para aquela data, o conteúdo é anexado em vez de sobrescrito

### 2.20.0 — 21/07/2026
- Aba MDM (Cadastrar e Alterar): o JSON gerado agora é **editável antes do envio**. Cada item da lista de payloads (e o JSON Patch da alteração) é exibido em um editor de texto com validação ao vivo (borda vermelha + mensagem quando o JSON fica inválido). No envio, é usado o JSON editado — validado antes de sair; no cadastro, o identificador do histórico é reextraído do JSON editado. Gerar novamente descarta as edições

### 2.19.1 — 20/07/2026
- Guia **Requisição API** (Configurações): botões **↑/↓** em cada API para definir a **ordem de exibição no combobox**. A ordem é persistida em `api_order` (config.properties) e respeitada tanto no cadastro quanto na funcionalidade; APIs sem ordem definida aparecem ao final, em ordem alfabética

### 2.19.0 — 20/07/2026
- **Requisição API reformulada** no padrão de layout da aba **Manutenção PDV**: seleção da API por combobox, campo **Parâmetro** com a *dica de parâmetro* exibida como placeholder, e campo **Body** exibido apenas para métodos diferentes de GET (omitido em GET). A requisição passa a suportar qualquer método (GET/POST/PUT/PATCH/DELETE)
- O retorno da API agora pode ser **enviado por e-mail** (com combobox de destinatários e e-mail no padrão visual do sistema) ou **baixado** como arquivo (`.json`/`.xml`/`.txt` conforme o conteúdo), como na Exportação Oracle
- Nova guia **Requisição API** em Configurações para **cadastro e manutenção** das APIs: nome (combobox), URL, APIKEY, parâmetro, dica de parâmetro, método e body padrão. A **APIKEY é exibida mascarada** (com botão Mostrar/Ocultar) e armazenada em `secure.properties`; os demais metadados ficam em `config.properties`. As chaves `api.*` legadas em `secure.properties` (url/header/method/params) migram automaticamente ao salvar

### 2.18.1 — 17/07/2026
- Base de endereços (`static/data/base_de_ceps.json`) enriquecida com **1.310 endereços reais e válidos** (via API pública ViaCEP): +30 por UF e +130 para SP, RJ, MG, RS e CE. Total passou de 3.431 para 4.741 registros
- **AC** passa a ter endereços na base (30 registros) e foi incluído na lista de UFs de endereço (`UFS_CEP`), ficando disponível no combobox de UF do endereço e no preset UF

### 2.18.0 — 17/07/2026
- Aba MDM (Cadastrar e Alterar): CNAEs gerados automaticamente agora são **válidos** (código e descrição oficiais). Nova base `static/data/base_de_cnaes.json` com as **1.332 subclasses CNAE do IBGE** (API oficial de serviços de dados), no mesmo padrão da base de CEPs
- Cadastro: os N CNAEs do bloco são sorteados da base, sem repetição, com o 1º marcado como principal e a descrição oficial correspondente ao código
- Alterar: o botão **+ CNAE** pré-preenche com um código válido da base; ao gerar o patch, a descrição sai da base oficial (para códigos que o cliente já tinha e não estão na base, a descrição original do cliente é preservada)

### 2.17.3 — 17/07/2026
- Aba MDM (Cadastrar): preset **UF (Estado)** passa a ter **Automático** como valor padrão (primeira opção do combobox). A escolha — Automático ou uma UF específica — é replicada nos campos de UF do endereço e da Inscrição Estadual (PF e PJ)

### 2.17.2 — 17/07/2026
- Aba MDM (Cadastrar): layout da seção de presets refeito — rótulos e radio buttons na mesma linha e alinhados em colunas (rótulo com largura fixa à esquerda, opções Sim/Não alinhadas entre as linhas); campo **UF (Estado)** movido para a primeira linha dos presets (acima de Isento de Inscrição Estadual) com combobox compacto (130px)
- Corrigido o desalinhamento vertical do botão **Copiar** na lista de payloads: o `styles.css` global aplica `margin-top: 20px` em todo `button`, que não era zerado no botão Copiar (também zerado nos botões inline da aba Alterar: + Adicionar, Remover e + CNAE)

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
