import os
from flask import request as flask_request
from models.database import MenuItem
from bot import waha, session as sess
from bot import queue as fila


UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'uploads')


def _read_env_domain():
    """Lê FLASK_PUBLIC_DOMAIN direto do arquivo .env como fallback.

    Necessário porque dentro do webhook (WAHA -> Flask via Docker),
    os.getenv pode não ter o valor e flask_request.host_url retorna
    a URL interna do Docker (http://app:5000).
    """
    for env_path in ['/app/.env', os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')]:
        if os.path.exists(env_path):
            try:
                with open(env_path, 'r', encoding='utf-8') as f:
                    domain = ''
                    public_url = ''
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        if '=' in line:
                            key, _, val = line.partition('=')
                            key = key.strip()
                            val = val.strip().strip('"\'').strip()
                            if key == 'FLASK_PUBLIC_DOMAIN':
                                domain = val
                            elif key == 'FLASK_PUBLIC_URL':
                                public_url = val
                    return domain, public_url
            except Exception:
                pass
    return '', ''


def _get_base_url():
    """Pega a URL pública para montar links de arquivos enviados aos alunos."""
    # 1. FLASK_PUBLIC_DOMAIN do ambiente
    domain = os.getenv('FLASK_PUBLIC_DOMAIN', '').strip().strip('"\'').strip()
    if domain:
        if domain.startswith('http://143.95.210.62') or domain.startswith('143.95.210.62'):
            return 'https://movidachat.duckdns.org'
        return domain.rstrip('/')

    # 2. Lê direto do arquivo .env (fallback para contexto de webhook)
    file_domain, file_url = _read_env_domain()
    if file_domain:
        if file_domain.startswith('http://143.95.210.62') or file_domain.startswith('143.95.210.62'):
            return 'https://movidachat.duckdns.org'
        return file_domain.rstrip('/')

    # 3. FLASK_PUBLIC_URL do ambiente (se não for URL interna)
    public_url = os.getenv('FLASK_PUBLIC_URL', '').strip().strip('"\'').strip()
    if public_url and public_url not in ('http://app:5000', 'http://localhost:5000'):
        if public_url.startswith('http://143.95.210.62') or public_url.startswith('143.95.210.62'):
            return 'https://movidachat.duckdns.org'
        return public_url.rstrip('/')

    # 3b. FLASK_PUBLIC_URL do arquivo .env
    if file_url and file_url not in ('http://app:5000', 'http://localhost:5000'):
        if file_url.startswith('http://143.95.210.62') or file_url.startswith('143.95.210.62'):
            return 'https://movidachat.duckdns.org'
        return file_url.rstrip('/')

    # 4. Request context (funciona pelo navegador)
    try:
        # Nginx envia X-Forwarded-Host e X-Forwarded-Proto
        proto = flask_request.headers.get('X-Forwarded-Proto', 'http')
        host = flask_request.headers.get('X-Forwarded-Host') or flask_request.host
        
        if host and host not in ('app:5000', 'localhost:5000', '127.0.0.1:5000'):
            # Se for um IP de servidor ou local, força o domínio público
            if host.startswith('143.95.210.62') or host.split(':')[0].replace('.', '').isdigit():
                return 'https://movidachat.duckdns.org'
            return f'{proto}://{host}'.rstrip('/')
    except Exception:
        pass

    # Fallback definitivo para o domínio público oficial do cliente
    return 'https://movidachat.duckdns.org'



def show_menu(chat_id: str, parent_id: int | None = None):
    """Mostra o menu (raiz ou sub-menu) para o aluno."""
    if parent_id is None:
        items = MenuItem.get_menu_raiz()
        titulo = 'Menu Principal - Escolha o assunto:'
        voltar = False
    else:
        parent = MenuItem.query.get(parent_id)
        if not parent:
            return show_menu(chat_id, None)
        items = parent.filhos_ativos()
        titulo = f'{parent.titulo} - Escolha:'
        voltar = True

    if not items:
        waha.send_text(chat_id, 'Nenhuma opcao disponivel nesta categoria.\n\nEnvie *menu* para voltar ao inicio.')
        return

    opcoes = [item.titulo for item in items]
    waha.send_menu(chat_id, titulo, opcoes, voltar=voltar)

    sess.set_menu_position(chat_id, parent_id)


def _send_attachment(chat_id: str, item: MenuItem):
    """Envia link de download do arquivo anexo, se houver."""
    if not item.tem_arquivo:
        return
    file_path = os.path.join(UPLOAD_FOLDER, item.arquivo_path)
    if not os.path.exists(file_path):
        return

    base_url = _get_base_url()
    link = f'{base_url}/static/uploads/{item.arquivo_path}'

    tipo = item.arquivo_tipo or 'arquivo'
    emoji = '🖼️' if tipo == 'image' else '📄'

    waha.send_text(chat_id,
        f'{emoji} *{item.arquivo_nome}*\n'
        f'Acesse o arquivo: {link}')


def process_menu_choice(chat_id: str, choice: str) -> bool:
    """
    Processa a escolha numérica do aluno.
    Retorna True se processou, False se não era uma opção de menu.
    """
    lower = choice.lower().strip()

    # Comando "menu" ou "inicio" → volta ao menu raiz
    if lower in ('menu', 'inicio', 'início', 'voltar', 'oi', 'olá', 'ola', 'hi', 'hello'):
        show_menu(chat_id, None)
        return True

    # Comando "fila" → consulta posição na fila
    if lower == 'fila':
        fila.check_position(chat_id)
        return True

    # Comando "sair" → sai da fila
    if lower == 'sair':
        fila.leave_queue(chat_id)
        return True

    # Verifica se é número
    if not choice.strip().isdigit():
        return False  # Não é opção de menu, vai para fallback

    num = int(choice.strip())

    # "0" = voltar
    if num == 0:
        current_menu_id = sess.get_menu_position(chat_id)
        if current_menu_id is None:
            show_menu(chat_id, None)
        else:
            current = MenuItem.query.get(current_menu_id)
            parent_id = current.parent_id if current else None
            show_menu(chat_id, parent_id)
        return True

    # Busca os itens do menu atual
    current_menu_id = sess.get_menu_position(chat_id)
    if current_menu_id is None:
        items = MenuItem.get_menu_raiz()
    else:
        parent = MenuItem.query.get(current_menu_id)
        items = parent.filhos_ativos() if parent else MenuItem.get_menu_raiz()

    # Valida escolha
    if num < 1 or num > len(items):
        waha.send_text(chat_id, f'Opcao invalida. Escolha entre 1 e {len(items)}.')
        return True

    selected = items[num - 1]

    # Verifica se é item de fila (marcador __FILA__)
    if selected.resposta == '__FILA__':
        fila.start_form(chat_id)
        return True

    if selected.is_folha:
        # Item final — envia resposta
        if selected.resposta:
            waha.send_text(chat_id, selected.resposta)
        else:
            waha.send_text(chat_id, f'Voce selecionou: *{selected.titulo}*')
        # Envia anexo se houver
        _send_attachment(chat_id, selected)
        waha.send_text(chat_id, 'Envie *menu* para voltar ao inicio.')
    else:
        # Tem sub-menus — mostra resposta (se houver) e depois os sub-menus
        if selected.resposta and selected.resposta != '__FILA__':
            waha.send_text(chat_id, selected.resposta)
        # Envia anexo se houver
        _send_attachment(chat_id, selected)
        show_menu(chat_id, selected.id)

    return True
