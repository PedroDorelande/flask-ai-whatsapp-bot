import requests
import os
import sys
import base64

WAHA_URL = os.getenv('WAHA_API_URL', 'http://localhost:3000')
WAHA_KEY = os.getenv('WAHA_API_KEY', '')
WAHA_SESSION = os.getenv('WAHA_SESSION', 'default')

HEADERS = {
    'X-Api-Key': WAHA_KEY,
    'Content-Type': 'application/json'
}

# Track message IDs sent by the bot so the webhook can ignore them
_bot_sent_ids = set()
_MAX_SENT_CACHE = 200

# LID → número real cache
_lid_cache = {}


def resolve_lid(from_jid: str, payload: dict = None) -> str:
    """Resolve LID para número real usando 5 estratégias + API contacts."""
    if not from_jid:
        return from_jid

    raw = from_jid.split('@')[0]

    # Se já parece número BR válido (≤13 dígitos, começa com 55), retorna direto
    if raw.isdigit() and len(raw) <= 13 and raw.startswith('55'):
        return raw

    # Checar cache
    if raw in _lid_cache:
        return _lid_cache[raw]

    numero = None
    _data = (payload or {}).get('_data', {})

    # Estratégia 1: SenderAlt (prioridade máxima)
    sender_alt = (_data.get('Info') or {}).get('SenderAlt', '')
    if sender_alt:
        alt = sender_alt.split('@')[0].split(':')[0]
        alt = ''.join(c for c in alt if c.isdigit())
        if len(alt) >= 10:
            numero = alt

    # Estratégia 2: chatId com @s.whatsapp.net
    if not numero and payload:
        chat_id = payload.get('chatId', '')
        if '@s.whatsapp.net' in chat_id:
            numero = chat_id.split('@')[0]

    # Estratégia 3/4: from com @c.us ou @s.whatsapp.net
    if not numero:
        if '@c.us' in from_jid:
            numero = from_jid.split('@')[0]
        elif '@s.whatsapp.net' in from_jid:
            numero = from_jid.split('@')[0]

    # Estratégia 5: API contacts (para LIDs)
    if not numero and '@lid' in from_jid:
        try:
            resp = requests.get(
                f'{WAHA_URL}/api/contacts',
                headers=HEADERS,
                params={'contactId': from_jid, 'session': WAHA_SESSION},
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    numero = data.get('number', '') or data.get('id', '').split('@')[0]
                elif isinstance(data, list) and data:
                    numero = data[0].get('number', '') or data[0].get('id', '').split('@')[0]
        except Exception:
            pass

    if numero and numero.isdigit() and len(numero) >= 10:
        _lid_cache[raw] = numero
        return numero

    # Fallback: retorna o raw (pode ser LID)
    return raw


def is_bot_sent(msg_id: str) -> bool:
    """Check if a message ID was sent by our bot."""
    return msg_id in _bot_sent_ids


def _track_sent(response_data):
    """Store message ID from WAHA send response."""
    if not response_data or not isinstance(response_data, dict):
        return
    msg_id = response_data.get('id', '')
    if isinstance(msg_id, dict):
        msg_id = msg_id.get('_serialized', '') or msg_id.get('id', '')
    if msg_id:
        _bot_sent_ids.add(msg_id)
        inner = response_data.get('id', {})
        if isinstance(inner, dict) and 'id' in inner:
            _bot_sent_ids.add(inner['id'])
    if len(_bot_sent_ids) > _MAX_SENT_CACHE:
        _bot_sent_ids.clear()


def send_text(chat_id: str, text: str):
    """Envia mensagem de texto simples via WAHA."""
    try:
        resp = requests.post(f'{WAHA_URL}/api/sendText', headers=HEADERS, json={
            'chatId': chat_id,
            'text': text,
            'session': WAHA_SESSION
        }, timeout=10)
        resp.raise_for_status()
        try:
            data = resp.json()
            _track_sent(data)
            return data
        except Exception:
            return {'status': 'sent', 'text': resp.text}
    except Exception as e:
        print(f'[ERRO] Falha ao enviar texto para {chat_id}: {type(e).__name__}',
              file=sys.stderr, flush=True)
        return None


def send_image(chat_id: str, file_path: str, caption: str = ''):
    """Envia imagem via WAHA usando base64."""
    try:
        with open(file_path, 'rb') as f:
            file_data = base64.b64encode(f.read()).decode('utf-8')

        filename = os.path.basename(file_path)
        ext = filename.rsplit('.', 1)[-1].lower()
        mime_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
                    'gif': 'image/gif', 'webp': 'image/webp'}
        mimetype = mime_map.get(ext, 'image/jpeg')

        resp = requests.post(f'{WAHA_URL}/api/sendImage', headers=HEADERS, json={
            'chatId': chat_id,
            'session': WAHA_SESSION,
            'file': {
                'mimetype': mimetype,
                'filename': filename,
                'data': file_data
            },
            'caption': caption
        }, timeout=30)
        resp.raise_for_status()
        try:
            data = resp.json()
            _track_sent(data)
            return data
        except Exception:
            return {'status': 'sent'}
    except Exception as e:
        print(f'[ERRO] Falha ao enviar imagem para {chat_id}: {type(e).__name__}',
              file=sys.stderr, flush=True)
        return None


def send_file(chat_id: str, file_path: str, caption: str = ''):
    """Envia arquivo/documento via WAHA usando base64."""
    try:
        with open(file_path, 'rb') as f:
            file_data = base64.b64encode(f.read()).decode('utf-8')

        filename = os.path.basename(file_path)
        ext = filename.rsplit('.', 1)[-1].lower()
        mime_map = {'pdf': 'application/pdf', 'doc': 'application/msword',
                    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'xls': 'application/vnd.ms-excel', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    'txt': 'text/plain', 'csv': 'text/csv'}
        mimetype = mime_map.get(ext, 'application/octet-stream')

        resp = requests.post(f'{WAHA_URL}/api/sendFile', headers=HEADERS, json={
            'chatId': chat_id,
            'session': WAHA_SESSION,
            'file': {
                'mimetype': mimetype,
                'filename': filename,
                'data': file_data
            },
            'caption': caption
        }, timeout=30)
        resp.raise_for_status()
        try:
            data = resp.json()
            _track_sent(data)
            return data
        except Exception:
            return {'status': 'sent'}
    except Exception as e:
        print(f'[ERRO] Falha ao enviar arquivo para {chat_id}: {type(e).__name__}',
              file=sys.stderr, flush=True)
        return None


def send_attachment(chat_id: str, file_path: str, file_type: str, caption: str = ''):
    """Envia anexo baseado no tipo (image/pdf/document)."""
    if file_type == 'image':
        return send_image(chat_id, file_path, caption)
    else:
        return send_file(chat_id, file_path, caption)


def send_buttons(chat_id: str, body: str, buttons: list[dict]):
    """Fallback: texto formatado com numeros."""
    linhas = [body, '']
    for i, btn in enumerate(buttons, 1):
        linhas.append(f'{i} - {btn["title"]}')
    linhas.append('')
    linhas.append('_Responda com o numero da opcao._')
    return send_text(chat_id, '\n'.join(linhas))


def send_menu(chat_id: str, titulo: str, opcoes: list, voltar=True):
    """Envia um menu formatado com opcoes numeradas."""
    linhas = [f'*{titulo}*', '']
    for i, opcao in enumerate(opcoes, 1):
        linhas.append(f'{i} - {opcao}')
    if voltar:
        linhas.append('')
        linhas.append('0 - Voltar')
    linhas.append('')
    linhas.append('_Responda com o numero da opcao._')
    return send_text(chat_id, '\n'.join(linhas))


# =============================================
# WAHA Session Management (for QR code page)
# =============================================

def get_session_status():
    """Get WAHA session status."""
    try:
        resp = requests.get(f'{WAHA_URL}/api/sessions/{WAHA_SESSION}',
                          headers=HEADERS, timeout=5)
        if resp.status_code == 200:
            return resp.json()
        return {'status': 'STOPPED'}
    except Exception:
        return {'status': 'ERROR', 'error': 'Cannot connect to WAHA'}


def get_qr_code():
    """Get QR code image bytes from WAHA."""
    try:
        headers = {'X-Api-Key': WAHA_KEY, 'Accept': 'image/png'}
        resp = requests.get(f'{WAHA_URL}/api/{WAHA_SESSION}/auth/qr',
                          headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.content, resp.headers.get('Content-Type', 'image/png')
        return None, None
    except Exception:
        return None, None


def start_session():
    """Start WAHA session."""
    try:
        resp = requests.post(f'{WAHA_URL}/api/sessions/start',
                           headers=HEADERS, json={'name': WAHA_SESSION}, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def stop_session():
    """Stop WAHA session."""
    try:
        resp = requests.post(f'{WAHA_URL}/api/sessions/stop',
                           headers=HEADERS, json={'name': WAHA_SESSION}, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def configure_webhook(flask_url: str = None):
    """Auto-configura o webhook do WAHA para apontar para o Flask."""
    if not flask_url:
        flask_url = os.getenv('FLASK_PUBLIC_URL', 'http://host.docker.internal:5000')
    webhook_url = f'{flask_url}/webhook'
    try:
        # Tenta via PUT /api/sessions/{session}
        resp = requests.put(
            f'{WAHA_URL}/api/sessions/{WAHA_SESSION}',
            headers=HEADERS,
            json={
                'config': {
                    'webhooks': [{
                        'url': webhook_url,
                        'events': ['message', 'message.any']
                    }]
                }
            },
            timeout=10
        )
        if resp.status_code in (200, 201):
            print(f'[WAHA] Webhook configurado: {webhook_url}', file=sys.stderr, flush=True)
            return True
        # Fallback: PATCH
        resp2 = requests.patch(
            f'{WAHA_URL}/api/sessions/{WAHA_SESSION}',
            headers=HEADERS,
            json={
                'config': {
                    'webhooks': [{
                        'url': webhook_url,
                        'events': ['message', 'message.any']
                    }]
                }
            },
            timeout=10
        )
        if resp2.status_code in (200, 201):
            print(f'[WAHA] Webhook configurado (PATCH): {webhook_url}', file=sys.stderr, flush=True)
            return True
        print(f'[WAHA] Falha ao configurar webhook: {resp.status_code} / {resp2.status_code}',
              file=sys.stderr, flush=True)
    except Exception as e:
        print(f'[WAHA] Erro ao configurar webhook: {e}', file=sys.stderr, flush=True)
    return False


def get_messages(chat_id: str, limit: int = 30):
    """Busca últimas mensagens de um chat via WAHA."""
    try:
        resp = requests.get(
            f'{WAHA_URL}/api/{WAHA_SESSION}/chats/{chat_id}/messages',
            headers=HEADERS,
            params={'limit': limit},
            timeout=10
        )
        if resp.status_code == 200:
            msgs = resp.json()
            # Retorna em ordem cronológica (mais antiga primeiro)
            msgs.sort(key=lambda m: m.get('timestamp', 0))
            return msgs
        return []
    except Exception:
        return []
