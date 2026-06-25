# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['extrator_logs.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('templates',       'templates'),
        ('static',          'static'),
        ('agent.properties', '.'),
    ],
    hiddenimports=[
        # Flask e dependências
        'flask',
        'flask.json',
        'jinja2',
        'jinja2.ext',
        'werkzeug',
        'werkzeug.routing',
        'werkzeug.exceptions',
        'werkzeug.utils',
        'werkzeug.middleware.proxy_fix',
        'click',
        'itsdangerous',
        # Oracle
        'oracledb',
        'oracledb.driver_mode',
        # Excel
        'openpyxl',
        'openpyxl.styles',
        'openpyxl.utils',
        # Requisições HTTP
        'requests',
        'requests.adapters',
        'requests.auth',
        'urllib3',
        'urllib3.util',
        'urllib3.util.retry',
        'certifi',
        'charset_normalizer',
        'idna',
        # E-mail
        'smtplib',
        'email',
        'email.mime.text',
        'email.mime.multipart',
        # Stdlib extras
        'csv',
        'zipfile',
        'logging',
        'logging.handlers',
        # pywebview (janela nativa Windows com Edge WebView2)
        'webview',
        'webview.platforms',
        'webview.platforms.winforms',
        'webview.event',
        'clr',
        'clr_loader',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'unittest',
        'test',
        'xmlrpc',
        'pydoc',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ExtratordeLogs',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,         # execução em background sem janela de console
    icon='icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ExtratordeLogs',
)
