from flask import render_template, request, jsonify
import logging

logger = logging.getLogger('ExtratrorLogs')

def converter_para_array(valor):
    if isinstance(valor, str):
        return [v.strip() for v in valor.split(',') if v.strip()]
    return valor if isinstance(valor, list) else []

def pagina_configurar_pdv(app, ler_properties, config_file):
    logger.debug("Página 'Manutenção PDV' acessada")
    props = ler_properties(config_file)
    return render_template('configurar_pdv.html', config_props=props)

def enviar_configuracao_pdv(app, ler_properties, config_file, gerar_pid, enviar_email_gmail):
    logger.info("Requisição de configuração de PDV recebida")
    props = ler_properties(config_file)

    selecao = request.form.get('selecao', '').strip()
    email_destino = request.form.get('email_destino', '').strip()
    parametros_str = request.form.get('parametros', '').strip()

    if not selecao:
        msg = "Nenhuma loja/PDV selecionado."
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 400

    if not parametros_str:
        msg = "Selecione pelo menos um parâmetro."
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 400

    if not email_destino:
        msg = "E-mail de destino é obrigatório."
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 400

    parametros_lista = [p.strip() for p in parametros_str.split(',') if p.strip()]
    parametros_join = ', '.join(parametros_lista)

    linhas = []
    for parte in selecao.split('|'):
        if ':' in parte:
            loja, pdvs = parte.split(':', 1)
            linhas.append(f"  Loja {loja.strip()} - PDVs: {pdvs.strip()}")
        else:
            linhas.append(f"  {parte.strip()}")
    selecao_formatada = '\n'.join(linhas)

    remetente = props.get('email_envio', '')
    senha = props.get('senha_envio', '')

    pid = gerar_pid()
    assunto = f"[Parametrização PDV] - [{pid}]"
    corpo = (
        f"PID: {pid}\n"
        f"Selecao: {selecao}\n"
        f"Parametros: {parametros_join}\n\n"
        f"PDVs para parametrização:\n{selecao_formatada}"
    )

    try:
        enviar_email_gmail(remetente, senha, remetente, assunto, corpo)
        msg = f"Configuração de PDV enviada com sucesso! PID: {pid}"
        logger.info(msg)
        return jsonify({'sucesso': True, 'mensagem': msg, 'pid': pid})
    except Exception as e:
        msg = f"Erro ao enviar e-mail de configuração: {str(e)}"
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 500

def relatorio_parametrizacao(app, ler_properties, config_file, gerar_pid, enviar_email_gmail):
    """Envia e-mail ao agente solicitando relatório de parametrização para os PDVs escolhidos."""
    logger.info("Requisição de relatório de parametrização recebida")
    props = ler_properties(config_file)

    selecao = request.form.get('selecao', '').strip()
    email_destino = request.form.get('email_destino', '').strip()

    if not selecao:
        msg = "Nenhuma loja/PDV selecionado."
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 400

    if not email_destino:
        msg = "E-mail de destino é obrigatório."
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 400

    # Formata a seleção para o corpo do e-mail: "0007:53,277|0019:192,194" → linhas legíveis
    linhas = []
    for parte in selecao.split('|'):
        if ':' in parte:
            loja, pdvs = parte.split(':', 1)
            linhas.append(f"  Loja {loja.strip()} - PDVs: {pdvs.strip()}")
        else:
            linhas.append(f"  {parte.strip()}")
    selecao_formatada = '\n'.join(linhas)

    remetente = props.get('email_envio', '')
    senha = props.get('senha_envio', '')

    pid = gerar_pid()
    assunto = f"[Relatório Parametrização] - [{pid}]"
    corpo = (
        f"PID: {pid}\n"
        f"Destino: {email_destino}\n"
        f"Selecao: {selecao}\n\n"
        f"PDVs para relatório:\n{selecao_formatada}"
    )

    try:
        enviar_email_gmail(remetente, senha, remetente, assunto, corpo)
        msg = f"Solicitação de relatório enviada com sucesso! PID: {pid}"
        logger.info(msg)
        return jsonify({'sucesso': True, 'mensagem': msg, 'pid': pid})
    except Exception as e:
        msg = f"Erro ao enviar e-mail de relatório: {str(e)}"
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 500


def versao_pdv(app, ler_properties, config_file, gerar_pid, enviar_email_gmail):
    """Envia e-mail ao agente solicitando a versão dos PDVs escolhidos."""
    logger.info("Requisição de versão de PDV recebida")
    props = ler_properties(config_file)

    selecao = request.form.get('selecao', '').strip()
    email_destino = request.form.get('email_destino', '').strip()

    if not selecao:
        msg = "Nenhuma loja/PDV selecionado."
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 400

    if not email_destino:
        msg = "E-mail de destino é obrigatório."
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 400

    linhas = []
    for parte in selecao.split('|'):
        if ':' in parte:
            loja, pdvs = parte.split(':', 1)
            linhas.append(f"  Loja {loja.strip()} - PDVs: {pdvs.strip()}")
        else:
            linhas.append(f"  {parte.strip()}")
    selecao_formatada = '\n'.join(linhas)

    remetente = props.get('email_envio', '')
    senha = props.get('senha_envio', '')

    pid = gerar_pid()
    assunto = f"[Status PDV] - [{pid}]"
    corpo = (
        f"PID: {pid}\n"
        f"Destino: {email_destino}\n"
        f"Selecao: {selecao}\n\n"
        f"PDVs para consulta de status:\n{selecao_formatada}"
    )

    try:
        enviar_email_gmail(remetente, senha, remetente, assunto, corpo)
        msg = f"Solicitação de versão enviada com sucesso! PID: {pid}"
        logger.info(msg)
        return jsonify({'sucesso': True, 'mensagem': msg, 'pid': pid})
    except Exception as e:
        msg = f"Erro ao enviar e-mail de versão: {str(e)}"
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 500


def reiniciar_pdv(app, ler_properties, config_file, gerar_pid, enviar_email_gmail):
    """Envia e-mail ao agente solicitando reinicialização dos PDVs escolhidos."""
    logger.info("Requisição de reinicialização de PDV recebida")
    props = ler_properties(config_file)

    selecao = request.form.get('selecao', '').strip()
    email_destino = request.form.get('email_destino', '').strip()

    if not selecao:
        msg = "Nenhuma loja/PDV selecionado."
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 400

    if not email_destino:
        msg = "E-mail de destino é obrigatório."
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 400

    linhas = []
    for parte in selecao.split('|'):
        if ':' in parte:
            loja, pdvs = parte.split(':', 1)
            linhas.append(f"  Loja {loja.strip()} - PDVs: {pdvs.strip()}")
        else:
            linhas.append(f"  {parte.strip()}")
    selecao_formatada = '\n'.join(linhas)

    remetente = props.get('email_envio', '')
    senha = props.get('senha_envio', '')

    pid = gerar_pid()
    assunto = f"[Reiniciar PDV] - [{pid}]"
    corpo = (
        f"PID: {pid}\n"
        f"Destino: {email_destino}\n"
        f"Selecao: {selecao}\n\n"
        f"PDVs para reinicialização:\n{selecao_formatada}"
    )

    try:
        enviar_email_gmail(remetente, senha, remetente, assunto, corpo)
        msg = f"Solicitação de reinicialização enviada com sucesso! PID: {pid}"
        logger.info(msg)
        return jsonify({'sucesso': True, 'mensagem': msg, 'pid': pid})
    except Exception as e:
        msg = f"Erro ao enviar e-mail de reinicialização: {str(e)}"
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 500


def fechar_pdv(app, ler_properties, config_file, gerar_pid, enviar_email_gmail):
    """Envia e-mail ao agente solicitando encerramento dos PDVs escolhidos."""
    logger.info("Requisição de encerramento de PDV recebida")
    props = ler_properties(config_file)

    selecao = request.form.get('selecao', '').strip()
    email_destino = request.form.get('email_destino', '').strip()

    if not selecao:
        msg = "Nenhuma loja/PDV selecionado."
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 400

    if not email_destino:
        msg = "E-mail de destino é obrigatório."
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 400

    linhas = []
    for parte in selecao.split('|'):
        if ':' in parte:
            loja, pdvs = parte.split(':', 1)
            linhas.append(f"  Loja {loja.strip()} - PDVs: {pdvs.strip()}")
        else:
            linhas.append(f"  {parte.strip()}")
    selecao_formatada = '\n'.join(linhas)

    remetente = props.get('email_envio', '')
    senha = props.get('senha_envio', '')

    pid = gerar_pid()
    assunto = f"[Fechar PDV] - [{pid}]"
    corpo = (
        f"PID: {pid}\n"
        f"Destino: {email_destino}\n"
        f"Selecao: {selecao}\n\n"
        f"PDVs para encerramento:\n{selecao_formatada}"
    )

    try:
        enviar_email_gmail(remetente, senha, remetente, assunto, corpo)
        msg = f"Solicitação de encerramento enviada com sucesso! PID: {pid}"
        logger.info(msg)
        return jsonify({'sucesso': True, 'mensagem': msg, 'pid': pid})
    except Exception as e:
        msg = f"Erro ao enviar e-mail de encerramento: {str(e)}"
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 500


def verificar_configuracao_pdv(app, ler_properties, config_file, gerar_pid, enviar_email_gmail):
    """Envia e-mail de verificação de configuração para o e-mail digitado no pop-up."""
    logger.info("Requisição de verificação de configuração de PDV recebida")
    props = ler_properties(config_file)

    selecao = request.form.get('selecao', '').strip()
    email_destino = request.form.get('email_destino', '').strip()

    if not selecao:
        msg = "Nenhuma loja/PDV selecionado."
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 400

    if not email_destino:
        msg = "E-mail de destino é obrigatório."
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 400

    linhas = []
    for parte in selecao.split('|'):
        if ':' in parte:
            loja, pdvs = parte.split(':', 1)
            linhas.append(f"  Loja {loja.strip()} - PDVs: {pdvs.strip()}")
        else:
            linhas.append(f"  {parte.strip()}")
    selecao_formatada = '\n'.join(linhas)

    remetente = props.get('email_envio', '')
    senha = props.get('senha_envio', '')

    pid = gerar_pid()
    assunto = f"[Verificar Parametrização] - [{pid}]"
    corpo = (
        f"PID: {pid}\n"
        f"Destino: {email_destino}\n"
        f"Selecao: {selecao}\n\n"
        f"PDVs para verificação:\n{selecao_formatada}"
    )

    try:
        enviar_email_gmail(remetente, senha, remetente, assunto, corpo)
        msg = f"Solicitação de verificação enviada com sucesso! PID: {pid}"
        logger.info(msg)
        return jsonify({'sucesso': True, 'mensagem': msg, 'pid': pid})
    except Exception as e:
        msg = f"Erro ao enviar e-mail de verificação: {str(e)}"
        logger.error(msg)
        return jsonify({'sucesso': False, 'mensagem': msg}), 500
