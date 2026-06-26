from flask import Flask, render_template, request, redirect, render_template_string, jsonify
import os, sys, random, string, json, smtplib, webbrowser, zipfile, logging
from email.mime.text import MIMEText
import oracledb, csv
from datetime import datetime
from logger import logger
from parametrizacao_pdv import pagina_configurar_pdv, enviar_configuracao_pdv, verificar_configuracao_pdv
from request_api import (pagina_requisicao_api,fazer_requisicao_api,salvar_retorno)

# BUNDLE_DIR: recursos somente-leitura empacotados (templates, static)
# APP_DIR:    pasta do .exe / pasta do script — para arquivos editáveis (config, output, log)
if getattr(sys, 'frozen', False):
    BUNDLE_DIR = sys._MEIPASS
    APP_DIR    = os.path.dirname(sys.executable)
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR    = BUNDLE_DIR

app = Flask(__name__,
            template_folder=os.path.join(BUNDLE_DIR, 'templates'),
            static_folder=os.path.join(BUNDLE_DIR, 'static'))

PROPS_DIR   = os.path.join(APP_DIR, 'properties')
CONFIG_FILE = os.path.join(PROPS_DIR, 'config.properties')
SECURE_FILE = os.path.join(PROPS_DIR, 'secure.properties')
OUTPUT_DIR  = os.path.join(APP_DIR, 'output')

# Redireciona werkzeug para o mesmo arquivo de log quando empacotado
if getattr(sys, 'frozen', False):
    from logger import _LOG_PATH
    _wz_handler = logging.FileHandler(_LOG_PATH, encoding='utf-8')
    _wz_handler.setFormatter(logging.Formatter(
        '[%(asctime)s] - %(levelname)s - %(name)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    for _name in ('werkzeug', 'flask.app'):
        _lg = logging.getLogger(_name)
        _lg.setLevel(logging.INFO)
        _lg.addHandler(_wz_handler)
    # Redireciona stdout/stderr para o log (sem console visível)
    sys.stdout = open(_LOG_PATH, 'a', encoding='utf-8')
    sys.stderr = sys.stdout

logger.info("Aplicação iniciada")


def ler_properties(arquivo):
    props = {}
    if not os.path.exists(arquivo):
        logger.warning(f"Arquivo de configuração não encontrado: {arquivo}")
        return {}
    try:
        with open(arquivo, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    props[key.strip()] = value.strip()  # SEMPRE salva como STRING
        logger.debug(f"Arquivo {arquivo} carregado com sucesso")      
        return props
    except Exception as e:
        logger.error(f"Erro ao ler arquivo {arquivo}: {str(e)}")
        return {}

def ler_config_completo(*args):
    """Retorna config.properties + secure.properties mesclados (secure tem precedência).
    Aceita argumentos opcionais para ser compatível com a assinatura de ler_properties."""
    props = ler_properties(CONFIG_FILE)
    props.update(ler_properties(SECURE_FILE))
    return props

def get_oracle_conn(props):
    try:
        dsn = f"{props['oracle_host']}:{props['oracle_port']}/{props['oracle_service']}"
        logger.info(f"Conexão Oracle estabelecida: {props['oracle_host']}")
        return oracledb.connect(
            user=props['oracle_user'],
            password=props['oracle_password'],
            dsn=dsn
        )
    except Exception as e:
        logger.error(f"Erro ao conectar ao Oracle: {str(e)}")
        raise

PROP_SECTIONS = [
    ("# === Abas - controla quais abas ficam visíveis na interface (true/false) ===",
     lambda k: k.startswith('tab.')),
    ("# === E-mails - lista de destinatários disponíveis ===",
     lambda k: k == 'emails_destino'),
    ("# === Lojas e PDVs disponíveis para seleção ===",
     lambda k: k == 'stores' or k.endswith('_pdvs')),
    ("# === Logs disponíveis para solicitação ===",
     lambda k: k == 'logs'),
    ("# === Parâmetros de configuração de PDV ===",
     lambda k: k == 'PARAMETROS_PDV'),
    ("# === Oracle - consultas SQL disponíveis ===",
     lambda k: k.startswith('oracle_query')),
]

def salvar_properties(arquivo, props):
    try:
        escritas = set()
        linhas = []
        for comentario, pertence in PROP_SECTIONS:
            chaves_secao = [k for k in props if pertence(k)]
            if not chaves_secao:
                continue
            linhas.append(f"{comentario}\n")
            for key in chaves_secao:
                value = props[key]
                if isinstance(value, list):
                    value = ','.join(str(v) for v in value)
                elif isinstance(value, str):
                    value = value.strip()
                else:
                    value = str(value)
                linhas.append(f"{key}={value}\n")
                escritas.add(key)
            linhas.append("\n")

        # Chaves que não se encaixam em nenhuma seção
        restantes = [k for k in props if k not in escritas]
        if restantes:
            linhas.append("# === Outras configurações ===\n")
            for key in restantes:
                value = props[key]
                if isinstance(value, list):
                    value = ','.join(str(v) for v in value)
                else:
                    value = str(value).strip()
                linhas.append(f"{key}={value}\n")

        with open(arquivo, 'w', encoding='utf-8') as f:
            f.writelines(linhas)
        logger.info(f"Arquivo {arquivo} salvo com sucesso")
    except Exception as e:
        logger.error(f"Erro ao salvar arquivo {arquivo}: {str(e)}")

def converter_data_para_oracle(data_ddmmyyyy):
    """Converte DD/MM/YYYY para YYYYMMDD"""
    if not data_ddmmyyyy:
        return ''
    try:
        dia, mes, ano = data_ddmmyyyy.split('/')
        data_oracle = f"{ano}{mes}{dia}"
        logger.debug(f"Data convertida: {data_ddmmyyyy} -> {data_oracle}")
        return data_oracle
    except Exception as e:
        logger.error(f"Erro ao converter data {data_ddmmyyyy}: {str(e)}")
        return data_ddmmyyyy

def exportar_consulta_para_csv(nome_consulta, loja='', pdv='', nsu='', data='', formato='csv', separador='.'):
    props = ler_config_completo()
    sql = props.get(f'oracle_query.{nome_consulta}')
    if not sql:
        erro = f"Consulta '{nome_consulta}' não encontrada no config.properties"
        logger.error(erro)
        raise ValueError(erro)

    logger.info(f"Iniciando exportação da consulta: {nome_consulta}")
    logger.debug(f"Filtros - Loja: {loja}, PDV: {pdv}, NSU: {nsu}, Data: {data}")
    logger.debug(f"Formato: {formato}, Separador: {separador}")

    # Converter data de DD/MM/YYYY para YYYYMMDD
    data_oracle = converter_data_para_oracle(data)
    
    # Substituir variáveis
    sql = sql.replace('$LOJA', loja if loja else '0')
    sql = sql.replace('$PDV', pdv if pdv else '0')
    sql = sql.replace('$NSU', nsu if nsu else '')
    sql = sql.replace('$DATA', data_oracle if data_oracle else '')
    
    logger.debug(f"SQL final: {sql}")
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d%H%M%S')
    
    # Definir extensão baseado no formato
    extensao = 'xlsx' if formato == 'xlsx' else 'csv'
    caminho_arquivo = os.path.join(OUTPUT_DIR, f'{nome_consulta}_{ts}.{extensao}')

    try:
        conn = get_oracle_conn(props)
        cur = conn.cursor()
        cur.execute(sql)
        colunas = [d[0] for d in cur.description]
        dados = cur.fetchall()
        
        if formato == 'xlsx':
            # Exportar para XLSX
            exportar_para_xlsx(caminho_arquivo, colunas, dados, separador)
        else:
            # Exportar para CSV
            exportar_para_csv(caminho_arquivo, colunas, dados, separador)
        
        linhas = len(dados)
        cur.close()
        conn.close()
        
        logger.info(f"Exportação concluída: {caminho_arquivo} ({linhas} linhas)")
        return caminho_arquivo
    except Exception as e:
        logger.error(f"Erro durante exportação de {nome_consulta}: {str(e)}")
        raise

def exportar_para_csv(caminho, colunas, dados, separador='.'):
    """Exporta dados para CSV com separador decimal configurável e suporte a LOBs"""
    try:
        with open(caminho, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f, delimiter=';', lineterminator='\n')
            writer.writerow(colunas)
            
            for row in dados:
                row_processada = []
                for valor in row:
                    # Converter LOBs
                    if hasattr(valor, 'read'):  # LOB object
                        try:
                            valor = valor.read()
                            logger.debug("LOB convertido")
                        except Exception as e:
                            logger.warning(f"Erro ao ler LOB: {str(e)}")
                            valor = "[LOB não lido]"
                    
                    if isinstance(valor, bytes):
                        # Se for bytes, tenta decodificar
                        try:
                            valor = valor.decode('utf-8')
                        except:
                            valor = "[Dados binários]"
                    
                    if isinstance(valor, float):
                        # Formatar número com separador decimal escolhido
                        valor_str = str(valor).replace('.', ',') if separador == ',' else str(valor)
                        row_processada.append(valor_str)
                    else:
                        row_processada.append(str(valor) if valor is not None else '')
                writer.writerow(row_processada)
        
        logger.debug(f"Arquivo CSV criado: {caminho}")
    except Exception as e:
        logger.error(f"Erro ao exportar para CSV: {str(e)}")
        raise


def exportar_para_xlsx(caminho, colunas, dados, separador='.'):
    """Exporta dados para XLSX com separador decimal configurável e suporte a LOBs"""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Dados"
        
        # Adicionar header com estilo
        header_fill = PatternFill(start_color="4a90e2", end_color="4a90e2", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF")
        
        for col_num, coluna in enumerate(colunas, 1):
            cell = ws.cell(row=1, column=col_num)
            cell.value = coluna
            cell.fill = header_fill
            cell.font = header_font
        
        # Adicionar dados
        for row_num, row in enumerate(dados, 2):
            for col_num, valor in enumerate(row, 1):
                cell = ws.cell(row=row_num, column=col_num)
                
                # Converter LOBs
                if hasattr(valor, 'read'):  # LOB object
                    try:
                        valor = valor.read()
                        logger.debug(f"LOB convertido em linha {row_num}, coluna {col_num}")
                    except Exception as e:
                        logger.warning(f"Erro ao ler LOB: {str(e)}")
                        valor = "[LOB não lido]"
                
                if isinstance(valor, bytes):
                    # Se for bytes, tenta decodificar
                    try:
                        valor = valor.decode('utf-8')
                    except:
                        valor = "[Dados binários]"
                
                if isinstance(valor, float):
                    # Formatar número com separador decimal
                    if separador == ',':
                        cell.value = str(valor).replace('.', ',')
                    else:
                        cell.value = valor
                else:
                    cell.value = valor if valor is not None else ''
        
        # Auto ajustar largura das colunas
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column_letter].width = adjusted_width
        
        wb.save(caminho)
        logger.debug(f"Arquivo XLSX criado: {caminho}")
    except ImportError:
        logger.error("Módulo openpyxl não instalado. Execute: pip install openpyxl")
        raise ValueError("Para exportar em XLSX, instale openpyxl: pip install openpyxl")
    except Exception as e:
        logger.error(f"Erro ao exportar para XLSX: {str(e)}")
        raise

def compactar_csvs(caminhos_arquivo, nome_zip):
    """Compacta múltiplos arquivos em um ZIP com compressão e remove os originais"""
    try:
        with zipfile.ZipFile(nome_zip, 'w', compression=zipfile.ZIP_DEFLATED) as zipf:
            for arquivo in caminhos_arquivo:
                if os.path.exists(arquivo):
                    arcname = os.path.basename(arquivo)
                    zipf.write(arquivo, arcname=arcname)
                    logger.debug(f"Arquivo adicionado ao ZIP: {arcname}")
        
        logger.info(f"ZIP criado com sucesso: {nome_zip}")
        
        # Remover arquivos originais após compactação bem-sucedida
        for arquivo in caminhos_arquivo:
            try:
                if os.path.exists(arquivo):
                    os.remove(arquivo)
                    logger.debug(f"Arquivo removido: {arquivo}")
            except Exception as e:
                logger.warning(f"Erro ao remover arquivo {arquivo}: {str(e)}")
        
        return nome_zip
    except Exception as e:
        logger.error(f"Erro ao compactar arquivos: {str(e)}")
        raise

def obter_apis_do_props(props):
    apis = {}
    for key, value in props.items():
        if key.startswith('api.') and '.' in key:
            partes = key.split('.')
            if len(partes) < 3:
                continue
            nome = partes[1]
            campo = partes[2]  # url, header, token, method, params
            if nome not in apis:
                apis[nome] = {'nome': nome}
            apis[nome][campo] = value
    return sorted(apis.values(), key=lambda a: a['nome'])


@app.route('/')
def index():
    props = ler_properties(CONFIG_FILE)  # tabs só existem em config.properties
    tabs = {
        'solicitar_logs': props.get('tab.solicitar_logs', 'true').lower() == 'true',
        'exportar_oracle': props.get('tab.exportar_oracle', 'true').lower() == 'true',
        'requisicao_api': props.get('tab.requisicao_api', 'true').lower() == 'true',
        'configurar_pdv': props.get('tab.configurar_pdv', 'true').lower() == 'true',
        'configuracoes': props.get('tab.configuracoes', 'true').lower() == 'true',
    }
    return render_template('index.html', tabs=tabs)

@app.route('/solicitar-logs')
def solicitar_logs_page():
    logger.debug("Página 'Solicitar Logs' acessada")
    return render_template('solicitar_logs.html', config_props=ler_config_completo())

@app.route('/exportar-oracle')
def exportar_oracle_page():
    logger.debug("Página 'Exportar Dados Oracle' acessada")
    return render_template('exportar_oracle.html', config_props=ler_config_completo())

@app.route('/configurar-pdv')
def configurar_pdv_page():
    return pagina_configurar_pdv(app, ler_config_completo, CONFIG_FILE)

@app.route('/enviar-config-pdv', methods=['POST'])
def enviar_config_pdv_route():
    return enviar_configuracao_pdv(app, ler_config_completo, CONFIG_FILE, gerar_pid, enviar_email_gmail)

@app.route('/verificar-config-pdv', methods=['POST'])
def verificar_config_pdv_route():
    return verificar_configuracao_pdv(app, ler_config_completo, CONFIG_FILE, gerar_pid, enviar_email_gmail)

@app.route('/requisicao-api')
def requisicao_api_page():
    return pagina_requisicao_api(app, ler_config_completo, CONFIG_FILE)

@app.route('/fazer-requisicao-api', methods=['POST'])
def fazer_requisicao_api_route():
    return fazer_requisicao_api(app, ler_config_completo, CONFIG_FILE)

@app.route('/salvar-retorno-api', methods=['POST'])
def salvar_retorno_route():
    return salvar_retorno(app, ler_config_completo, CONFIG_FILE)


def converterParaArray(valor):
    if isinstance(valor, str):
        return [v.strip() for v in valor.split(',') if v.strip()]
    return valor if isinstance(valor, list) else []

@app.route('/config', methods=['GET', 'POST'])
def config():
    props = ler_properties(CONFIG_FILE)  # apenas props editáveis

    if request.method == 'POST':
        # Abas
        for tab in ['solicitar_logs', 'exportar_oracle', 'requisicao_api', 'configurar_pdv', 'configuracoes']:
            props[f'tab.{tab}'] = 'true' if request.form.get(f'tab_{tab}') else 'false'

        # E-mails destino (lista de destinatários — não sensível)
        emails_raw = request.form.get('emails_destino', '').replace('\n', ',')
        props['emails_destino'] = ','.join(e.strip() for e in emails_raw.split(',') if e.strip())

        # Lojas e PDVs
        stores_str = request.form.get('stores', props.get('stores', ''))
        props['stores'] = stores_str
        for loja in [l.strip() for l in stores_str.split(',') if l.strip()]:
            props[f'{loja}_pdvs'] = request.form.get(f'pdvs_{loja}', props.get(f'{loja}_pdvs', ''))

        # Logs
        props['logs'] = request.form.get('logs', props.get('logs', ''))

        # Parâmetros PDV
        parametros_pdv_str = request.form.get('parametros_pdv', '').strip()
        if parametros_pdv_str:
            props['PARAMETROS_PDV'] = parametros_pdv_str

        # Oracle – adicionar nova consulta
        nome_nova_consulta = request.form.get('oracle_query_name', '').strip()
        sql_nova_consulta  = request.form.get('oracle_query_sql', '').strip()
        if nome_nova_consulta and sql_nova_consulta:
            lista = [n.strip() for n in props.get('oracle_query_names', '').split(',') if n.strip()]
            if nome_nova_consulta not in lista:
                lista.append(nome_nova_consulta)
            props['oracle_query_names'] = ','.join(lista)
            props[f'oracle_query.{nome_nova_consulta}'] = sql_nova_consulta

        # Oracle – editar consultas existentes
        for nome in [n.strip() for n in props.get('oracle_query_names', '').split(',') if n.strip()]:
            key = f'oracle_query_sql_{nome}'
            if key in request.form:
                props[f'oracle_query.{nome}'] = request.form.get(key, '')

        # Oracle – remover consulta
        remover = request.form.get('remover_consulta', '')
        if remover:
            nomes = [n.strip() for n in props.get('oracle_query_names', '').split(',') if n.strip()]
            props['oracle_query_names'] = ','.join(n for n in nomes if n != remover)
            props.pop(f'oracle_query.{remover}', None)

        salvar_properties(CONFIG_FILE, props)
        return redirect('/config?saved=1')

    # GET – preparar dados (somente props editáveis para o formulário)
    lojas = converterParaArray(props.get('stores', ''))
    pdvs_dict = {f'{loja}_pdvs': converterParaArray(props.get(f'{loja}_pdvs', '')) for loja in lojas}
    emails_destino_lista = '\n'.join(e.strip() for e in props.get('emails_destino', '').split(',') if e.strip())

    return render_template(
        'config.html',
        lojas=lojas,
        stores_str=','.join(lojas),
        pdvs_dict=pdvs_dict,
        logs_str=','.join(converterParaArray(props.get('logs', ''))),
        emails_destino_lista=emails_destino_lista,
        config_props=props,
    )

       

@app.route('/oracle_export', methods=['POST'])
def oracle_export():
    logger.info("Requisição de exportação Oracle recebida")
    
    logger.debug(f"Dados recebidos: {request.form}")
    
    consultas_str = request.form.get('consultas', '')
    loja = request.form.get('loja', '')
    pdv = request.form.get('pdv', '')
    nsu = request.form.get('nsu', '')
    data = request.form.get('data', '')
    formato = request.form.get('formato', 'csv')
    separador = request.form.get('separador', '.')
    
    logger.debug(f"consultas_str bruto: {repr(consultas_str)}")
    
    if not consultas_str:
        erro = "Nenhuma consulta foi selecionada"
        logger.error(erro)
        return jsonify({'sucesso': False, 'mensagem': erro}), 400
    
    consultas = [c.strip() for c in consultas_str.split(',') if c.strip()]
    
    logger.debug(f"Consultas selecionadas: {consultas}")
    logger.debug(f"Parâmetros: loja={loja}, pdv={pdv}, nsu={nsu}, data={data}, formato={formato}, separador={separador}")
    
    if not consultas:
        erro = "Lista de consultas vazia após processamento"
        logger.error(erro)
        return jsonify({'sucesso': False, 'mensagem': erro}), 400
    
    try:
        caminhos_arquivos = []
        
        for nome_consulta in consultas:
            try:
                logger.info(f"Processando consulta: {nome_consulta}")
                caminho = exportar_consulta_para_csv(nome_consulta, loja, pdv, nsu, data, formato, separador)
                caminhos_arquivos.append(caminho)
            except Exception as e:
                logger.error(f"Erro ao exportar {nome_consulta}: {str(e)}")
                raise
        
        if not caminhos_arquivos:
            raise ValueError("Nenhuma consulta foi exportada com sucesso")
        
        # Se apenas uma consulta, retornar diretamente (sem ZIP)
        if len(caminhos_arquivos) == 1:
            logger.info(f"Uma consulta exportada: {caminhos_arquivos[0]}")
            return jsonify({'sucesso': True, 'caminho': caminhos_arquivos[0]})
        
        # Se múltiplas consultas, compactar em ZIP e remover originais
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d%H%M%S')
        nome_zip = os.path.join(OUTPUT_DIR, f'OracleDB-{loja}-{pdv}-{ts}.zip')
        
        compactar_csvs(caminhos_arquivos, nome_zip)
        logger.info(f"Exportação múltipla concluída: {nome_zip} ({len(caminhos_arquivos)} arquivos)")
        
        return jsonify({'sucesso': True, 'caminho': nome_zip})
    except Exception as e:
        logger.error(f"Erro na exportação Oracle: {str(e)}")
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500
    

def gerar_pid(tamanho=10):
    caracteres = string.ascii_letters + string.digits
    return ''.join(random.choice(caracteres) for _ in range(tamanho))


@app.route('/solicitar', methods=['POST'])
def solicitar():
    logger.info("Requisição de solicitação de logs recebida")
    props = ler_config_completo()

    loja = request.form['loja']
    pdv = request.form['pdv']
    logs = request.form.getlist('logs')
    email_destino = request.form['email_destino']

    logger.debug(f"Dados da solicitação - Loja: {loja}, PDV: {pdv}, Email: {email_destino}, Logs: {logs}")

    pid = gerar_pid()

    if loja == 'server_152':
        assunto = f"[Solicitação linx-webservices] - [{pid}]"
        corpo = f"PID: {pid}\nDestino: {email_destino}\nLoja: {loja}\nPDV: {pdv}\nLogs: linx-webservices"
    else:
        assunto = f"[Solicitação Log] - [{pid}]"
        corpo = f"PID: {pid}\nDestino: {email_destino}\nLoja: {loja}\nPDV: {pdv}\nLogs: {', '.join(logs)}"

    try:
        remetente = props.get('email_envio', '')
        enviar_email_gmail(remetente, props.get('senha_envio', ''), remetente, assunto, corpo)
        logger.info(f"Solicitação enviada com sucesso. PID: {pid}")
        return jsonify({'sucesso': True, 'mensagem': f"Solicitação enviada com sucesso! PID: {pid}", 'pid': pid})
    except Exception as e:
        logger.error(f"Erro ao enviar solicitação: {str(e)}")
        return jsonify({'sucesso': False, 'mensagem': f"Erro ao enviar solicitação: {str(e)}"}), 500
    
# Função para enviar email via Gmail SMTP
def enviar_email_gmail(remetente, senha, destinatario, assunto, corpo):

    msg = MIMEText(corpo, 'plain', 'utf-8')
    msg['Subject'] = assunto
    msg['From'] = remetente
    msg['To'] = remetente

    logger.debug(f"Remetente: {remetente} | Destinatário: {destinatario}")

    smtp_server = "smtp.gmail.com"
    smtp_port = 587

    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(remetente, senha)
        server.sendmail(remetente, destinatario, msg.as_string())




if __name__ == '__main__':
    logger.info("Iniciando servidor Flask")
    import threading, time, webview

    def _iniciar_flask():
        app.run(debug=False, port=5000, use_reloader=False)

    flask_thread = threading.Thread(target=_iniciar_flask, daemon=True)
    flask_thread.start()

    # Aguarda Flask ficar pronto
    time.sleep(1.5)

    logger.info("Abrindo janela da aplicação")
    janela = webview.create_window(
        'Backoffice Equipe QA',
        'http://localhost:5000',
        width=1366,
        height=860,
        min_size=(900, 600),
    )
    webview.start()
    # webview.start() bloqueia até a janela ser fechada — ao sair, o processo encerra
    logger.info("Janela fechada — encerrando aplicação")
    sys.exit(0)
