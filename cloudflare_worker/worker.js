/**
 * BEC Relay Worker — Cloudflare Workers (formato Service Worker, compatível com upload direto)
 *
 * Deploy: Cloudflare Dashboard → Workers → bec-relay → Edit code → colar este arquivo → Deploy
 *
 * Settings → Variables:
 *   TOKEN = seu_token_secreto          (o mesmo do BEC e do agent.properties)
 * Settings → Variables → KV Namespace Bindings:
 *   Nome da variável: KV               Namespace: bec-relay
 *
 * ---------------------------------------------------------------------------
 * O contrato HTTP é o mesmo da versão anterior — mesmos caminhos, métodos e
 * formatos de resposta. BEC e agentes já instalados continuam funcionando sem
 * atualização, então esta troca não precisa ser sincronizada com ninguém.
 *
 * O que mudou em relação à versão anterior:
 *
 *  1. FILA DE VERDADE, em vez de um item por loja/PDV.
 *     Antes: `KV.put('cmd:<loja>:<pdv>')` guardava UM item; um segundo POST
 *     sobrescrevia o primeiro, sem erro. Dois pedidos seguidos ao mesmo agente,
 *     ou um pedido novo durante uma extração de logs, perdiam um deles.
 *     Agora cada item tem chave própria (`cmd:<loja>:<pdv>:<ts>:<rnd>`) e o
 *     `GET /pendente` escolhe o mais antigo. Uma chave por item também evita
 *     ler-modificar-gravar, que no KV não é atômico e perderia itens de novo.
 *
 *  2. ACK CASADO POR PID.
 *     Antes o `POST /resultado/<pid>` apagava a chave do comando qualquer que
 *     fosse o item ali — o ack de um pedido apagava outro que tivesse chegado
 *     no meio. Agora o PID aponta para a chave exata do item, e só ela é
 *     removida.
 *
 *  3. ENTREGA COM RESERVA (lease), para o item lento não travar a fila.
 *     Um item entregue fica reservado por LEASE_TTL; enquanto isso o `GET
 *     /pendente` passa para o próximo. Sem isso, uma extração de logs de vários
 *     minutos seguraria o PinPad atrás dela. Se o agente morrer no meio, a
 *     reserva expira e o item volta a ser entregue — o agente já ignora item
 *     repetido pelo PID que mantém em memória.
 *
 *  4. VALIDADE MAIOR E POR TIPO.
 *     Antes tudo expirava em 120s: agente offline por mais de 2 minutos perdia o
 *     pedido, e o BEC tinha 2 minutos para ler um resultado. Agora comandos
 *     duráveis (logs, manutenção, atualização) valem TTL_COMANDO, e só o PinPad
 *     segue curto — um comando de PinPad entregue 10 minutos depois não serve
 *     para nada, é melhor que expire.
 *
 *  5. AUTENTICAÇÃO À PROVA DE ESQUECIMENTO.
 *     Antes, `if (token && ...)`: sem a variável TOKEN configurada, o relay
 *     ficava ABERTO. Agora, sem TOKEN ele recusa tudo com 503 — falha visível
 *     em vez de silenciosamente público.
 *
 *  6. GET /fila/<loja>/<pdv> — endpoint só de leitura para diagnóstico, mostra
 *     quantos itens estão na fila e quais estão reservados.
 *
 * Limitação que permanece: o KV é eventualmente consistente, então um item
 * recém-gravado pode levar um instante para aparecer na listagem. Na prática o
 * agente busca a cada 2s e pega no ciclo seguinte. Se um dia isso incomodar, o
 * caminho é migrar a fila para Durable Objects, que dá consistência forte.
 */

// Validade dos itens. O mínimo aceito pelo KV é 60s.
const TTL_COMANDO = 900;   // 15 min — logs, manutenção PDV, atualização, registro
const TTL_PINPAD  = 120;   // 2 min  — comando velho de PinPad não serve mais
const TTL_RESULTO = 900;   // 15 min — janela do BEC para ler o resultado
const LEASE_TTL   = 600;   // 10 min de reserva por item entregue

// Teto de chaves examinadas em um GET /pendente, para o polling continuar barato
// mesmo se a fila crescer muito.
const MAX_VARRIDURA = 25;

addEventListener('fetch', event => {
  event.respondWith(handleRequest(event.request));
});

async function handleRequest(request) {
  const url   = new URL(request.url);
  const path  = url.pathname;
  const token = (typeof TOKEN !== 'undefined' ? TOKEN : '').trim();

  // Sem token configurado o relay não sobe: melhor recusar tudo do que servir
  // aberto para a internet.
  if (!token) {
    return jsonResp({ erro: 'Relay sem TOKEN configurado (Settings → Variables).' }, 503);
  }
  if (request.headers.get('X-Token') !== token) {
    return jsonResp({ erro: 'Não autorizado' }, 401);
  }

  // GET /status — health check
  if (request.method === 'GET' && path === '/status') {
    return jsonResp({ ok: true, ts: new Date().toISOString() });
  }

  // POST /comando/:loja/:pdv — BEC enfileira um pedido para o agente
  if (request.method === 'POST' && /^\/comando\/[^/]+\/[^/]+$/.test(path)) {
    const [, , loja, pdv] = path.split('/');
    const body = await request.text();

    let dados = {};
    try { dados = JSON.parse(body) || {}; } catch (_) { dados = {}; }

    // Item sem `tipo` é PinPad — era o único tipo antes de o campo existir.
    const ehPinpad = !dados.tipo || dados.tipo === 'pinpad';
    const ttl = ehPinpad ? TTL_PINPAD : TTL_COMANDO;

    // Chave por item: o timestamp com zeros à esquerda faz a ordem lexicográfica
    // coincidir com a cronológica, e o sufixo aleatório evita colisão entre dois
    // POSTs no mesmo milissegundo.
    const chave = `cmd:${loja}:${pdv}:${String(Date.now()).padStart(13, '0')}:${sufixo()}`;
    await KV.put(chave, body, { expirationTtl: ttl });

    // O PID aponta para a chave exata, para o ack remover só este item.
    if (dados.pid) {
      await KV.put('pid:' + dados.pid, chave, { expirationTtl: ttl });
    }
    return jsonResp({ ok: true });
  }

  // GET /pendente/:loja/:pdv — agente busca o próximo pedido (poll a cada 2s)
  if (request.method === 'GET' && /^\/pendente\/[^/]+\/[^/]+$/.test(path)) {
    const [, , loja, pdv] = path.split('/');
    const lista = await KV.list({ prefix: `cmd:${loja}:${pdv}:`, limit: MAX_VARRIDURA });

    for (const chaveInfo of lista.keys) {
      const chave = chaveInfo.name;

      // Item já entregue e ainda dentro da reserva: pula para o próximo, senão
      // um pedido demorado seguraria todos os que vieram depois dele.
      if (await KV.get('lease:' + chave)) continue;

      const valor = await KV.get(chave);
      if (!valor) continue;  // expirou entre a listagem e a leitura

      await KV.put('lease:' + chave, String(Date.now()), { expirationTtl: LEASE_TTL });
      return new Response(valor, { headers: { 'Content-Type': 'application/json' } });
    }
    return new Response(null, { status: 204 });
  }

  // POST /resultado/:pid — agente devolve o resultado e libera o item
  if (request.method === 'POST' && /^\/resultado\/[^/]+$/.test(path)) {
    const pid  = path.split('/')[2];
    const body = await request.text();

    const chave = await KV.get('pid:' + pid);
    if (chave) {
      await KV.delete(chave);
      await KV.delete('lease:' + chave);
      await KV.delete('pid:' + pid);
    }
    await KV.put('res:' + pid, body, { expirationTtl: TTL_RESULTO });
    return jsonResp({ ok: true });
  }

  // GET /resultado/:pid — BEC lê o resultado (uma vez; a leitura consome)
  if (request.method === 'GET' && /^\/resultado\/[^/]+$/.test(path)) {
    const pid   = path.split('/')[2];
    const valor = await KV.get('res:' + pid);
    if (valor) {
      await KV.delete('res:' + pid);
      return new Response(valor, { headers: { 'Content-Type': 'application/json' } });
    }
    return new Response(null, { status: 204 });
  }

  // GET /fila/:loja/:pdv — diagnóstico: o que está na fila deste agente
  if (request.method === 'GET' && /^\/fila\/[^/]+\/[^/]+$/.test(path)) {
    const [, , loja, pdv] = path.split('/');
    const lista = await KV.list({ prefix: `cmd:${loja}:${pdv}:`, limit: MAX_VARRIDURA });

    const itens = [];
    for (const chaveInfo of lista.keys) {
      const chave = chaveInfo.name;
      const reservado = !!(await KV.get('lease:' + chave));
      let tipo = '?', pid = '?';
      try {
        const d = JSON.parse(await KV.get(chave) || '{}');
        tipo = d.tipo || 'pinpad';
        pid  = d.pid  || '?';
      } catch (_) {}
      itens.push({ pid, tipo, reservado, chave });
    }
    return jsonResp({ total: itens.length, itens });
  }

  return jsonResp({ erro: 'Rota não encontrada' }, 404);
}

function sufixo() {
  return Math.random().toString(36).slice(2, 8);
}

function jsonResp(obj, status) {
  return new Response(JSON.stringify(obj), {
    status: status || 200,
    headers: { 'Content-Type': 'application/json' },
  });
}
