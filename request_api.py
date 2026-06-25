# request_api.py
import os
import sys as _sys
import requests
import logging
from datetime import datetime
from flask import render_template, request, jsonify
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger('ExtratrorLogs')

# Resolve o diretório base igual ao extrator_logs.py
if getattr(_sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(_sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

OUTPUT_DIR = os.path.join(_BASE_DIR, 'output')

def ler_properties(arquivo):
    """Função de exemplo; substitua pela sua função real de leitura de properties."""
    props = {}
    try:
        with open(arquivo, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    props[key.strip()] = value.strip()
    except Exception as e:
        logger.error(f"Erro ao ler {arquivo}: {str(e)}")
    return props

def obter_apis_props(props):
    apis = {}
    for key, value in props.items():
        if not key.startswith("api."):
            continue

        if ".url" in key:
            nome = key.split(".")[1]
            apis.setdefault(nome, {"nome": nome})["url"] = value
        elif ".header" in key:
            nome = key.split(".")[1]
            apis.setdefault(nome, {"nome": nome})["header"] = value
        elif ".token" in key:
            nome = key.split(".")[1]
            apis.setdefault(nome, {"nome": nome})["token"] = value
        elif ".params" in key:  # NOVO
            nome = key.split(".")[1]
            apis.setdefault(nome, {"nome": nome})["params"] = value
        elif ".method" in key:  # opcional, se quiser usar
            nome = key.split(".")[1]
            apis.setdefault(nome, {"nome": nome})["method"] = value

    return list(apis.values())

def pagina_requisicao_api(app, ler_properties, config_file):
    """Retorna a página da aba Requisição API"""
    logger.debug("Página 'Requisição API' acessada")
    props = ler_properties(config_file)
    apis = obter_apis_props(props)
    return render_template('requisicao_api.html', apis=apis)

def parse_header_string(header_str: str) -> dict:
    """
    Converte uma string do tipo:
      'Apikey=XXXXX;Accept=application/json;Content-Type=application/json'
    em um dict:
      {'Apikey': 'XXXXX', 'Accept': 'application/json', 'Content-Type': 'application/json'}
    """
    headers = {}
    if not header_str:
        return headers

    partes = [p.strip() for p in header_str.split(';') if p.strip()]
    for parte in partes:
        if '=' in parte:
            k, v = parte.split('=', 1)
            headers[k.strip()] = v.strip()
    return headers

def fazer_requisicao_api(app, ler_properties, config_file):
    """Faz a chamada à API selecionada"""
    logger.info("Requisição API recebida")
    props = ler_properties(config_file)
    apis = obter_apis_props(props)
    
    nome_api = request.form.get('api', '').strip()
    body = request.form.get('body', '').strip()
    params_tela = request.form.get('url_params', '').strip()  
    
    if not nome_api:
        return jsonify({'sucesso': False, 'mensagem': 'Selecione uma API.'}), 400
    
    api = next((a for a in apis if a['nome'] == nome_api), None)
    if not api:
        return jsonify({'sucesso': False, 'mensagem': 'API não encontrada.'}), 400
    
    url_base = api.get('url', '').strip()
    if not url_base:
        return jsonify({'sucesso': False, 'mensagem': 'URL da API não configurada.'}), 400

    method = api.get('method', '').strip()
    if not method:
        return jsonify({'sucesso': False, 'mensagem': 'Method da API não configurada.'}), 400

    # Params padrão da API (config)
    paramscfg = api.get("params", "").strip()
    paramstela = request.form.get("urlparams", "").strip()

    logger.debug(f"*** Method API URL: {nome_api} ({method}) ***")

    queryparts = []

    if paramscfg:
        queryparts.append(paramscfg.lstrip("?&"))

    if paramstela:
        queryparts.append(paramstela.lstrip("?&"))

    if queryparts:
        url_final = f"{url_base}?{'&'.join(queryparts)}"
    else:
        url_final = url_base

    headers = {}


    # Header configurado em config.properties
    if 'header' in api:
        headers_from_cfg = parse_header_string(api['header'])
        headers.update(headers_from_cfg)

    # Token configurado (se quiser continuar com Bearer)
    if 'token' in api and 'Authorization' not in headers:
        headers['Authorization'] = f"Bearer {api['token']}"

    logger.debug(f"Chamando API {nome_api} ({url_final}) com headers: {headers}")
    
    try:
        logger.debug(f"Chamando API URL: {nome_api} ({url_final})")
        logger.debug(f"Chamando API Header: {nome_api} ({headers})")
        response = requests.get(url_final, headers=headers, timeout=30, verify=False)
        # response = requests.post(url, data=body, headers=headers, timeout=30, verify=False)
        
        retorno = {
            'status_code': response.status_code,
            'headers': dict(response.headers),
            'body': response.text
        }
        
        logger.info(f"API {nome_api} retornou status {response.status_code}")
        return jsonify({'sucesso': True, 'retorno': retorno})
    except Exception as e:
        logger.error(f"Erro ao chamar API {nome_api}: {str(e)}")
        return jsonify({'sucesso': False, 'mensagem': f'Erro na chamada: {str(e)}'}), 500

def salvar_retorno(app, ler_properties, config_file):
    """Salva o retorno da API em um arquivo"""
    logger.info("Requisição para salvar retorno da API")
    retorno_json = request.form.get('retorno', '').strip()
    
    if not retorno_json:
        return jsonify({'sucesso': False, 'mensagem': 'Nenhum retorno para salvar.'}), 400
    
    try:
        import json
        retorno = json.loads(retorno_json)
    except Exception as e:
        logger.error(f"Erro ao decodificar retorno JSON: {str(e)}")
        return jsonify({'sucesso': False, 'mensagem': 'Retorno inválido.'}), 400
    
    try:
        import os
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        nome_arquivo = f"retorno_api_{ts}.json"
        caminho = os.path.join(OUTPUT_DIR, nome_arquivo)
        
        with open(caminho, 'w', encoding='utf-8') as f:
            json.dump(retorno, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Retorno da API salvo em: {caminho}")
        return jsonify({'sucesso': True, 'caminho': caminho})
    except Exception as e:
        logger.error(f"Erro ao salvar retorno da API: {str(e)}")
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao salvar: {str(e)}'}), 500
