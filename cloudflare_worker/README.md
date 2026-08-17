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
| `cmd:<loja>:<pdv>:<ts>:<rnd>` | corpo do pedido | 15 min (PinPad: 2 min) |
| `pid:<pid>` | chave do item correspondente | idem |
| `lease:<chave do item>` | marca de entregue | 10 min |
| `res:<pid>` | resultado devolvido pelo agente | 15 min |

Uma chave **por item** (não uma por loja/PDV) é o que evita perder pedidos: não
há ler-modificar-gravar, que no KV não é atômico.

## Limitação conhecida

O KV é eventualmente consistente: um item recém-gravado pode levar um instante
para aparecer na listagem. Como o agente busca a cada 2 s, ele pega no ciclo
seguinte. Se algum dia isso incomodar, o caminho é migrar a fila para **Durable
Objects**, que dá consistência forte e ordenação garantida.
