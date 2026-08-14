# request_api.py
import os
import sys as _sys
import json
import requests
import logging
from datetime import datetime
from flask import render_template, request, jsonify, send_file
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from execucao import registrar_execucao

logger = logging.getLogger('ExtratrorLogs')

# Resolve o diretório base igual ao extrator_logs.py
if getattr(_sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(_sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

OUTPUT_DIR = os.path.join(_BASE_DIR, 'output')

# Campos que compõem uma API. 'apikey' é sensível (secure.properties);
# os demais ficam em config.properties.
#   headers        -> headers extras (um por linha, "Nome: Valor" ou "Nome=Valor")
#   token_provider -> nome de outra API que gera o token (Authorization Bearer)
API_CAMPOS = ('url', 'apikey', 'params', 'param_hint', 'method', 'body',
              'headers', 'token_provider')


def obter_apis(props):
    """Monta a lista de APIs a partir de props mescladas (config + secure).

    Cada chave tem o formato api.<nome>.<campo>, ex.: api.GET_Facede.url.
    Retorna uma lista de dicts ordenada por nome:
      {'nome', 'url', 'apikey', 'params', 'param_hint', 'method', 'body'}
    """
    apis = {}
    for key, value in props.items():
        if not key.startswith('api.'):
            continue
        partes = key.split('.', 2)
        if len(partes) < 3:
            continue
        nome, campo = partes[1], partes[2]
        if campo not in API_CAMPOS:
            continue
        apis.setdefault(nome, {'nome': nome})[campo] = value

    # Garante que todos os campos existam (string vazia quando ausente)
    for api in apis.values():
        for campo in API_CAMPOS:
            api.setdefault(campo, '')
        if not api.get('method'):
            api['method'] = 'GET'
        # headers é armazenado com quebras de linha codificadas como "\n" literal
        # (o .properties é linha-a-linha); aqui devolvemos as quebras reais.
        api['headers'] = (api.get('headers') or '').replace('\\n', '\n')

    # Ordem de exibição no combobox: definida em api_order (config); as APIs que
    # não constam na lista vão para o fim, em ordem alfabética.
    ordem = [n.strip() for n in props.get('api_order', '').split(',') if n.strip()]

    def chave(api):
        try:
            return (0, ordem.index(api['nome']))
        except ValueError:
            return (1, api['nome'].lower())

    return sorted(apis.values(), key=chave)


def pagina_requisicao_api(app, ler_properties, config_file):
    """Retorna a página da aba Requisição API"""
    props = ler_properties(config_file)
    apis = obter_apis(props)
    return render_template('requisicao_api.html', apis=apis, config_props=props)


def _parse_headers(texto):
    """Converte headers multi-linha ('Nome: Valor' ou 'Nome=Valor', um por linha)
    em dict. Usa o primeiro ':' ou '=' de cada linha como separador, preservando
    ':' e '=' no restante do valor (ex.: 'Authorization: Basic xxx==',
    'Cookie=PF=abc'). Um header por linha evita conflito com ';' de cookies."""
    headers = {}
    for linha in (texto or '').splitlines():
        linha = linha.strip()
        if not linha:
            continue
        posicoes = [p for p in (linha.find(':'), linha.find('=')) if p != -1]
        if not posicoes:
            continue
        i = min(posicoes)
        chave = linha[:i].strip()
        valor = linha[i + 1:].strip()
        if chave:
            headers[chave] = valor
    return headers


def _montar_headers(api):
    """Monta os headers da chamada a partir da API configurada.

    Ordem de precedência (do menor para o maior): defaults (Accept/Content-Type)
    < Apikey < headers configurados. O Authorization via token (quando houver API
    geradora) é aplicado depois, em fazer_requisicao_api, e sobrepõe estes."""
    headers = {'Accept': 'application/json'}
    metodo = (api.get('method') or 'GET').strip().upper()
    if metodo != 'GET':
        headers['Content-Type'] = 'application/json'
    apikey = (api.get('apikey') or '').strip()
    if apikey:
        headers['Apikey'] = apikey
    headers.update(_parse_headers(api.get('headers', '')))
    return headers


def _extrair_token(response):
    """Extrai o valor do Authorization a partir da resposta da API geradora de token.
    Suporta JSON OAuth (access_token/token/id_token + token_type opcional) e também
    token 'cru' no corpo da resposta."""
    try:
        dados = response.json()
    except Exception:
        dados = None
    if isinstance(dados, dict):
        for campo in ('access_token', 'accessToken', 'token', 'id_token', 'idToken'):
            valor = dados.get(campo)
            if valor:
                tipo = (dados.get('token_type') or 'Bearer').strip() or 'Bearer'
                return f"{tipo} {valor}"
    auth = response.headers.get('Authorization')
    if auth:
        return auth
    txt = (response.text or '').strip()
    if txt and len(txt) < 4000 and '\n' not in txt and ' ' not in txt:
        return f"Bearer {txt}"
    return ''


def _obter_token(provider):
    """Executa a API geradora de token e devolve (authorization, erro)."""
    url = (provider.get('url') or '').strip()
    if not url:
        return '', 'URL da API geradora de token não configurada.'
    url += (provider.get('params') or '').strip()
    metodo = (provider.get('method') or 'POST').strip().upper()
    headers = _montar_headers(provider)
    body = provider.get('body') or ''
    dados = None if metodo == 'GET' else (body if body.strip() else '')
    logger.debug(f"Gerando token via {provider.get('nome')} [{metodo}] {url}")
    try:
        resp = requests.request(metodo, url, headers=headers, data=dados, timeout=30, verify=False)
    except Exception as e:
        return '', f'Erro ao chamar a API de token: {str(e)}'
    if resp.status_code >= 400:
        return '', f'API de token retornou status {resp.status_code}.'
    auth = _extrair_token(resp)
    if not auth:
        return '', 'Não foi possível extrair o token da resposta da API geradora.'
    return auth, ''


def fazer_requisicao_api(app, ler_properties, config_file):
    """Faz a chamada à API selecionada, para qualquer método HTTP."""
    props = ler_properties(config_file)
    apis = obter_apis(props)

    nome_api = request.form.get('api', '').strip()
    param_tela = request.form.get('parametro', '').strip()
    body = request.form.get('body', '')

    if not nome_api:
        return jsonify({'sucesso': False, 'mensagem': 'Selecione uma API.'}), 400

    api = next((a for a in apis if a['nome'] == nome_api), None)
    if not api:
        return jsonify({'sucesso': False, 'mensagem': 'API não encontrada.'}), 400

    url_base = (api.get('url') or '').strip()
    if not url_base:
        return jsonify({'sucesso': False, 'mensagem': 'URL da API não configurada.'}), 400

    metodo = (api.get('method') or 'GET').strip().upper()

    # O parâmetro é concatenado diretamente à URL (aceita path "/123" ou query "?id=1")
    url_final = url_base + param_tela
    headers = _montar_headers(api)

    # Encadeamento de token: se a API indicar uma API geradora, gera o token e
    # sobrepõe o Authorization antes de fazer a consulta.
    provider_nome = (api.get('token_provider') or '').strip()
    if provider_nome:
        provider = next((a for a in apis if a['nome'] == provider_nome), None)
        if not provider:
            return jsonify({'sucesso': False, 'mensagem': f'API geradora de token "{provider_nome}" não encontrada.'}), 400
        auth, erro = _obter_token(provider)
        if erro:
            return jsonify({'sucesso': False, 'mensagem': f'[Token via {provider_nome}] {erro}'}), 502
        headers['Authorization'] = auth
        logger.info(f"Token obtido via {provider_nome} para a chamada de {nome_api}")

    # GET nunca envia body
    dados = None if metodo == 'GET' else (body if body.strip() else None)

    logger.debug(f"Chamando API {nome_api} [{metodo}] {url_final}")

    try:
        response = requests.request(
            metodo, url_final, headers=headers, data=dados,
            timeout=30, verify=False
        )
        retorno = {
            'status_code': response.status_code,
            'headers': dict(response.headers),
            'body': response.text
        }
        logger.info(f"API {nome_api} retornou status {response.status_code}")
        registrar_execucao(props, 'Requisição API', detalhes={
            'API': nome_api, 'Metodo': metodo, 'URL': url_final,
            'StatusCode': response.status_code,
        })
        return jsonify({'sucesso': True, 'retorno': retorno})
    except Exception as e:
        logger.error(f"Erro ao chamar API {nome_api}: {str(e)}")
        return jsonify({'sucesso': False, 'mensagem': f'Erro na chamada: {str(e)}'}), 500


def _extensao_por_conteudo(texto):
    """Detecta a extensão adequada (json/xml/txt) a partir do conteúdo."""
    conteudo = (texto or '').lstrip()
    if conteudo[:1] in ('{', '['):
        return 'json'
    if conteudo[:1] == '<':
        return 'xml'
    return 'txt'


def _gravar_retorno_arquivo(nome_api, conteudo):
    """Grava o retorno da API em OUTPUT_DIR e devolve o caminho do arquivo."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ext = _extensao_por_conteudo(conteudo)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    nome_seguro = ''.join(c if c.isalnum() or c in ('_', '-') else '_' for c in (nome_api or 'api'))
    nome_arquivo = f"retorno_api_{nome_seguro}_{ts}.{ext}"
    caminho = os.path.join(OUTPUT_DIR, nome_arquivo)
    with open(caminho, 'w', encoding='utf-8') as f:
        f.write(conteudo)
    return caminho


def gerar_retorno_download(app, ler_properties, config_file):
    """Grava o retorno recebido em um arquivo e devolve o nome para download."""
    retorno = request.form.get('retorno', '')
    nome_api = request.form.get('api', '').strip()

    if not retorno.strip():
        return jsonify({'sucesso': False, 'mensagem': 'Nenhum retorno para salvar.'}), 400

    try:
        caminho = _gravar_retorno_arquivo(nome_api, retorno)
        logger.info(f"Retorno da API gravado em: {caminho}")
        return jsonify({'sucesso': True, 'arquivo': os.path.basename(caminho)})
    except Exception as e:
        logger.error(f"Erro ao gravar retorno da API: {str(e)}")
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao salvar: {str(e)}'}), 500


def baixar_retorno(app, nome_arquivo):
    """Serve um arquivo de retorno já gerado em OUTPUT_DIR."""
    nome_seguro = os.path.basename(nome_arquivo)
    caminho = os.path.realpath(os.path.join(OUTPUT_DIR, nome_seguro))
    if not caminho.startswith(os.path.realpath(OUTPUT_DIR) + os.sep) or not os.path.isfile(caminho):
        logger.error(f"Tentativa de download de arquivo inválido: {nome_arquivo}")
        return jsonify({'sucesso': False, 'mensagem': 'Arquivo não encontrado'}), 404
    logger.info(f"Download do retorno da API: {nome_seguro}")
    return send_file(caminho, as_attachment=True, download_name=nome_seguro)


def enviar_retorno_email(app, ler_properties, config_file, gerar_pid, enviar_email_com_anexos):
    """Envia o retorno da API por e-mail, anexando o conteúdo como arquivo."""
    props = ler_properties(config_file)

    retorno = request.form.get('retorno', '')
    nome_api = request.form.get('api', '').strip()
    status_code = request.form.get('status_code', '').strip()
    email_destino = request.form.get('email_destino', '').strip()

    if not email_destino:
        return jsonify({'sucesso': False, 'mensagem': 'Selecione um e-mail de destino.'}), 400
    if not retorno.strip():
        return jsonify({'sucesso': False, 'mensagem': 'Nenhum retorno para enviar.'}), 400

    apis = obter_apis(props)
    api = next((a for a in apis if a['nome'] == nome_api), {'nome': nome_api})

    try:
        caminho = _gravar_retorno_arquivo(nome_api, retorno)
        pid = gerar_pid()
        assunto = f"[Requisição API][{nome_api}][{pid}]"
        corpo_txt, corpo_html = _montar_email_retorno(api, status_code, caminho, pid)
        enviar_email_com_anexos(
            props.get('email_envio', ''), props.get('senha_envio', ''),
            email_destino, assunto, corpo_txt, corpo_html, [caminho]
        )
        logger.info(f"Retorno da API {nome_api} enviado para {email_destino}")
        return jsonify({'sucesso': True, 'mensagem': f'E-mail enviado com sucesso para {email_destino}!'})
    except Exception as e:
        logger.error(f"Erro ao enviar retorno da API por e-mail: {str(e)}")
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao enviar: {str(e)}'}), 500


def _montar_email_retorno(api, status_code, caminho_arquivo, pid):
    """Monta o corpo texto/HTML do e-mail de retorno da API, no mesmo padrão
    visual dos demais e-mails do sistema (cabeçalho azul + caixas de resumo)."""
    agora_fmt = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    nome_anexo = os.path.basename(caminho_arquivo)
    metodo = (api.get('method') or 'GET').upper()
    url = api.get('url') or '-'

    corpo_html = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'></head>
<body style='margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif'>
<table width='100%' cellpadding='0' cellspacing='0' style='background:#f3f4f6;padding:24px 0'>
<tr><td align='center'>
<table width='640' cellpadding='0' cellspacing='0' style='background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)'>

  <tr><td style='background:#1e3a5f;padding:24px 28px'>
    <p style='margin:0;color:#93c5fd;font-size:12px;text-transform:uppercase;letter-spacing:1px'>Backoffice Equipe QA</p>
    <h1 style='margin:6px 0 0;color:#ffffff;font-size:20px'>Retorno de Requisição API</h1>
  </td></tr>

  <tr><td style='padding:20px 28px 0'>
    <table cellpadding='0' cellspacing='0' width='100%'>
      <tr>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:34%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>API</p>
          <p style='margin:4px 0 0;font-size:15px;font-weight:bold;color:#1e3a5f'>{api.get('nome','-')}</p>
        </td>
        <td width='8'></td>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:20%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>Método</p>
          <p style='margin:4px 0 0;font-size:15px;font-weight:bold;color:#1e3a5f'>{metodo}</p>
        </td>
        <td width='8'></td>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:18%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>Status</p>
          <p style='margin:4px 0 0;font-size:15px;font-weight:bold;color:#1e3a5f'>{status_code or '-'}</p>
        </td>
        <td width='8'></td>
        <td style='padding:8px 12px;background:#f8fafc;border-radius:6px;text-align:center;width:20%'>
          <p style='margin:0;font-size:11px;color:#6b7280;text-transform:uppercase'>PID</p>
          <p style='margin:4px 0 0;font-size:13px;font-weight:bold;color:#1e3a5f;font-family:monospace'>{pid}</p>
        </td>
      </tr>
    </table>
  </td></tr>

  <tr><td style='padding:16px 28px 0'>
    <p style='margin:0;font-size:12px;color:#6b7280;word-break:break-all'><strong>URL:</strong> {url}</p>
  </td></tr>

  <tr><td style='padding:16px 28px 8px'>
    <div style='background:#f0f9ff;border:1px solid #bae6fd;border-radius:6px;padding:12px 16px'>
      <p style='margin:0;font-size:12px;color:#0369a1'><strong>Anexo:</strong> {nome_anexo}</p>
    </div>
  </td></tr>

  <tr><td style='padding:14px 28px;background:#f8fafc;border-top:1px solid #e5e7eb'>
    <p style='margin:0;font-size:11px;color:#9ca3af'>Gerado em {agora_fmt} &nbsp;|&nbsp; Backoffice Equipe QA</p>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""

    corpo_txt = (
        f"Retorno de Requisição API\n"
        f"API: {api.get('nome','-')} | Método: {metodo} | Status: {status_code or '-'} | PID: {pid}\n"
        f"URL: {url}\n"
        f"Anexo: {nome_anexo}\n"
        f"{'=' * 60}"
    )
    return corpo_txt, corpo_html
