import os
import uuid
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response
from dotenv import load_dotenv
from models.database import db, init_db, MenuItem, Knowledge, SessionControl, QueueEntry, BotConfig
from bot.webhook import webhook_bp
from bot.ai import process_pdf_text
from bot import waha
from bot import queue as fila_module
import PyPDF2
import io
import json

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'dev-secret')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///typebot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

UPLOAD_FOLDER = os.path.join(app.static_folder, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'txt', 'csv'}

# Inicializa banco
init_db(app)

# Registra webhook do WAHA
app.register_blueprint(webhook_bp)

# Auto-configura webhook do WAHA
with app.app_context():
    try:
        waha.configure_webhook()
    except Exception:
        pass


@app.context_processor
def inject_bot_status():
    """Injeta status global do bot em todos os templates."""
    return {'bot_ativo': BotConfig.get('bot_ativo', 'true') == 'true'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_upload(file):
    """Salva arquivo e retorna (nome_original, nome_salvo)."""
    if not file or not file.filename:
        return None, None
    if not allowed_file(file.filename):
        return None, None
    ext = file.filename.rsplit('.', 1)[1].lower()
    safe_name = f'{uuid.uuid4().hex[:12]}.{ext}'
    file.save(os.path.join(UPLOAD_FOLDER, safe_name))
    return file.filename, safe_name


def _get_arquivos_disponiveis():
    """Lista arquivos disponíveis para seleção nos menus."""
    files = []
    if os.path.exists(UPLOAD_FOLDER):
        for fname in sorted(os.listdir(UPLOAD_FOLDER)):
            fpath = os.path.join(UPLOAD_FOLDER, fname)
            if os.path.isfile(fpath):
                ext = fname.rsplit('.', 1)[-1].lower()
                if ext in ('jpg', 'jpeg', 'png', 'gif', 'webp'):
                    emoji = '🖼️'
                elif ext == 'pdf':
                    emoji = '📄'
                else:
                    emoji = '📎'
                # Get original name from DB or UploadedFile
                from models.database import UploadedFile
                uf = UploadedFile.query.filter_by(saved_name=fname).first()
                nome = uf.original_name if uf else fname
                size = os.path.getsize(fpath)
                tamanho = f'{size / 1_000_000:.1f} MB' if size > 1_000_000 else f'{size / 1_000:.0f} KB'
                files.append({'path': fname, 'nome': nome, 'emoji': emoji, 'tamanho': tamanho})
    return files


# =====================================================
# DASHBOARD — Página Principal
# =====================================================
@app.route('/')
def dashboard():
    total_menus = MenuItem.query.filter_by(ativo=True).count()
    total_faqs = Knowledge.query.count()
    faqs_aprovadas = Knowledge.query.filter_by(status='Aprovado').count()
    faqs_pendentes = Knowledge.query.filter_by(status='Pendente').count()
    sessoes_ativas = SessionControl.query.filter_by(ia_ativa=True).count()
    sessoes_mutadas = SessionControl.query.filter_by(ia_ativa=False).count()
    fila_esperando = QueueEntry.query.filter_by(status='esperando').count()
    fila_atendendo = QueueEntry.query.filter_by(status='chamado').count()
    total_atendidos = QueueEntry.query.filter_by(status='atendido').count()

    # Fila completa para dashboard
    esperando = QueueEntry.query.filter_by(status='esperando')\
        .order_by(QueueEntry.criado_em).all()
    chamados = QueueEntry.query.filter_by(status='chamado')\
        .order_by(QueueEntry.chamado_em.desc()).all()
    ultimos_atendidos = QueueEntry.query.filter_by(status='atendido')\
        .order_by(QueueEntry.chamado_em.desc()).limit(10).all()

    # Status do WhatsApp
    try:
        waha_status = waha.get_session_status()
    except Exception:
        waha_status = {}

    return render_template('dashboard.html',
        total_menus=total_menus,
        total_faqs=total_faqs,
        faqs_aprovadas=faqs_aprovadas,
        faqs_pendentes=faqs_pendentes,
        sessoes_ativas=sessoes_ativas,
        sessoes_mutadas=sessoes_mutadas,
        fila_esperando=fila_esperando,
        fila_atendendo=fila_atendendo,
        total_atendidos=total_atendidos,
        esperando=esperando,
        chamados=chamados,
        ultimos_atendidos=ultimos_atendidos,
        waha_status=waha_status
    )


# =====================================================
# MENUS — CRUD
# =====================================================
@app.route('/menus')
def menus():
    items = MenuItem.query.filter_by(parent_id=None).order_by(MenuItem.posicao).all()
    return render_template('menus.html', items=items, parent=None,
        arquivos_disponiveis=_get_arquivos_disponiveis())


@app.route('/menus/<int:parent_id>')
def menus_sub(parent_id):
    parent = MenuItem.query.get_or_404(parent_id)
    items = parent.filhos.order_by(MenuItem.posicao).all()
    return render_template('menus.html', items=items, parent=parent,
        arquivos_disponiveis=_get_arquivos_disponiveis())


@app.route('/menus/add', methods=['POST'])
def menu_add():
    titulo = request.form.get('titulo', '').strip()
    resposta = request.form.get('resposta', '').strip() or None
    parent_id = request.form.get('parent_id') or None
    if parent_id:
        parent_id = int(parent_id)

    if not titulo:
        flash('Titulo e obrigatorio!', 'error')
        return redirect(request.referrer or url_for('menus'))

    # Calcula próxima posição
    if parent_id:
        max_pos = db.session.query(db.func.max(MenuItem.posicao))\
            .filter_by(parent_id=parent_id).scalar() or 0
    else:
        max_pos = db.session.query(db.func.max(MenuItem.posicao))\
            .filter_by(parent_id=None).scalar() or 0

    item = MenuItem(titulo=titulo, resposta=resposta, parent_id=parent_id, posicao=max_pos + 1)

    # Handle file selection from dropdown
    arquivo_path = request.form.get('arquivo_path', '').strip()
    if arquivo_path:
        item.arquivo_path = arquivo_path
        from models.database import UploadedFile
        uf = UploadedFile.query.filter_by(saved_name=arquivo_path).first()
        item.arquivo_nome = uf.original_name if uf else arquivo_path

    db.session.add(item)
    db.session.commit()
    flash(f'Menu "{titulo}" criado!', 'success')

    if parent_id:
        return redirect(url_for('menus_sub', parent_id=parent_id))
    return redirect(url_for('menus'))


@app.route('/menus/edit/<int:item_id>', methods=['POST'])
def menu_edit(item_id):
    item = MenuItem.query.get_or_404(item_id)
    item.titulo = request.form.get('titulo', item.titulo).strip()
    resposta = request.form.get('resposta', '').strip()
    item.resposta = resposta if resposta else None

    # Handle file selection from dropdown
    arquivo_path = request.form.get('arquivo_path', '').strip()
    if arquivo_path:
        item.arquivo_path = arquivo_path
        from models.database import UploadedFile
        uf = UploadedFile.query.filter_by(saved_name=arquivo_path).first()
        item.arquivo_nome = uf.original_name if uf else arquivo_path
    else:
        item.arquivo_path = None
        item.arquivo_nome = None

    db.session.commit()
    flash('Menu atualizado!', 'success')
    return redirect(request.referrer or url_for('menus'))


@app.route('/menus/delete/<int:item_id>', methods=['POST'])
def menu_delete(item_id):
    item = MenuItem.query.get_or_404(item_id)
    parent_id = item.parent_id
    # Deleta filhos recursivamente
    _delete_menu_tree(item)
    db.session.commit()
    flash('Menu excluido!', 'success')
    if parent_id:
        return redirect(url_for('menus_sub', parent_id=parent_id))
    return redirect(url_for('menus'))


def _delete_menu_tree(item):
    """Deleta item e todos os sub-menus recursivamente."""
    for child in item.filhos.all():
        _delete_menu_tree(child)
    db.session.delete(item)


@app.route('/menus/toggle/<int:item_id>', methods=['POST'])
def menu_toggle(item_id):
    item = MenuItem.query.get_or_404(item_id)
    item.ativo = not item.ativo
    db.session.commit()
    return redirect(request.referrer or url_for('menus'))


@app.route('/api/menus/reorder', methods=['POST'])
def menu_reorder():
    """API para reordenar menus via drag-and-drop."""
    data = request.get_json(silent=True) or {}
    ids = data.get('ids', [])
    for pos, item_id in enumerate(ids, start=1):
        item = MenuItem.query.get(item_id)
        if item:
            item.posicao = pos
    db.session.commit()
    return jsonify({'ok': True})

# =====================================================
# CONHECIMENTO — FAQs
# =====================================================
@app.route('/knowledge')
def knowledge():
    filtro = request.args.get('status', 'todos')
    if filtro == 'todos':
        faqs = Knowledge.query.order_by(Knowledge.criado_em.desc()).all()
    else:
        faqs = Knowledge.query.filter_by(status=filtro)\
            .order_by(Knowledge.criado_em.desc()).all()
    return render_template('knowledge.html', faqs=faqs, filtro=filtro)


def _faq_to_menu(faq):
    """Transforma FAQ aprovada em item de menu automaticamente."""
    categoria = (faq.categoria or 'Geral').strip()

    # Busca ou cria categoria como menu raiz
    menu_cat = MenuItem.query.filter_by(parent_id=None, titulo=categoria).first()
    if not menu_cat:
        max_pos = db.session.query(db.func.max(MenuItem.posicao))\
            .filter_by(parent_id=None).scalar() or 0
        menu_cat = MenuItem(titulo=categoria, posicao=max_pos + 1)
        db.session.add(menu_cat)
        db.session.flush()

    # Verifica se já existe um item com o mesmo título nessa categoria
    existing = MenuItem.query.filter_by(parent_id=menu_cat.id, titulo=faq.pergunta).first()
    if existing:
        return menu_cat.titulo  # Já existe, não duplica

    # Calcula posição
    max_pos = db.session.query(db.func.max(MenuItem.posicao))\
        .filter_by(parent_id=menu_cat.id).scalar() or 0

    new_item = MenuItem(
        parent_id=menu_cat.id,
        posicao=max_pos + 1,
        titulo=faq.pergunta,
        resposta=faq.resposta
    )
    db.session.add(new_item)
    return menu_cat.titulo


@app.route('/knowledge/approve/<int:faq_id>', methods=['POST'])
def knowledge_approve(faq_id):
    faq = Knowledge.query.get_or_404(faq_id)
    faq.status = 'Aprovado'
    cat = _faq_to_menu(faq)
    db.session.commit()
    flash(f'FAQ aprovada e adicionada ao menu "{cat}"!', 'success')
    return redirect(request.referrer or url_for('knowledge'))


@app.route('/knowledge/approve_all', methods=['POST'])
def knowledge_approve_all():
    pendentes = Knowledge.query.filter_by(status='Pendente').all()
    count = 0
    for faq in pendentes:
        faq.status = 'Aprovado'
        _faq_to_menu(faq)
        count += 1
    db.session.commit()
    flash(f'{count} FAQs aprovadas e adicionadas aos menus!', 'success')
    return redirect(url_for('knowledge'))


@app.route('/knowledge/reject_all', methods=['POST'])
def knowledge_reject_all():
    pendentes = Knowledge.query.filter_by(status='Pendente').all()
    count = 0
    for faq in pendentes:
        faq.status = 'Rejeitado'
        count += 1
    db.session.commit()
    flash(f'{count} FAQs rejeitadas!', 'success')
    return redirect(url_for('knowledge'))


@app.route('/knowledge/delete_all/<status>', methods=['POST'])
def knowledge_delete_all(status):
    if status == 'todos':
        count = Knowledge.query.delete()
    else:
        count = Knowledge.query.filter_by(status=status).delete()
    db.session.commit()
    flash(f'{count} FAQs apagadas!', 'success')
    return redirect(url_for('knowledge'))


@app.route('/knowledge/reject/<int:faq_id>', methods=['POST'])
def knowledge_reject(faq_id):
    faq = Knowledge.query.get_or_404(faq_id)
    faq.status = 'Rejeitado'
    db.session.commit()
    return redirect(request.referrer or url_for('knowledge'))


@app.route('/knowledge/delete/<int:faq_id>', methods=['POST'])
def knowledge_delete(faq_id):
    faq = Knowledge.query.get_or_404(faq_id)
    db.session.delete(faq)
    db.session.commit()
    flash('FAQ excluida!', 'success')
    return redirect(request.referrer or url_for('knowledge'))


@app.route('/knowledge/edit/<int:faq_id>', methods=['POST'])
def knowledge_edit(faq_id):
    faq = Knowledge.query.get_or_404(faq_id)
    faq.pergunta = request.form.get('pergunta', faq.pergunta)
    faq.resposta = request.form.get('resposta', faq.resposta)
    faq.categoria = request.form.get('categoria', faq.categoria)
    db.session.commit()
    flash('FAQ atualizada!', 'success')
    return redirect(request.referrer or url_for('knowledge'))


# =====================================================
# UPLOAD DE PDF
# =====================================================
@app.route('/upload', methods=['GET', 'POST'])
def upload_pdf():
    if request.method == 'POST':
        file = request.files.get('pdf')
        if not file or not file.filename.endswith('.pdf'):
            flash('Envie um arquivo PDF valido!', 'error')
            return redirect(url_for('upload_pdf'))

        instrucao = request.form.get('instrucao', '').strip()

        try:
            file_bytes = file.read()
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            text = ''
            for page in reader.pages:
                text += page.extract_text() or ''

            if not text.strip():
                flash('PDF sem texto detectavel!', 'error')
                return redirect(url_for('upload_pdf'))

            # Salva o PDF na pasta de uploads para consulta futura
            ext = file.filename.rsplit('.', 1)[1].lower()
            safe_name = f'{uuid.uuid4().hex[:12]}.{ext}'
            with open(os.path.join(UPLOAD_FOLDER, safe_name), 'wb') as f:
                f.write(file_bytes)

            # Salva registro no banco
            from models.database import UploadedFile
            uf = UploadedFile(
                original_name=file.filename,
                saved_name=safe_name,
                file_type='pdf',
                file_size=len(file_bytes)
            )
            db.session.add(uf)
            db.session.commit()

            faqs = process_pdf_text(text, file.filename, instrucao=instrucao)
            flash(f'PDF processado! {len(faqs)} FAQs geradas. Arquivo salvo na aba Arquivos.', 'success')

        except Exception as e:
            flash(f'Erro ao processar PDF: {str(e)}', 'error')

        return redirect(url_for('knowledge', status='Pendente'))

    return render_template('upload.html')


# =====================================================
# SESSÕES — Visualizar
# =====================================================
@app.route('/sessions')
def sessions():
    sessoes = SessionControl.query.order_by(SessionControl.atualizado_em.desc()).all()
    return render_template('sessions.html', sessoes=sessoes)


@app.route('/sessions/unmute/<int:session_id>', methods=['POST'])
def session_unmute(session_id):
    s = SessionControl.query.get_or_404(session_id)
    s.ia_ativa = True
    db.session.commit()
    flash(f'Bot reativado para {s.remote_jid}!', 'success')
    return redirect(url_for('sessions'))


@app.route('/sessions/delete/<int:session_id>', methods=['POST'])
def session_delete(session_id):
    s = SessionControl.query.get_or_404(session_id)
    db.session.delete(s)
    db.session.commit()
    flash('Sessao excluida!', 'success')
    return redirect(url_for('sessions'))


# =====================================================
# WHATSAPP — QR Code e Status da Sessão
# =====================================================
@app.route('/whatsapp')
def whatsapp():
    return render_template('whatsapp.html')


@app.route('/api/whatsapp/status')
def whatsapp_status():
    """Retorna status da sessão WAHA como JSON."""
    data = waha.get_session_status()
    return jsonify(data)


@app.route('/api/whatsapp/qr')
def whatsapp_qr():
    """Proxy para QR code do WAHA."""
    img_bytes, content_type = waha.get_qr_code()
    if img_bytes:
        return Response(img_bytes, mimetype=content_type or 'image/png')
    return Response('QR not available', status=404)


@app.route('/api/whatsapp/start', methods=['POST'])
def whatsapp_start():
    ok = waha.start_session()
    # Re-configura webhook ao iniciar sessão
    if ok:
        waha.configure_webhook()
    return jsonify({'success': ok})


@app.route('/api/whatsapp/stop', methods=['POST'])
def whatsapp_stop():
    ok = waha.stop_session()
    return jsonify({'success': ok})


@app.route('/api/bot/toggle', methods=['POST'])
def bot_toggle():
    """Liga/desliga o bot globalmente sem desconectar o WhatsApp."""
    current = BotConfig.get('bot_ativo', 'true')
    new_val = 'false' if current == 'true' else 'true'
    BotConfig.set('bot_ativo', new_val)
    return jsonify({'bot_ativo': new_val == 'true'})


# =====================================================
# FILA — Sistema de fila para coordenador
# =====================================================
@app.route('/fila')
def fila():
    esperando = QueueEntry.query.filter_by(status='esperando')\
        .order_by(QueueEntry.criado_em).all()
    chamados = QueueEntry.query.filter_by(status='chamado')\
        .order_by(QueueEntry.chamado_em.desc()).all()
    atendidos = QueueEntry.query.filter_by(status='atendido')\
        .order_by(QueueEntry.chamado_em.desc()).limit(10).all()

    # Mensagem customizável
    msg_padrao = ('*Sua vez chegou, {nome}!*\n\n'
                  'O coordenador vai te atender agora.\n'
                  'Aguarde a mensagem dele nesta conversa.')
    mensagem_chamada = BotConfig.get('mensagem_chamada', msg_padrao)

    # Perguntas do formulário
    perguntas = fila_module.get_perguntas()

    # Mensagens customizáveis
    msg_entrada_padrao = fila_module.MENSAGEM_ENTRADA_PADRAO
    mensagem_entrada = BotConfig.get('mensagem_entrada', msg_entrada_padrao)

    msg_termino_padrao = fila_module.MENSAGEM_TERMINO_PADRAO
    mensagem_termino = BotConfig.get('mensagem_termino', msg_termino_padrao)

    msg_cancel_padrao = fila_module.MENSAGEM_CANCELAMENTO_PADRAO
    mensagem_cancelamento = BotConfig.get('mensagem_cancelamento', msg_cancel_padrao)

    return render_template('fila.html',
        esperando=esperando, chamados=chamados, atendidos=atendidos,
        mensagem_chamada=mensagem_chamada, perguntas=perguntas,
        mensagem_entrada=mensagem_entrada, mensagem_termino=mensagem_termino,
        mensagem_cancelamento=mensagem_cancelamento)


@app.route('/fila/chamar', methods=['POST'])
def fila_chamar():
    entry = fila_module.call_next()
    if entry:
        flash(f'Chamando {entry.nome} ({entry.numero})!', 'success')
    else:
        flash('Fila vazia!', 'warning')
    return redirect(request.referrer or url_for('fila'))


@app.route('/fila/finalizar/<int:entry_id>', methods=['POST'])
def fila_finalizar(entry_id):
    fila_module.finish_attendance(entry_id)
    flash('Atendimento finalizado!', 'success')
    return redirect(url_for('fila'))


@app.route('/fila/cancelar/<int:entry_id>', methods=['POST'])
def fila_cancelar(entry_id):
    fila_module.cancel_attendance(entry_id)
    flash('Agendamento excluído!', 'success')
    return redirect(url_for('fila'))


@app.route('/fila/remover/<int:entry_id>', methods=['POST'])
def fila_remover(entry_id):
    fila_module.remove_from_queue(entry_id)
    flash('Removido da fila!', 'success')
    return redirect(url_for('fila'))


@app.route('/fila/responder/<int:entry_id>', methods=['POST'])
def fila_responder(entry_id):
    entry = QueueEntry.query.get_or_404(entry_id)
    mensagem = request.form.get('mensagem', '').strip()
    if mensagem:
        waha.send_text(entry.remote_jid, mensagem)
        flash(f'Mensagem enviada para {entry.nome}!', 'success')
    else:
        flash('Digite uma mensagem!', 'error')
    return redirect(url_for('fila'))


@app.route('/api/fila/responder/<int:entry_id>', methods=['POST'])
def fila_responder_ajax(entry_id):
    """API AJAX para enviar mensagem sem recarregar a página."""
    entry = QueueEntry.query.get_or_404(entry_id)
    data = request.get_json(silent=True) or {}
    mensagem = data.get('mensagem', '').strip()
    if mensagem:
        waha.send_text(entry.remote_jid, mensagem)
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'Mensagem vazia'}), 400


@app.route('/fila/config', methods=['POST'])
def fila_config():
    saved = []
    msg_chamada = request.form.get('mensagem_chamada', '').strip()
    if msg_chamada:
        BotConfig.set('mensagem_chamada', msg_chamada)
        saved.append('chamada')
    msg_entrada = request.form.get('mensagem_entrada', '').strip()
    if msg_entrada:
        BotConfig.set('mensagem_entrada', msg_entrada)
        saved.append('entrada')
    msg_termino = request.form.get('mensagem_termino', '').strip()
    if msg_termino:
        BotConfig.set('mensagem_termino', msg_termino)
        saved.append('término')
    msg_cancel = request.form.get('mensagem_cancelamento', '').strip()
    if msg_cancel:
        BotConfig.set('mensagem_cancelamento', msg_cancel)
        saved.append('cancelamento')
    if saved:
        flash('Mensagem atualizada!', 'success')
    return redirect(url_for('fila'))


@app.route('/api/fila/mensagens/<path:jid>')
def fila_mensagens(jid):
    """API AJAX para buscar mensagens do chat via WAHA."""
    msgs = waha.get_messages(jid, limit=30)
    result = []
    for m in msgs:
        result.append({
            'body': m.get('body', ''),
            'fromMe': m.get('fromMe', False),
            'timestamp': m.get('timestamp', 0)
        })
    return jsonify(result)


@app.route('/fila/pular/<int:entry_id>', methods=['POST'])
def fila_pular(entry_id):
    fila_module.skip_in_queue(entry_id)
    flash('Aluno movido para o topo da fila!', 'success')
    return redirect(url_for('fila'))


@app.route('/fila/perguntas', methods=['POST'])
def fila_perguntas():
    """Salva perguntas do formulário pré-fila."""
    campos = request.form.getlist('campo[]')
    textos = request.form.getlist('pergunta[]')
    perguntas = []
    for campo, texto in zip(campos, textos):
        campo = campo.strip()
        texto = texto.strip()
        if campo and texto:
            perguntas.append({'campo': campo, 'pergunta': texto})
    fila_module.set_perguntas(perguntas)
    flash(f'{len(perguntas)} perguntas salvas!', 'success')
    return redirect(url_for('fila'))


# =====================================================
# ARQUIVOS — Gerenciamento de arquivos
# =====================================================
@app.route('/arquivos')
def arquivos():
    """Lista todos os arquivos no upload folder."""
    from models.database import UploadedFile
    files = []
    if os.path.exists(UPLOAD_FOLDER):
        for fname in sorted(os.listdir(UPLOAD_FOLDER)):
            fpath = os.path.join(UPLOAD_FOLDER, fname)
            if os.path.isfile(fpath):
                ext = fname.rsplit('.', 1)[-1].lower()
                if ext in ('jpg', 'jpeg', 'png', 'gif', 'webp'):
                    tipo = 'image'
                elif ext == 'pdf':
                    tipo = 'pdf'
                else:
                    tipo = 'document'

                # Check usage in menus
                usado_em = MenuItem.query.filter_by(arquivo_path=fname).count()

                # Original name from UploadedFile
                uf = UploadedFile.query.filter_by(saved_name=fname).first()
                nome_original = uf.original_name if uf else fname

                size = os.path.getsize(fpath)
                if size > 1_000_000:
                    tamanho = f'{size / 1_000_000:.1f} MB'
                else:
                    tamanho = f'{size / 1_000:.0f} KB'

                files.append({
                    'filename': fname,
                    'nome': nome_original,
                    'tipo': tipo,
                    'tamanho': tamanho,
                    'usado_em': usado_em,
                    'link': f'{request.host_url}static/uploads/{fname}'
                })

    return render_template('arquivos.html', arquivos=files)


@app.route('/arquivos/upload', methods=['POST'])
def arquivo_upload():
    file = request.files.get('arquivo')
    if file and file.filename:
        nome_orig, nome_salvo = save_upload(file)
        if nome_salvo:
            from models.database import UploadedFile
            uf = UploadedFile(
                original_name=nome_orig,
                saved_name=nome_salvo,
                file_type=nome_salvo.rsplit('.', 1)[-1].lower(),
                file_size=os.path.getsize(os.path.join(UPLOAD_FOLDER, nome_salvo))
            )
            db.session.add(uf)
            db.session.commit()
            flash(f'Arquivo "{nome_orig}" enviado!', 'success')
        else:
            flash('Tipo de arquivo nao permitido!', 'error')
    else:
        flash('Selecione um arquivo!', 'error')
    return redirect(url_for('arquivos'))


@app.route('/arquivos/delete/<filename>', methods=['POST'])
def arquivo_delete(filename):
    # Only delete if not used by any menu item
    usado = MenuItem.query.filter_by(arquivo_path=filename).count()
    if usado > 0:
        flash(f'Arquivo em uso por {usado} item(s)! Remova dos menus primeiro.', 'error')
        return redirect(url_for('arquivos'))

    fpath = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.exists(fpath):
        os.remove(fpath)
    # Remove do banco
    from models.database import UploadedFile
    UploadedFile.query.filter_by(saved_name=filename).delete()
    db.session.commit()
    flash('Arquivo excluido!', 'success')
    return redirect(url_for('arquivos'))


@app.route('/arquivos/gerar-perguntas/<filename>', methods=['POST'])
def arquivo_gerar_perguntas(filename):
    """Gera perguntas de um PDF já salvo."""
    fpath = os.path.join(UPLOAD_FOLDER, filename)
    if not os.path.exists(fpath) or not filename.endswith('.pdf'):
        flash('Arquivo PDF nao encontrado!', 'error')
        return redirect(url_for('arquivos'))

    instrucao = request.form.get('instrucao', '').strip()

    try:
        with open(fpath, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            text = ''
            for page in reader.pages:
                text += page.extract_text() or ''

        if not text.strip():
            flash('PDF sem texto detectavel!', 'error')
            return redirect(url_for('arquivos'))

        from models.database import UploadedFile
        uf = UploadedFile.query.filter_by(saved_name=filename).first()
        nome = uf.original_name if uf else filename

        faqs = process_pdf_text(text, nome, instrucao=instrucao)
        flash(f'{len(faqs)} FAQs geradas a partir de "{nome}"!', 'success')
    except Exception as e:
        flash(f'Erro: {str(e)}', 'error')

    return redirect(url_for('knowledge', status='Pendente'))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true')
