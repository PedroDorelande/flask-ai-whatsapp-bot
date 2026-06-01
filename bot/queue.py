"""Sistema de fila de atendimento para coordenador."""
import sys
import json
from datetime import datetime
from models.database import db, QueueEntry, BotConfig
from bot import waha, session as sess


# Dias da semana em português (indexados por weekday: 0=segunda)
DIAS_SEMANA = ['segunda', 'terca', 'quarta', 'quinta', 'sexta', 'sabado', 'domingo']
DIAS_SEMANA_DISPLAY = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']

# Horário padrão (usado se não houver configuração salva)
HORARIOS_PADRAO = {
    'segunda': {'ativo': True, 'inicio': '08:00', 'fim': '18:00'},
    'terca':   {'ativo': True, 'inicio': '08:00', 'fim': '18:00'},
    'quarta':  {'ativo': True, 'inicio': '08:00', 'fim': '18:00'},
    'quinta':  {'ativo': True, 'inicio': '08:00', 'fim': '18:00'},
    'sexta':   {'ativo': True, 'inicio': '08:00', 'fim': '18:00'},
    'sabado':  {'ativo': False, 'inicio': '', 'fim': ''},
    'domingo': {'ativo': False, 'inicio': '', 'fim': ''},
}


def get_horarios_semana():
    """Retorna dict com horários de cada dia da semana."""
    db.session.expire_all()
    raw = BotConfig.get('horarios_semana', '')
    if raw:
        try:
            h = json.loads(raw)
            if isinstance(h, dict):
                return h
        except Exception:
            pass
    return HORARIOS_PADRAO.copy()


def set_horarios_semana(horarios: dict):
    """Salva horários da semana no banco."""
    BotConfig.set('horarios_semana', json.dumps(horarios, ensure_ascii=False))


def _get_horario_atendimento():
    """Retorna string de horário baseado no dia atual da semana."""
    horarios = get_horarios_semana()
    hoje_idx = datetime.now().weekday()  # 0=segunda, 6=domingo
    dia_key = DIAS_SEMANA[hoje_idx]
    dia_display = DIAS_SEMANA_DISPLAY[hoje_idx]

    config_hoje = horarios.get(dia_key, {})
    if config_hoje.get('ativo') and config_hoje.get('inicio') and config_hoje.get('fim'):
        return f'{dia_display}: {config_hoje["inicio"]} às {config_hoje["fim"]}'

    # Não atende hoje — encontrar próximo dia com atendimento
    for offset in range(1, 8):
        prox_idx = (hoje_idx + offset) % 7
        prox_key = DIAS_SEMANA[prox_idx]
        prox_display = DIAS_SEMANA_DISPLAY[prox_idx]
        prox_config = horarios.get(prox_key, {})
        if prox_config.get('ativo') and prox_config.get('inicio'):
            return (f'Sem atendimento hoje ({dia_display}). '
                    f'Proximo: {prox_display}, {prox_config["inicio"]} às {prox_config["fim"]}')

    return 'Horario de atendimento nao configurado'


# Mensagem padrão quando chama o próximo (editável na dashboard)
MENSAGEM_CHAMADA_PADRAO = (
    '*Sua vez chegou, {nome}!*\n\n'
    'O coordenador vai te atender agora.\n'
    'Aguarde a mensagem dele nesta conversa.'
)

# Perguntas padrão do formulário pré-fila
PERGUNTAS_PADRAO = [
    {'campo': 'nome_completo', 'pergunta': 'Qual seu *nome completo*?'},
    {'campo': 'matricula', 'pergunta': 'Qual sua *matrícula*?'},
    {'campo': 'assunto', 'pergunta': 'Qual o *assunto* que deseja tratar?'},
]


def _get_mensagem_chamada():
    """Retorna a mensagem de chamada customizada."""
    db.session.expire_all()
    return BotConfig.get('mensagem_chamada', MENSAGEM_CHAMADA_PADRAO)


MENSAGEM_ENTRADA_PADRAO = (
    'Voce entrou na fila para falar com o coordenador!\n\n'
    '*Nome:* {nome}\n'
    '*Posicao:* {posicao} de {total}\n'
    '*Horario de atendimento:* {horario}\n\n'
    'Envie *fila* a qualquer momento para ver sua posicao.\n'
    'Envie *sair* para sair da fila.'
)

MENSAGEM_POSICAO_PADRAO = (
    'Sua posicao na fila: *{posicao}* de {total}\n\n'
    '*Horario de atendimento:* {horario}\n\n'
    'Aguarde, voce sera notificado quando for sua vez!'
)

MENSAGEM_TERMINO_PADRAO = (
    'Atendimento finalizado! Obrigado.\n\n'
    'Envie *menu* se precisar de mais alguma coisa.'
)


def _get_mensagem_entrada():
    """Retorna a mensagem de entrada na fila customizada."""
    db.session.expire_all()
    return BotConfig.get('mensagem_entrada', MENSAGEM_ENTRADA_PADRAO)


def _get_mensagem_termino():
    """Retorna a mensagem de término de atendimento customizada."""
    db.session.expire_all()
    return BotConfig.get('mensagem_termino', MENSAGEM_TERMINO_PADRAO)


def _get_mensagem_posicao():
    """Retorna a mensagem de consulta de posição customizada."""
    db.session.expire_all()
    return BotConfig.get('mensagem_posicao', MENSAGEM_POSICAO_PADRAO)


MENSAGEM_CANCELAMENTO_PADRAO = (
    'Seu agendamento foi cancelado pelo coordenador.\n\n'
    'Se precisar, entre novamente pelo menu.'
)


def _get_mensagem_cancelamento():
    """Retorna a mensagem de cancelamento customizada."""
    db.session.expire_all()
    return BotConfig.get('mensagem_cancelamento', MENSAGEM_CANCELAMENTO_PADRAO)


def get_perguntas():
    """Retorna lista de perguntas do formulário pré-fila."""
    raw = BotConfig.get('fila_perguntas', '')
    if raw:
        try:
            perguntas = json.loads(raw)
            if isinstance(perguntas, list) and len(perguntas) > 0:
                return perguntas
        except Exception:
            pass
    return PERGUNTAS_PADRAO


def set_perguntas(perguntas: list):
    """Salva perguntas do formulário no banco."""
    BotConfig.set('fila_perguntas', json.dumps(perguntas, ensure_ascii=False))


def start_form(chat_id: str):
    """Inicia o formulário pré-fila. Envia primeira pergunta."""
    perguntas = get_perguntas()
    if not perguntas:
        # Sem perguntas configuradas — entra direto
        enter_queue(chat_id, 'Aluno', {})
        return

    sess.start_fila_form(chat_id)
    waha.send_text(chat_id,
        'Para entrar na fila, preciso de algumas informacoes:')
    waha.send_text(chat_id, perguntas[0]['pergunta'])


def process_form_answer(chat_id: str, resposta: str) -> bool:
    """Processa resposta do formulário. Retorna True se ainda está coletando."""
    etapa, dados = sess.get_fila_state(chat_id)
    if etapa <= 0:
        return False  # Não está preenchendo formulário

    perguntas = get_perguntas()
    idx = etapa - 1  # etapa 1 = pergunta 0

    if idx >= len(perguntas):
        # Já acabou, não deveria chegar aqui
        sess.clear_fila_form(chat_id)
        return False

    # Salva resposta atual
    campo = perguntas[idx]['campo']
    nova_etapa = sess.advance_fila_form(chat_id, campo, resposta)
    next_idx = nova_etapa - 1

    if next_idx < len(perguntas):
        # Ainda tem perguntas — envia a próxima
        waha.send_text(chat_id, perguntas[next_idx]['pergunta'])
        return True
    else:
        # Formulário completo — entra na fila
        _, dados_finais = sess.get_fila_state(chat_id)
        nome = dados_finais.get('nome_completo', 'Aluno')
        sess.clear_fila_form(chat_id)
        enter_queue(chat_id, nome, dados_finais)
        return True


def enter_queue(chat_id: str, nome: str = None, dados: dict = None):
    """Adiciona aluno na fila. Retorna posição ou 0 se já está."""
    existing = QueueEntry.query.filter_by(
        remote_jid=chat_id, status='esperando'
    ).first()

    if existing:
        pos = QueueEntry.posicao_na_fila(chat_id)
        horario = _get_horario_atendimento()
        waha.send_text(chat_id,
            f'Voce ja esta na fila!\n\n'
            f'*Posicao:* {pos} de {QueueEntry.total_esperando()}\n'
            f'*Horario de atendimento:* {horario}\n\n'
            f'Envie *fila* para consultar sua posicao.')
        return pos

    # Resolver número real
    numero_real = waha.resolve_lid(chat_id)

    entry = QueueEntry(
        remote_jid=chat_id,
        nome=nome or 'Aluno',
        numero_real=numero_real,
        dados=json.dumps(dados or {}, ensure_ascii=False)
    )
    db.session.add(entry)
    db.session.commit()

    pos = QueueEntry.posicao_na_fila(chat_id)
    total = QueueEntry.total_esperando()

    nome_display = nome or 'Aluno'
    msg_template = _get_mensagem_entrada()
    horario = _get_horario_atendimento()
    try:
        msg = msg_template.format(
            nome=nome_display, posicao=pos, total=total,
            horario=horario)
    except Exception:
        msg = msg_template.replace('{nome}', nome_display)\
            .replace('{posicao}', str(pos)).replace('{total}', str(total))\
            .replace('{horario}', horario)
    waha.send_text(chat_id, msg)

    print(f'[FILA] {chat_id} entrou na fila (posicao {pos})',
          file=sys.stderr, flush=True)
    return pos


def check_position(chat_id: str):
    """Verifica posição do aluno na fila e envia mensagem."""
    pos = QueueEntry.posicao_na_fila(chat_id)

    if pos == 0:
        waha.send_text(chat_id,
            'Voce nao esta na fila no momento.\n\n'
            'Para entrar, selecione *Falar com o Coordenador* no menu.')
    else:
        total = QueueEntry.total_esperando()
        horario = _get_horario_atendimento()
        msg_template = _get_mensagem_posicao()
        try:
            msg = msg_template.format(
                posicao=pos, total=total, horario=horario)
        except Exception:
            msg = msg_template.replace('{posicao}', str(pos))\
                .replace('{total}', str(total))\
                .replace('{horario}', horario)
        waha.send_text(chat_id, msg)


def leave_queue(chat_id: str):
    """Remove aluno da fila."""
    entry = QueueEntry.query.filter_by(
        remote_jid=chat_id, status='esperando'
    ).first()

    if entry:
        entry.status = 'atendido'
        db.session.commit()
        waha.send_text(chat_id, 'Voce saiu da fila. Ate logo!')
        print(f'[FILA] {chat_id} saiu da fila',
              file=sys.stderr, flush=True)
    else:
        waha.send_text(chat_id, 'Voce nao estava na fila.')


def call_next():
    """Chama o próximo da fila. Muta o bot automaticamente."""
    entry = QueueEntry.proximo()
    if not entry:
        return None

    entry.status = 'chamado'
    entry.chamado_em = datetime.utcnow()
    db.session.commit()

    # AUTO-MUTE: para o bot para este aluno enquanto coordenador atende
    sess.mute_bot(entry.remote_jid)

    # Envia mensagem customizável com nome
    msg_template = _get_mensagem_chamada()
    nome = entry.nome or 'Aluno'
    try:
        msg = msg_template.format(nome=nome)
    except Exception:
        msg = msg_template.replace('{nome}', nome)
    waha.send_text(entry.remote_jid, msg)

    print(f'[FILA] Chamando {entry.remote_jid} ({nome}) + BOT MUTADO',
          file=sys.stderr, flush=True)
    return entry


def finish_attendance(entry_id: int):
    """Marca atendimento como concluído. Reativa o bot."""
    entry = QueueEntry.query.get(entry_id)
    if entry:
        entry.status = 'atendido'
        db.session.commit()

        # AUTO-UNMUTE: reativa o bot para este aluno
        sess.unmute_bot(entry.remote_jid)

        waha.send_text(entry.remote_jid, _get_mensagem_termino())

        print(f'[FILA] Atendimento de {entry.remote_jid} finalizado + BOT REATIVADO',
              file=sys.stderr, flush=True)


def finish_silent(entry_id: int):
    """Marca atendimento como concluído SEM enviar mensagem.
    
    Usado quando o coordenador atende pelo celular e não quer
    que o bot envie mensagem automática de término.
    """
    entry = QueueEntry.query.get(entry_id)
    if entry:
        entry.status = 'atendido'
        db.session.commit()

        # AUTO-UNMUTE: reativa o bot para este aluno
        sess.unmute_bot(entry.remote_jid)

        print(f'[FILA] Atendimento de {entry.remote_jid} finalizado (silencioso) + BOT REATIVADO',
              file=sys.stderr, flush=True)


def cancel_attendance(entry_id: int):
    """Cancela agendamento. Envia mensagem e reativa o bot."""
    entry = QueueEntry.query.get(entry_id)
    if entry:
        jid = entry.remote_jid

        # Reativa o bot
        sess.unmute_bot(jid)

        # Envia mensagem de cancelamento
        waha.send_text(jid, _get_mensagem_cancelamento())

        # Remove da fila
        db.session.delete(entry)
        db.session.commit()

        print(f'[FILA] Agendamento de {jid} cancelado + BOT REATIVADO',
              file=sys.stderr, flush=True)


def remove_from_queue(entry_id: int):
    """Remove entrada da fila."""
    entry = QueueEntry.query.get(entry_id)
    if entry:
        jid = entry.remote_jid
        if entry.status == 'esperando':
            waha.send_text(jid,
                'Voce foi removido da fila pelo coordenador.\n'
                'Se precisar, entre novamente pelo menu.')
        db.session.delete(entry)
        db.session.commit()


def skip_in_queue(entry_id: int):
    """Move entrada para o topo da fila (mais antiga)."""
    entry = QueueEntry.query.get(entry_id)
    if entry and entry.status == 'esperando':
        # Pega o item mais antigo e define este como 1 segundo antes
        oldest = QueueEntry.query.filter_by(status='esperando')\
            .order_by(QueueEntry.criado_em).first()
        if oldest and oldest.id != entry.id:
            from datetime import timedelta
            entry.criado_em = oldest.criado_em - timedelta(seconds=1)
            db.session.commit()
