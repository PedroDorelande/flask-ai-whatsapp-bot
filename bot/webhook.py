import json
import sys
import time
from flask import Blueprint, request, jsonify
from bot import waha, session as sess, menu, ai
from bot import queue as fila

webhook_bp = Blueprint('webhook', __name__)

# Deduplicacao: evita processar a mesma mensagem varias vezes
_processed_ids = set()
_MAX_CACHE = 500

# Log file for debugging
LOG_FILE = 'webhook_debug.log'

# Timestamp when the app started — ignore messages older than this
_APP_START_TIME = time.time()


def _log(msg: str):
    """Log to file and stderr for immediate visibility."""
    line = f'[WEBHOOK] {msg}'
    print(line, file=sys.stderr, flush=True)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def _is_duplicate(msg_id: str) -> bool:
    """Verifica se a mensagem ja foi processada."""
    if not msg_id:
        return False
    if msg_id in _processed_ids:
        return True
    _processed_ids.add(msg_id)
    # Limpa cache se ficar muito grande
    if len(_processed_ids) > _MAX_CACHE:
        _processed_ids.clear()
    return False


def _extract_from_me(payload: dict) -> bool:
    """Extract fromMe from various WAHA payload formats."""
    # Direct field
    if 'fromMe' in payload:
        return bool(payload['fromMe'])
    # Inside id object
    id_data = payload.get('id', {})
    if isinstance(id_data, dict) and 'fromMe' in id_data:
        return bool(id_data['fromMe'])
    # Inside key object
    key_data = payload.get('key', {})
    if isinstance(key_data, dict) and 'fromMe' in key_data:
        return bool(key_data['fromMe'])
    return False


def _extract_msg_id(payload: dict) -> str:
    """Extract full message ID for deduplication."""
    msg_id_data = payload.get('id', '')
    if isinstance(msg_id_data, dict):
        # Prefer _serialized (full unique ID) over just id (can be truncated)
        return msg_id_data.get('_serialized', '') or msg_id_data.get('id', '')
    elif isinstance(msg_id_data, str):
        return msg_id_data
    return ''


@webhook_bp.route('/webhook', methods=['POST'])
def handle_webhook():
    """Recebe webhooks do WAHA e processa mensagens."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'status': 'no data'}), 200

    event = data.get('event', '')
    payload = data.get('payload', {})

    # ======================
    # ONLY accept message events
    # ======================
    if event not in ('message', 'message.any'):
        return jsonify({'status': 'ignored'}), 200

    # ======================
    # GLOBAL BOT ON/OFF — if disabled, ignore everything
    # ======================
    from models.database import BotConfig
    if BotConfig.get('bot_ativo', 'true') != 'true':
        return jsonify({'status': 'bot disabled'}), 200

    # Extract fields
    from_me = _extract_from_me(payload)
    body = (payload.get('body', '') or '').strip()
    from_jid = payload.get('from', '')
    to_jid = payload.get('to', '')

    # ======================
    # FILTER OLD MESSAGES — discard anything older than 30 seconds
    # ======================
    msg_timestamp = payload.get('timestamp', 0)
    now = time.time()
    if msg_timestamp > 0 and (now - msg_timestamp) > 30:
        return jsonify({'status': 'old message ignored'}), 200

    # ======================
    # EVENT ROUTING to avoid duplicates:
    # - 'message' event: only for RECEIVED messages (fromMe=false)
    # - 'message.any' event: only for SENT messages (fromMe=true)
    # ======================
    if event == 'message' and from_me:
        return jsonify({'status': 'ignored'}), 200
    if event == 'message.any' and not from_me:
        return jsonify({'status': 'ignored'}), 200

    # ======================
    # DEDUPLICATION by full message ID (_serialized)
    # ======================
    msg_id = _extract_msg_id(payload)
    if _is_duplicate(msg_id):
        return jsonify({'status': 'duplicate'}), 200

    # Save last payload to file for debugging
    try:
        with open('last_webhook.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

    _log(f'event={event} fromMe={from_me} from={from_jid} body="{body[:60]}"')

    # Ignore empty messages
    if not body:
        return jsonify({'status': 'empty'}), 200

    # Ignore groups (only process 1:1)
    if '@g.us' in from_jid or '@g.us' in (to_jid or ''):
        return jsonify({'status': 'group ignored'}), 200

    # ========================================
    # BRANCH 1: Mensagem com fromMe=True
    # Only react to EXPLICIT commands from the professor:
    #   !bot   -> unmute (reactivate bot)
    #   !parar -> mute (deactivate bot)
    # All other fromMe messages are IGNORED (could be bot or professor)
    # This avoids the problem of distinguishing bot-sent from professor-typed
    # ========================================
    if from_me:
        body_lower = body.lower().strip()
        student_jid = to_jid if to_jid else from_jid

        if '!bot' in body_lower:
            sess.unmute_bot(student_jid)
            _log(f'  -> UNMUTE bot for {student_jid}')
            return jsonify({'status': 'bot unmuted'}), 200

        if '!parar' in body_lower or '!mute' in body_lower or '!pausar' in body_lower:
            sess.mute_bot(student_jid)
            _log(f'  -> MUTE bot for {student_jid} (professor command)')
            return jsonify({'status': 'bot muted by professor'}), 200

        # Any other fromMe message: just ignore (could be bot response)
        _log(f'  -> IGNORED fromMe (no command)')
        return jsonify({'status': 'fromMe ignored'}), 200

    # ========================================
    # BRANCH 2: Mensagem do ALUNO (fromMe=False)
    # ========================================
    chat_id = from_jid

    # Resolver LID → número real para display (mas usa JID original para enviar)
    numero_real = waha.resolve_lid(from_jid, payload)
    _log(f'  -> ALUNO msg from {chat_id} (num={numero_real}): "{body}"')

    # Check if bot is active for this student
    if not sess.is_bot_active(chat_id):
        _log(f'  -> BOT MUTED for {chat_id}, silent')
        return jsonify({'status': 'bot muted'}), 200

    # Check if student is filling pre-queue form
    etapa, _ = sess.get_fila_state(chat_id)
    if etapa > 0:
        fila.process_form_answer(chat_id, body)
        _log(f'  -> FORM etapa {etapa} answered')
        return jsonify({'status': 'form answered'}), 200

    # Try to process as menu choice (number or "menu" command)
    if menu.process_menu_choice(chat_id, body):
        _log(f'  -> MENU handled')
        return jsonify({'status': 'menu handled'}), 200

    # Not a menu option -> show menu again with a helpful message
    _log(f'  -> UNKNOWN input, showing menu')
    waha.send_text(chat_id,
        'Desculpe, nao entendi sua mensagem.\n'
        'Por favor, escolha uma das opcoes abaixo:')
    menu.show_menu(chat_id, None)
    return jsonify({'status': 'menu shown'}), 200
