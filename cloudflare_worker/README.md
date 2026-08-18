# BEC Relay — Cloudflare Worker

Código do relay que leva as solicitações do BEC até os agentes sem passar por
e-mail. É o fonte do Worker publicado em `bec-relay.<usuario>.workers.dev`.

> Este arquivo existe porque o Worker ficou muito tempo **só no painel da
> Cloudflare**, sem cópia no repositório. Toda a comunicação do BEC com o Agent
> Extrator depende dele — se for perdido ou editado por engano, sem o fonte
> versionado não há de onde restaurar. Mantenha este arquivo em sincronia com o
> que está publicado.

## Deploy

1. Cloudflare Dashboard → **Workers & Pages** → `bec-relay` → **Edit code**
2. Colar o conteúdo de [`worker.js`](worker.js) e **Deploy**
3. Conferir em **Settings → Variables**:
   - `TOKEN` = o mesmo token do `config.properties` (BEC) e do `agent.properties`
   - **KV Namespace Bindings**: variável `KV` → namespace `bec-relay`

Antes de subir:

```bash
python scripts\conferir_worker.py
```

Depois de subir:

```bash
python scripts\testar_worker_relay.py
```

O teste de aceitação usa a loja/PDV fictícia `9999/999`, que nenhum agente real
consome, e limpa a fila ao terminar. Ele **reprova a versão antiga** — se
acusar fila sobrescrita ou rota `/fila` ausente, o deploy não pegou.

Para voltar atrás, o painel guarda as versões anteriores em **Deployments**.

## Endpoints

Todos exigem o header `X-Token`. Um `User-Agent` de navegador também é
necessário: o Worker fica atrás da proteção de bot do Cloudflare, que responde
**403** ao User-Agent padrão de `urllib`/`requests`.

| Método | Rota | Quem chama | O que faz |
|---|---|---|---|
| GET | `/status` | BEC | Health check |
| POST | `/comando/<loja>/<pdv>` | BEC | Enfileira um pedido |
| GET | `/pendente/<loja>/<pdv>` | agente | Pega o próximo pedido (não removido até o resultado chegar) |
| POST | `/resultado/<pid>` | agente | Devolve o resultado e **remove aquele** pedido |
| GET | `/resultado/<pid>` | BEC | Lê o resultado (consome na leitura) |
| GET | `/fila/<loja>/<pdv>` | diagnóstico | Lista o que está na fila e o que está reservado |

A fila é endereçada pelo par **loja/PDV do agente** (`bec_loja`/`bec_pdv` no BEC,
`loja`/`pdv` no `agent.properties`) — os dois precisam ser iguais. O alvo de cada
operação vai dentro do corpo do pedido, não na URL.

## Modelo de dados no KV

| Chave | Conteúdo | Validade |
|---|---|---|
| `fila:<loja>:<pdv>` | indice: `[{k, pid, tipo, exp, lease}]` | 15 min |
| `cmd:<loja>:<pdv>:<ts>:<rnd>` | corpo do pedido | 15 min (PinPad: 2 min) |
| `pid:<pid>` | chave do item correspondente | idem |

| `res:<pid>` | resultado devolvido pelo agente | 15 min |

O índice é o que permite servir a fila **sem listar**. A reserva de entrega
(`lease`) mora dentro da entrada do índice, então entregar um item não custa uma
escrita extra. O corpo de cada pedido continua em chave própria: só o índice é
reescrito nas operações de fila, e o payload grande — o pacote de atualização,
por exemplo — não é reserializado a cada mexida.

## Custo no KV — leia antes de mexer no worker

O agente busca trabalho de poucos em poucos segundos, o dia inteiro. Qualquer
operação de KV na rota `GET /pendente` é multiplicada por dezenas de milhares
por dia. Limites diários do plano gratuito:

| Operação | Limite/dia |
|---|---|
| leitura | 100.000 |
| escrita | 1.000 |
| remoção | 1.000 |
| **listagem** | **1.000** |

Uma versão anterior deste worker chamava `KV.list()` a cada `GET /pendente`.
Com o agente buscando a cada 2 s são ~43.200 listagens/dia contra as 1.000
permitidas: a cota estourava em cerca de uma hora e o relay passava a responder
**erro 1101** em `/pendente` e `/fila` (o `/status` continuava de pé, porque não
toca no KV).

Por isso **não existe `KV.list()` neste arquivo**. A fila é um índice em uma
chave por agente (`fila:<loja>:<pdv>`), e o caminho ocioso — a esmagadora
maioria dos polls — custa **uma leitura e nenhuma escrita**.

Ao acrescentar qualquer coisa aqui, a pergunta é: *quantas operações de KV isso
adiciona ao poll ocioso?* A resposta precisa continuar sendo zero.

Consumo com a configuração recomendada do agente (janela 07:00–20:00 em dias
úteis, intervalo ocioso de 15 s): **~3.100 leituras/dia**, 3% da cota — cabem
cerca de 30 agentes antes de encostar no limite.

## Limitação conhecida

O índice é lido, alterado e gravado sem transação — o KV não tem
ler-modificar-gravar atômico. Dois `POST /comando` no mesmo milissegundo podem
fazer um sobrescrever o outro. Na prática os pedidos são poucos e disparados por
pessoas, então a janela é desprezível; se um dia importar, o caminho é migrar a
fila para **Durable Objects**, que dá consistência forte.
