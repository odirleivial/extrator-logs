/**
 * BEC Relay Worker — Cloudflare Workers (formato Service Worker, compatível com upload direto)
 *
 * Deploy: Cloudflare Dashboard → Workers & Pages → bec-relay → Edit code → colar → Deploy
 *
 * Settings → Variables:
 *   TOKEN = seu_token_secreto          (o mesmo do BEC e do agent.properties)
 * Settings → Variables → KV Namespace Bindings:
 *   Nome da variável: KV               Namespace: bec-relay
 * Settings → Bindings → R2 Bucket (para o download de logs):
 *   Nome da variável: R2               Bucket: bec-relay-arquivos
 *
 * ---------------------------------------------------------------------------
 * CUSTO NO KV — é o que dita o desenho deste arquivo.
 *
 * O agente busca trabalho de poucos em poucos segundos, o dia inteiro. Essa rota
 * é executada dezenas de milhares de vezes por dia, então cada operação de KV
 * que ela fizer é multiplicada por isso. Os limites diários do plano gratuito
 * são: 100.000 leituras, 1.000 escritas, 1.000 remoções e 1.000 listagens.
 *
 * Uma versão anterior deste worker usava KV.list() a cada GET /pendente. Com o
 * agente buscando a cada 2s são ~43.200 chamadas por dia, contra as 1.000
 * permitidas — a cota estourava em cerca de uma hora e o relay passava a
 * responder erro 1101.
 *
 * Por isso aqui NÃO EXISTE KV.list() em lugar nenhum. A fila de cada agente é um
 * índice em uma única chave, e o poll ocioso — que é a esmagadora maioria — custa
 * exatamente UMA leitura:
 *
 *   fila:<loja>:<pdv>   → [ {k, pid, tipo, exp, lease}, ... ]
 *
 * Ao mexer neste arquivo, a pergunta a fazer sobre qualquer linha nova é
 * "quantas operações de KV isso adiciona ao poll ocioso?". A resposta precisa
 * continuar sendo zero.
 * ---------------------------------------------------------------------------
 *
 * O contrato HTTP é o mesmo das versões anteriores — mesmos caminhos, métodos e
 * formatos de resposta —, então BEC e agentes já instalados funcionam sem
 * atualização e a troca não precisa ser sincronizada.
 *
 * ARQUIVOS GRANDES VÃO PARA O R2, NÃO PARA O KV.
 *
 * O KV tem teto de 25 MB por valor, e um binário só cabe nele em base64 — que
 * ocupa 1/3 a mais e ainda obriga o Worker a serializar/parsear a coisa toda. O
 * R2 aceita o corpo binário como está, tem 10 GB no plano gratuito e não impõe
 * esse teto. Por isso o ZIP de logs trafega por /arquivo/<pid>, e pelo KV passa
 * apenas o aviso de que ele está pronto (nome, tamanho, sha256).
 *
 * Comportamento da fila:
 *  - vários pedidos podem esperar ao mesmo tempo, entregues na ordem de chegada
 *  - o item entregue fica reservado por LEASE_TTL, para um pedido demorado não
 *    segurar os que vieram depois; se o agente morrer, a reserva expira e o item
 *    volta a ser entregue
 *  - POST /resultado/<pid> remove exatamente aquele item
 */

// Validade dos itens. O mínimo aceito pelo KV é 60s.
const TTL_COMANDO = 900;   // 15 min — logs, manutenção PDV, atualização, registro
const TTL_PINPAD  = 120;   // 2 min  — comando velho de PinPad não serve mais
const TTL_RESULTO = 900;   // 15 min — janela do BEC para ler o resultado
const LEASE_TTL   = 600;   // 10 min de reserva por item entregue

addEventListener('fetch', event => {
  event.respondWith(handleRequest(event.request));
});

const agora = () => Math.floor(Date.now() / 1000);
const chaveFila = (loja, pdv) => 'fila:' + loja + ':' + pdv;

async function lerIndice(loja, pdv) {
  const bruto = await KV.get(chaveFila(loja, pdv));
  if (!bruto) return [];
  try {
    const lista = JSON.parse(bruto);
    return Array.isArray(lista) ? lista : [];
  } catch (_) {
    return [];
  }
}

async function gravarIndice(loja, pdv, itens) {
  if (itens.length === 0) {
    await KV.delete(chaveFila(loja, pdv));
    return;
  }
  await KV.put(chaveFila(loja, pdv), JSON.stringify(itens), { expirationTtl: TTL_COMANDO });
}

// Descarta o que já expirou. O corpo do item some sozinho pelo TTL do KV, mas a
// entrada no índice ficaria para trás — sem list, é aqui que ela é limpa.
function podar(itens) {
  const t = agora();
  return itens.filter(i => i && i.exp > t);
}

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

  // GET /status — health check. Não toca no KV de propósito: serve para saber se
  // o relay responde mesmo quando a cota do KV estourou.
  if (request.method === 'GET' && path === '/status') {
    return jsonResp({ ok: true, ts: new Date().toISOString() });
  }

  // POST /comando/:loja/:pdv — BEC enfileira um pedido para o agente
  if (request.method === 'POST' && /^\/comando\/[^/]+\/[^/]+$/.test(path)) {
    const partes = path.split('/');
    const loja = partes[2], pdv = partes[3];
    const body = await request.text();

    let dados = {};
    try { dados = JSON.parse(body) || {}; } catch (_) { dados = {}; }

    // Item sem `tipo` é PinPad — era o único tipo antes de o campo existir.
    const ehPinpad = !dados.tipo || dados.tipo === 'pinpad';
    const ttl = ehPinpad ? TTL_PINPAD : TTL_COMANDO;

    const k = 'cmd:' + loja + ':' + pdv + ':' + String(Date.now()).padStart(13, '0') + ':' + sufixo();
    await KV.put(k, body, { expirationTtl: ttl });

    const itens = podar(await lerIndice(loja, pdv));
    itens.push({
      k: k,
      pid: dados.pid || '',
      tipo: dados.tipo || 'pinpad',
      exp: agora() + ttl,
      lease: 0,
    });
    await gravarIndice(loja, pdv, itens);

    // O PID aponta para a chave exata, para o ack remover só este item.
    if (dados.pid) {
      await KV.put('pid:' + dados.pid, loja + '|' + pdv + '|' + k, { expirationTtl: ttl });
    }
    return jsonResp({ ok: true });
  }

  // GET /pendente/:loja/:pdv — agente busca o próximo pedido
  //
  // Caminho ocioso (o normal): UMA leitura do índice e 204. Sem escrita, sem
  // listagem. É o que mantém o consumo dentro da cota.
  if (request.method === 'GET' && /^\/pendente\/[^/]+\/[^/]+$/.test(path)) {
    const partes = path.split('/');
    const loja = partes[2], pdv = partes[3];

    const original = await lerIndice(loja, pdv);
    if (original.length === 0) return new Response(null, { status: 204 });

    const itens = podar(original);
    const t = agora();

    for (const item of itens) {
      // Pula o que está reservado: um pedido demorado não pode segurar a fila.
      if (item.lease && (t - item.lease) < LEASE_TTL) continue;

      const valor = await KV.get(item.k);
      if (!valor) { item.exp = 0; continue; }  // sumiu por TTL; poda abaixo

      item.lease = t;
      await gravarIndice(loja, pdv, podar(itens));
      return new Response(valor, { headers: { 'Content-Type': 'application/json' } });
    }

    // Nada entregável. Só grava se a poda mudou algo, para não gastar escrita em
    // poll ocioso.
    const limpo = podar(itens);
    if (limpo.length !== original.length) await gravarIndice(loja, pdv, limpo);
    return new Response(null, { status: 204 });
  }

  // POST /resultado/:pid — agente devolve o resultado e libera aquele item
  if (request.method === 'POST' && /^\/resultado\/[^/]+$/.test(path)) {
    const pid  = path.split('/')[2];
    const body = await request.text();

    const ref = await KV.get('pid:' + pid);
    if (ref) {
      const p = ref.split('|');
      const loja = p[0], pdv = p[1], k = p[2];
      await KV.delete(k);
      await KV.delete('pid:' + pid);
      const itens = podar(await lerIndice(loja, pdv)).filter(i => i.k !== k);
      await gravarIndice(loja, pdv, itens);
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

  // GET /fila/:loja/:pdv — diagnóstico. Lê o índice; também não lista.
  if (request.method === 'GET' && /^\/fila\/[^/]+\/[^/]+$/.test(path)) {
    const partes = path.split('/');
    const loja = partes[2], pdv = partes[3];
    const t = agora();
    const itens = podar(await lerIndice(loja, pdv)).map(i => ({
      pid: i.pid,
      tipo: i.tipo,
      reservado: !!(i.lease && (t - i.lease) < LEASE_TTL),
      expiraEm: Math.max(0, i.exp - t),
    }));
    return jsonResp({ total: itens.length, itens: itens });
  }

  // -------------------------------------------------------------------------
  // Arquivos grandes (ZIP de logs) — R2
  //
  // O corpo trafega binário puro: nada de base64 e nada de JSON.parse, então o
  // tamanho não é limitado pelos 25 MB do KV nem custa CPU do Worker.
  // -------------------------------------------------------------------------
  if (/^\/arquivo\/[^/]+$/.test(path)) {
    if (typeof R2 === 'undefined') {
      return jsonResp({ erro: 'Bucket R2 não configurado (Settings → Bindings → R2 Bucket, variável R2).' }, 503);
    }
    const chave = 'log/' + path.split('/')[2];

    // PUT — o agente envia o arquivo pronto
    if (request.method === 'PUT') {
      await R2.put(chave, request.body);
      return jsonResp({ ok: true });
    }

    // GET — o BEC baixa
    if (request.method === 'GET') {
      const obj = await R2.get(chave);
      if (!obj) return jsonResp({ erro: 'Arquivo não encontrado ou já baixado' }, 404);
      return new Response(obj.body, {
        headers: {
          'Content-Type': 'application/octet-stream',
          'Content-Length': String(obj.size),
        },
      });
    }

    // DELETE — o BEC confirma o download e libera o espaço
    if (request.method === 'DELETE') {
      await R2.delete(chave);
      return jsonResp({ ok: true });
    }
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
