from flask import render_template, request, jsonify
import logging

logger = logging.getLogger('ExtratrorLogs')

def converter_para_array(valor):
    if isinstance(valor, str):
        return [v.strip() for v in valor.split(',') if v.strip()]
    return valor if isinstance(valor, list) else []

def pagina_configurar_pdv(app, ler_properties, config_file):
    logger.debug("Página 'Configurar PDV' acessada")
    props = ler_properties(config_file)
    return render_template('configurar_pdv.html', config_props=props)

def enviar_configuracao_pdv(app, ler_properties, config_file, gerar_pid, enviar_email_gmail):
    logger.info("Requisição de configuração de PDV recebida")
    props = ler_properties(config_file)

    loja = request.form.get('loja', '').strip()
    pdv = request.form.get('pdv', '').strip()
    parametros_str = request.form.get('parametros', '').strip()

    if not loja or not pdv or not parametros_str:
        msg = "Loja, PDV e parâmetros são obrigatórios."
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 400

    parametros_lista = [p.strip() for p in parametros_str.split(',') if p.strip()]
    parametros_join = ', '.join(parametros_lista)

    remetente = props.get('email_envio', '')
    senha = props.get('senha_envio', '')

    pid = gerar_pid()
    assunto = f"[Parametrização PDV] - [{pid}]"
    corpo = f"PID: {pid}\nLoja: {loja}\nPDV: {pdv}\nParametros: {parametros_join}"

    try:
        enviar_email_gmail(remetente, senha, remetente, assunto, corpo)
        msg = f"Configuração de PDV enviada com sucesso! PID: {pid}"
        logger.info(msg)
        return jsonify({'sucesso': True, 'mensagem': msg, 'pid': pid})
    except Exception as e:
        msg = f"Erro ao enviar e-mail de configuração: {str(e)}"
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 500

def verificar_configuracao_pdv(app, ler_properties, config_file, gerar_pid, enviar_email_gmail):
    """Envia e-mail de verificação de configuração para o e-mail digitado no pop-up."""
    logger.info("Requisição de verificação de configuração de PDV recebida")
    props = ler_properties(config_file)

    email_destino = request.form.get('email_destino', '').strip()
    loja = request.form.get('loja', '').strip() or '0007'
    pdv = request.form.get('pdv', '').strip() or '53'

    if not email_destino:
        msg = "E-mail de destino é obrigatório."
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 400

    remetente = props.get('email_envio', '')
    senha = props.get('senha_envio', '')

    pid = gerar_pid()

    assunto = f"[Verificar Parametrização] - [{pid}]"
    corpo = f"PID: {pid}\nDestino: {email_destino}\nLoja: {loja}\nPDV: {pdv}"

    try:
        enviar_email_gmail(remetente, senha, remetente, assunto, corpo)
        msg = f"Solicitação de verificação enviada com sucesso! PID: {pid}"
        logger.info(msg)
        return jsonify({'sucesso': True, 'mensagem': msg, 'pid': pid})
    except Exception as e:
        msg = f"Erro ao enviar e-mail de verificação: {str(e)}"
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 500
