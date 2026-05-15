from models.database import db, SessionControl
from datetime import datetime


def get_session(remote_jid: str) -> SessionControl:
    """Busca ou cria sessão para o aluno."""
    session = SessionControl.query.filter_by(remote_jid=remote_jid).first()
    if not session:
        session = SessionControl(remote_jid=remote_jid, ia_ativa=True)
        db.session.add(session)
        db.session.commit()
    return session


def mute_bot(remote_jid: str):
    """Professor assumiu — desativa bot para este aluno."""
    session = get_session(remote_jid)
    session.ia_ativa = False
    session.atualizado_em = datetime.utcnow()
    db.session.commit()
    return session


def unmute_bot(remote_jid: str):
    """Professor enviou !bot — reativa bot para este aluno."""
    session = get_session(remote_jid)
    session.ia_ativa = True
    session.menu_atual_id = None  # Reset menu
    session.atualizado_em = datetime.utcnow()
    db.session.commit()
    return session


def is_bot_active(remote_jid: str) -> bool:
    """Verifica se o bot está ativo para este aluno."""
    session = get_session(remote_jid)
    return session.ia_ativa


def set_menu_position(remote_jid: str, menu_id: int | None):
    """Atualiza posição atual do aluno no menu."""
    session = get_session(remote_jid)
    session.menu_atual_id = menu_id
    session.atualizado_em = datetime.utcnow()
    db.session.commit()


def get_menu_position(remote_jid: str) -> int | None:
    """Retorna o ID do menu atual do aluno."""
    session = get_session(remote_jid)
    return session.menu_atual_id


# =====================================================
# Formulário pré-fila
# =====================================================
import json


def start_fila_form(remote_jid: str):
    """Inicia formulário pré-fila (etapa 1)."""
    session = get_session(remote_jid)
    session.fila_etapa = 1
    session.fila_dados = '{}'
    session.atualizado_em = datetime.utcnow()
    db.session.commit()


def get_fila_state(remote_jid: str) -> tuple[int, dict]:
    """Retorna (etapa, dados_parciais) do formulário."""
    session = get_session(remote_jid)
    etapa = session.fila_etapa or 0
    dados = {}
    if session.fila_dados:
        try:
            dados = json.loads(session.fila_dados)
        except Exception:
            pass
    return etapa, dados


def advance_fila_form(remote_jid: str, campo: str, valor: str) -> int:
    """Salva resposta do campo atual e avança para a próxima etapa. Retorna nova etapa."""
    session = get_session(remote_jid)
    dados = {}
    if session.fila_dados:
        try:
            dados = json.loads(session.fila_dados)
        except Exception:
            pass
    dados[campo] = valor
    session.fila_dados = json.dumps(dados, ensure_ascii=False)
    session.fila_etapa = (session.fila_etapa or 0) + 1
    session.atualizado_em = datetime.utcnow()
    db.session.commit()
    return session.fila_etapa


def clear_fila_form(remote_jid: str):
    """Limpa estado do formulário."""
    session = get_session(remote_jid)
    session.fila_etapa = 0
    session.fila_dados = None
    session.atualizado_em = datetime.utcnow()
    db.session.commit()

