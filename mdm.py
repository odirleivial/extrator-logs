# mdm.py
# Aba MDM - Cadastro e Consulta de clientes na API MDM (Facade)
# Não comunica com o Agent Extrator de Log. Espelha os parâmetros usados na
# aplicação de referência (scripts_python/MDM/mdm_payloads.py + constantes.py).
import csv
import logging
import os
import sys
import requests
from datetime import datetime
from flask import render_template, request, jsonify

from execucao import registrar_execucao

logger = logging.getLogger('ExtratrorLogs')

# Resolve o diretório base igual aos demais módulos (extrator_logs.py, request_api.py)
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_DIR = os.path.join(_BASE_DIR, 'log')


def registrar_historico_mdm(administrative_identifier, payload_json, status_code, retorno_texto, prefixo='post'):
    """Grava uma linha no histórico diário de operações MDM.

    prefixo='post'  -> log/post_mdm_<data>.csv  (cadastros / POST)
    prefixo='patch' -> log/patch_mdm_<data>.csv (alterações / PATCH via JSON Patch)
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        nome_arquivo = f"{prefixo}_mdm_{datetime.now().strftime('%Y-%m-%d')}.csv"
        caminho = os.path.join(LOG_DIR, nome_arquivo)
        arquivo_novo = not os.path.exists(caminho)

        with open(caminho, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f, delimiter=';', lineterminator='\n')
            if arquivo_novo:
                writer.writerow(['data_hora', 'administrativeIdentifier', 'payload', 'status_code', 'retorno'])
            writer.writerow([
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                administrative_identifier,
                payload_json,
                status_code,
                retorno_texto,
            ])
    except Exception as e:
        logger.error(f"Erro ao gravar histórico de cadastro MDM: {str(e)}")

# ---------------------------------------------------------------------------
# Definição dos campos (espelha a planilha mdm_payloads.xlsm - aba "parametros")
# ---------------------------------------------------------------------------
# Tipos de campo:
#   text        -> input de texto livre
#   text_auto   -> texto que aceita "Automatico" ou um valor Manual
#   number      -> input numérico
#   bool        -> Verdadeiro/Falso
#   bool_auto   -> Automatico/Verdadeiro/Falso
#   select      -> lista fixa de opções
#   select_auto -> lista fixa de opções + "Automatico"
#   select_manual -> lista fixa de opções; quando a opção selecionada é "Manual",
#                    abre um campo de texto para o usuário digitar o valor
#   cnae        -> bloco especial (apenas flag + quantidade, gerado automaticamente)

def f(name, label, type_, flag=False, value='', options=None, help_=''):
    return {
        'name': name, 'label': label, 'type': type_, 'flag': flag,
        'value': value, 'options': options or [], 'help': help_,
    }


# Todas as 27 UFs (26 estados + DF) — geração de IE válida via regras Sintegra
UFS_IE = ['AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS', 'MG',
          'PA', 'PB', 'PR', 'PE', 'PI', 'RJ', 'RN', 'RS', 'RO', 'RR', 'SC', 'SP', 'SE', 'TO']

# UFs realmente presentes na base de CEPs (static/data/base_de_ceps.json).
# Todas as 27 UFs têm registros na base (AC incluído a partir do enriquecimento).
UFS_CEP = ['AC', 'AL', 'AM', 'AP', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MG', 'MS', 'MT',
           'PA', 'PB', 'PE', 'PI', 'PR', 'RJ', 'RN', 'RO', 'RR', 'RS', 'SC', 'SE', 'SP', 'TO']

SECTIONS = [
    {
        'id': 'dados_gerais',
        'title': 'Dados Gerais',
        'kind': 'plain',
        'fields': [
            f('businessUnitIdentifier', 'Unidade de Negócio', 'number', True, '7'),
            f('creationDate', 'Data de Criação', 'text_auto', True, 'Automatico',
              help_='Automatico ou data no formato: 2025-03-07'),
            f('creationStoreIdentifier', 'Loja de Criação', 'text', True, '0007'),
            f('createdByApp', 'Criado por App', 'text', True, 'LMBR-IDB-SUPPORT basic'),
            f('creationUserLDAPNumber', 'Matrícula LDAP de Criação', 'number', True, 51044280),
            f('updatedByApp', 'Atualizado por App', 'text', True, 'LMBR-IDB-SUPPORT basic2'),
            f('lastUpdateUserLDAPNumber', 'Matrícula LDAP Última Atualização', 'number', False, 51044280),
            f('lastUpdateStoreIdentifier', 'Loja Última Atualização', 'text', False, '0007'),
            f('preferenceStoreIdentifier', 'Loja de Preferência', 'text', True, '0007'),
            f('customerIndicator', 'Indicador de Cliente', 'bool', True, False),
        ],
    },
    {
        'id': 'inhabitant',
        'title': 'Pessoa Física (Inhabitant)',
        'kind': 'plain',
        'fields': [
            f('inhabitantCpf', 'CPF', 'text_auto', True, 'Automatico'),
            f('inhabitantPassportNumber', 'Passaporte', 'text_auto', False, 'Automatico'),
            f('inhabitantRne', 'RNE', 'text_auto', False, 'Automatico'),
            f('inhabitantFullName', 'Nome Completo', 'text_auto', True, 'Automatico'),
            f('inhabitantBirthDate', 'Data de Nascimento', 'text', True, '2000-02-02',
              help_='Formato: AAAA-MM-DD'),
            f('inhabitantOver18YearsIndicator', 'Maior de 18 anos', 'bool', True, True),
            f('inhabitantCollaboratorIdentifier', 'Matrícula de Colaborador', 'text_auto', False, 'Automatico'),
            f('inhabitantProfessionName', 'Profissão', 'select', True, 'architect', [
                'engineer', 'others', 'electrician', 'builder', 'woodworker', 'gardener',
                'personalOrganizer', 'bricklayer', 'locksmith', 'potter', 'plumber',
                'interiorDesigner', 'architect']),
            f('inhabitantStateRegistrationNumber', 'Número Inscrição Estadual (PF)', 'text_auto', False, 'Automatico',
              help_="Geração automática de IE válida (regras Sintegra) para todas as 27 UFs"),
            f('inhabitantStateRegistrationFederatedUnit', 'UF Inscrição Estadual (PF)', 'select_auto', False, 'Automatico', UFS_IE),
        ],
    },
    {
        'id': 'organization',
        'title': 'Pessoa Jurídica (Professional Organization)',
        'kind': 'plain',
        'fields': [
            f('professionalOrganizationCnpj', 'CNPJ', 'select_manual', True, 'Alfanumerico',
              ['Automatico', 'Alfanumerico', 'Manual'],
              help_='Automatico = numérico aleatório; Alfanumerico = CNPJ alfanumérico (novo padrão); Manual = digite o CNPJ'),
            f('professionalOrganizationRegisteredName', 'Razão Social', 'text_auto', True, 'Automatico'),
            f('professionalOrganizationCommercialName', 'Nome Fantasia', 'text_auto', True, 'Automatico'),
            f('professionalOrganizationSize', 'Porte', 'select', True, 'ME', ['EPP', 'DEMAIS', 'ME']),
            f('professionalOrganizationStatus', 'Status CNPJ', 'text', True, 'Active'),
            f('professionalOrganizationLastCheckDate', 'Data Última Verificação CNPJ', 'text_auto', True, 'Automatico'),
            f('professionalOrganizationSalesEnableIndicator', 'Habilitado para Vendas', 'bool', True, True),
            f('professionalOrganizationEnableIndicator', 'Habilitado', 'bool', True, True),
            f('professionalOrganizationRegisteredActivityCode', 'Atividades Econômicas (CNAE)', 'cnae', True, 3,
              help_='CNAEs válidos (código + descrição) sorteados da base oficial de subclasses do IBGE'),
            f('professionalOrganizationStateRegistrationExemptIndicator', 'Isento de Inscrição Estadual', 'bool', True, False),
            f('professionalOrganizationStateRegistrationLastCheckDate', 'Data Última Verificação IE', 'text_auto', True, 'Automatico'),
            f('professionalOrganizationStateRegistrationNumber', 'Número Inscrição Estadual (PJ)', 'text_auto', True, 'Automatico'),
            f('professionalOrganizationStateRegistrationFederatedUnit', 'UF Inscrição Estadual (PJ)', 'select_auto', True, 'Automatico', UFS_IE),
            f('professionalOrganizationStateRegistrationStatus', 'Status Inscrição Estadual', 'select', True, 'Abled',
              ['Abled', 'Annulled', 'Unknown', 'Unabled', 'UnabledNotConfirmed', 'UnabledTemp']),
        ],
    },
    {
        'id': 'address',
        'title': 'Endereços',
        'kind': 'list',
        'master': 'addressPostalCode',
        'qtd_default': 1,
        'fields': [
            f('addressPostalCode', 'CEP', 'text_auto', True, 'Automatico'),
            f('addressIdentifier', 'Identificador', 'text', False, 'Residencial'),
            f('addressNickname', 'Apelido do Endereço', 'text_auto', True, 'Automatico'),
            f('addressStreetName', 'Logradouro', 'text_auto', True, 'Automatico'),
            f('addressStreetNumber', 'Número', 'text_auto', True, 'Automatico'),
            f('addressComplement', 'Complemento', 'text_auto', True, 'Automatico'),
            f('addressReferencePoint', 'Ponto de Referência', 'text', False, 'Em frente a Praça XV'),
            f('addressPostalCodeType', 'Tipo de CEP', 'select_auto', True, 'logradouro',
              ['grandeUsuario', 'localidade', 'logradouro', 'unidadeOperacional', 'caixaPostalComunitaria'],
              help_='Ao escolher manualmente, apenas endereços reais desse tipo serão gerados (quando existir na UF escolhida)'),
            f('addressPostalCodeDescription', 'Descrição do CEP', 'text_auto', True, 'Automatico'),
            f('addressCityName', 'Cidade', 'text_auto', True, 'Automatico'),
            f('addressIbgeCityCode', 'Código IBGE', 'text_auto', True, 'Automatico'),
            f('addressSuburb', 'Bairro', 'text_auto', True, 'Automatico'),
            f('addressProvince', 'UF', 'select_auto', True, 'Automatico', UFS_CEP,
              help_='Ao escolher manualmente uma UF, apenas endereços reais dessa UF serão gerados'),
            f('addressCountry', 'País', 'text_auto', True, 'Automatico'),
            f('mainAddressIndicator', 'Endereço Principal', 'bool_auto', True, 'Automatico',
              help_='Automatico marca o 1º endereço da lista como principal'),
            f('addressExternalSource', 'Origem Externa', 'text', True, 'apiPJ'),
            f('addressClientInformedIndicator', 'Informado pelo Cliente', 'bool_auto', True, 'Automatico'),
            f('addressGardenIndicator', 'Possui Jardim', 'bool', True, True),
            f('addressPoolIndicator', 'Possui Piscina', 'bool', True, True),
        ],
        'subgroups': [
            {
                'id': 'address_optin', 'title': 'Optin de Endereço (Mala Direta)',
                'fields': [
                    f('directMailOptinIndicator', 'Optin Mala Direta', 'bool', True, True),
                    f('directMailOptinDatetime', 'Data do Optin', 'text_auto', True, 'Automatico'),
                    f('directMailOptinPerformingApp', 'App Responsável', 'text', True, 'LMBR-IDB-SUPPORT Endereço'),
                ],
            },
        ],
    },
    {
        'id': 'phone',
        'title': 'Telefones',
        'kind': 'list',
        'master': 'phoneNumber',
        'qtd_default': 1,
        'fields': [
            f('phoneNumber', 'Número de Telefone', 'text_auto', True, 'Automatico'),
            f('phoneContactName', 'Nome de Contato', 'text_auto', True, 'Automatico'),
            f('mainPhoneIndicator', 'Telefone Principal', 'bool_auto', True, 'Automatico',
              help_='Automatico marca o 1º telefone da lista como principal'),
            f('phoneExternalSource', 'Origem Externa', 'text', True, 'apiPJ'),
            f('phoneInformedByClientIndicator', 'Informado pelo Cliente', 'bool', True, True),
        ],
        'subgroups': [
            {
                'id': 'phone_optin', 'title': 'Optin de Telefone',
                'fields': [
                    f('phoneNumberOptinIndicator', 'Optin Telefone', 'bool', True, True),
                    f('phoneNumberOptinDatetime', 'Data do Optin', 'text_auto', True, 'Automatico'),
                    f('phoneNumberOptinPerformingApp', 'App Responsável', 'text', True, 'LMBR-IDB-SUPPORT Telefone'),
                ],
            },
            {
                'id': 'sms_optin', 'title': 'Optin de SMS',
                'fields': [
                    f('smsOptinIndicator', 'Optin SMS', 'bool', True, True),
                    f('smsOptinDatetime', 'Data do Optin', 'text_auto', True, 'Automatico'),
                    f('smsOptinPerformingApp', 'App Responsável', 'text', True, 'LMBR-IDB-SUPPORT SMS'),
                ],
            },
            {
                'id': 'push_optin', 'title': 'Optin de Push Notification',
                'fields': [
                    f('pushNotificationOptinIndicator', 'Optin Push', 'bool', True, True),
                    f('pushNotificationOptinDatetime', 'Data do Optin', 'text_auto', True, 'Automatico'),
                    f('pushNotificationOptinPerformingApp', 'App Responsável', 'text', True, 'LMBR-IDB-SUPPORT Push'),
                ],
            },
            {
                'id': 'whatsapp_optin', 'title': 'Optin de WhatsApp',
                'fields': [
                    f('whatsAppOptinIndicator', 'Optin WhatsApp', 'bool', True, True),
                    f('whatsAppOptinDatetime', 'Data do Optin', 'text_auto', True, 'Automatico'),
                    f('whatsAppOptinPerformingApp', 'App Responsável', 'text', True, 'LMBR-IDB-SUPPORT whatsApp'),
                ],
            },
        ],
    },
    {
        'id': 'email',
        'title': 'E-mails',
        'kind': 'list',
        'master': 'email',
        'qtd_default': 1,
        'fields': [
            f('email', 'E-mail', 'text_auto', True, 'Automatico'),
            f('emailContactName', 'Nome de Contato', 'text_auto', True, 'Automatico'),
            f('emailIdentifiedUserCredentialIndicator', 'Credencial de Usuário Identificado', 'bool_auto', True, 'Automatico',
              help_='Preenchido automaticamente apenas para o 1º e-mail da lista'),
            f('emailInvoiceSubmissionIndicator', 'Envio de Nota Fiscal', 'bool_auto', True, 'Automatico',
              help_='Automatico = true para o 1º e-mail e false para os demais'),
            f('emailExternalSource', 'Origem Externa', 'text', False, 'apiPJ'),
            f('emailInformedbyClientIndicator', 'Informado pelo Cliente', 'bool', True, False),
            f('emailValildationStatus', 'Status de Validação', 'text', True, 'valid'),
        ],
        'subgroups': [
            {
                'id': 'email_optin', 'title': 'Optin de E-mail',
                'fields': [
                    f('emailOptinIndicator', 'Optin E-mail', 'bool', True, True),
                    f('emailOptinDatetime', 'Data do Optin', 'text_auto', True, 'Automatico'),
                    f('emailOptinPerformingApp', 'App Responsável', 'text', True, 'LMBR-IDB-SUPPORT Email'),
                ],
            },
        ],
    },
    {
        'id': 'loyalty',
        'title': 'Programa de Fidelidade (LoyaltyProgram)',
        'kind': 'group_master',
        'master': 'loyaltyProgramStatus',
        'fields': [
            f('loyaltyProgramStatus', 'Status (código)', 'number', False, 5),
            f('loyaltyProgramType', 'Tipo', 'select', False, 'LMCV', ['PRO/EXECUTOR', 'LMCV', 'PRO/EAD']),
            f('loyaltyProgramCreateSource', 'Origem de Criação', 'text', False, 'LMBR-IDB-SUPPORT'),
            f('loyaltyProgramStatusloyaltyCreationDate', 'Data de Criação no Programa', 'text_auto', False, 'Automatico'),
            f('loyaltyProgramLeadIndicator', 'Indicador de Lead', 'bool', False, False),
            f('loyaltyProgramProfessionalAssociationName', 'Associação Profissional', 'select', False, 'cau', ['cau', 'crea', 'abd']),
            f('loyaltyProgramProfessionalAssociationRegistrationNumber', 'Registro na Associação', 'text', False, 'A91771-0'),
            f('loyaltyProgramProfessionalAssociationDocumentationStatus', 'Status da Documentação', 'select', False, 'approved',
              ['denied', 'pendingSending', 'pendingApproval', 'approved']),
        ],
        'subgroups': [
            {
                'id': 'loyalty_adhesion_optin', 'title': 'Optin de Adesão',
                'fields': [
                    f('loyaltyProgramAdhesionOptinIndicator', 'Optin de Adesão', 'bool', False, True),
                    f('loyaltyProgramAdhesionOptinDatetime', 'Data do Optin de Adesão', 'text_auto', False, 'Automatico'),
                    f('loyaltyProgramAdhesionOptinPerformingApp', 'App Responsável pela Adesão', 'text', False, 'LMBR-IDB-SUPPORT Loyalty'),
                ],
            },
        ],
    },
    {
        'id': 'preferencias',
        'title': 'Preferências e Interesses',
        'kind': 'plain',
        'fields': [
            f('socialLoginEmail', 'E-mail Social Login', 'text_auto', False, 'Automatico'),
            f('socialLoginSource', 'Origem Social Login', 'text', False, 'google'),
            f('interestAreaName', 'Área de Interesse', 'select', True, 'decoration', [
                'decoration', 'home_appliances', 'ventilation', 'wood', 'organization', 'audio_video_tv',
                'bed_table_bath', 'garden', 'ironmongery', 'diy', 'paints', 'small_appliances', 'petshop',
                'electrical', 'maintenance', 'hydraulic', 'tools', 'carpet', 'kitchen', 'lighting', 'security',
                'construction', 'pool', 'informatics', 'door', 'bathroom', 'floor']),
            f('DIYRelationType', 'Relação com DIY', 'select', True, 'high', ['high', 'medium', 'low']),
            f('homeDecorKnowledgeLevel', 'Nível de Conhecimento em Decoração', 'select', True, 'low', ['high', 'low', 'none']),
        ],
    },
    {
        'id': 'cartao',
        'title': 'Cartão Leroy Merlin',
        'kind': 'plain',
        'fields': [
            f('leroyMerlinCreditCardStatusName', 'Status do Cartão', 'text', True, 'active'),
            f('leroyMerlinCreditCardCategoryIdentifier', 'Categoria do Cartão', 'number', True, 2),
        ],
    },
]


def pagina_mdm(app, ler_properties, config_file):
    """Retorna a página da aba MDM (Cadastrar/Consultar)."""
    props = ler_properties(config_file)
    schema = props.get('mdm_api_schema', 'lmbr_client_preprod')
    return render_template('mdm.html', sections=SECTIONS, schema=schema, ufs_ie=UFS_IE)


def consultar_cliente_mdm(app, ler_properties, config_file):
    """Consulta um cliente na API MDM (Facade) pelo identificador administrativo (CPF/CNPJ)."""
    props = ler_properties(config_file)
    identificador = request.form.get('administrativeIdentifier', '').strip()

    if not identificador:
        return jsonify({'sucesso': False, 'mensagem': 'Informe o CPF/CNPJ para consulta.'}), 400

    url_base = props.get('mdm_api_url', '').strip()
    apikey = props.get('mdm_api_apikey', '').strip()

    if not url_base or not apikey:
        return jsonify({'sucesso': False, 'mensagem': 'Configuração da API MDM não encontrada em secure.properties.'}), 400

    url = url_base.rstrip('/') + '/' + identificador
    headers = {'Apikey': apikey, 'Accept': 'application/json'}

    try:
        resp = requests.get(url, headers=headers, timeout=30, verify=False)
        retorno = {
            'status_code': resp.status_code,
            'headers': dict(resp.headers),
            'body': resp.text,
        }
        logger.info(f"Consulta MDM {identificador} retornou status {resp.status_code}")
        registrar_execucao(props, 'MDM - Consultar', detalhes={
            'AdministrativeIdentifier': identificador,
            'StatusCode': resp.status_code,
        })
        return jsonify({'sucesso': True, 'retorno': retorno})
    except Exception as e:
        logger.error(f"Erro ao consultar cliente MDM {identificador}: {str(e)}")
        return jsonify({'sucesso': False, 'mensagem': f'Erro na consulta: {str(e)}'}), 500


def cadastrar_cliente_mdm(app, ler_properties, config_file):
    """Envia (POST) um payload de cadastro de cliente para a API MDM (Facade)."""
    props = ler_properties(config_file)
    payload_json = request.form.get('payload', '').strip()
    administrative_identifier = request.form.get('administrativeIdentifier', '').strip()

    if not payload_json:
        return jsonify({'sucesso': False, 'mensagem': 'Payload vazio.'}), 400

    url = props.get('mdm_api_url', '').strip()
    apikey = props.get('mdm_api_apikey', '').strip()

    if not url or not apikey:
        return jsonify({'sucesso': False, 'mensagem': 'Configuração da API MDM não encontrada em secure.properties.'}), 400

    headers = {'Apikey': apikey, 'Accept': 'application/json', 'Content-Type': 'application/json'}

    try:
        resp = requests.post(url, headers=headers, data=payload_json, timeout=30, verify=False)
        retorno = {
            'status_code': resp.status_code,
            'headers': dict(resp.headers),
            'body': resp.text,
        }
        logger.info(f"Cadastro MDM retornou status {resp.status_code}")
        registrar_historico_mdm(administrative_identifier, payload_json, resp.status_code, resp.text)
        registrar_execucao(props, 'MDM - Cadastrar', detalhes={
            'AdministrativeIdentifier': administrative_identifier,
            'StatusCode': resp.status_code,
        })
        return jsonify({'sucesso': True, 'retorno': retorno})
    except Exception as e:
        logger.error(f"Erro ao cadastrar cliente MDM: {str(e)}")
        registrar_historico_mdm(administrative_identifier, payload_json, 'ERRO', str(e))
        return jsonify({'sucesso': False, 'mensagem': f'Erro no envio: {str(e)}'}), 500


def atualizar_cliente_mdm(app, ler_properties, config_file):
    """Envia (PATCH) um JSON Patch de alteração de cliente para a API MDM (Facade).

    A API espera o cabeçalho ?jsonPatch=true e um corpo no formato RFC 6902
    (array de operações op/path/value), diferente do cadastro (POST completo).
    """
    props = ler_properties(config_file)
    payload_json = request.form.get('payload', '').strip()
    administrative_identifier = request.form.get('administrativeIdentifier', '').strip()

    if not payload_json:
        return jsonify({'sucesso': False, 'mensagem': 'Payload de alteração vazio.'}), 400
    if not administrative_identifier:
        return jsonify({'sucesso': False, 'mensagem': 'Identificador (CPF/CNPJ) do cliente não informado.'}), 400

    url_base = props.get('mdm_api_url', '').strip()
    apikey = props.get('mdm_api_apikey', '').strip()

    if not url_base or not apikey:
        return jsonify({'sucesso': False, 'mensagem': 'Configuração da API MDM não encontrada em secure.properties.'}), 400

    url = url_base.rstrip('/') + '/' + administrative_identifier + '?jsonPatch=true'
    headers = {'Apikey': apikey, 'Accept': 'application/json', 'Content-Type': 'application/json'}

    try:
        resp = requests.patch(url, headers=headers, data=payload_json, timeout=30, verify=False)
        retorno = {
            'status_code': resp.status_code,
            'headers': dict(resp.headers),
            'body': resp.text,
        }
        logger.info(f"Alteração MDM {administrative_identifier} retornou status {resp.status_code}")
        registrar_historico_mdm(administrative_identifier, payload_json, resp.status_code, resp.text, prefixo='patch')
        registrar_execucao(props, 'MDM - Alterar', detalhes={
            'AdministrativeIdentifier': administrative_identifier,
            'StatusCode': resp.status_code,
        })
        return jsonify({'sucesso': True, 'retorno': retorno})
    except Exception as e:
        logger.error(f"Erro ao alterar cliente MDM {administrative_identifier}: {str(e)}")
        registrar_historico_mdm(administrative_identifier, payload_json, 'ERRO', str(e), prefixo='patch')
        return jsonify({'sucesso': False, 'mensagem': f'Erro no envio: {str(e)}'}), 500
