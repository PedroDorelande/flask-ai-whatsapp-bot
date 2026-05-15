from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class MenuItem(db.Model):
    """Menu em árvore — cada item pode ter filhos (sub-menus) ou uma resposta (folha)."""
    __tablename__ = 'menu_items'

    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('menu_items.id'), nullable=True)
    posicao = db.Column(db.Integer, nullable=False, default=1)
    titulo = db.Column(db.String(200), nullable=False)
    resposta = db.Column(db.Text, nullable=True)  # NULL = tem sub-menus
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    # Arquivo anexo (imagem, PDF, documento)
    arquivo_nome = db.Column(db.String(255), nullable=True)  # Nome original
    arquivo_path = db.Column(db.String(500), nullable=True)  # Caminho no servidor

    filhos = db.relationship('MenuItem', backref=db.backref('pai', remote_side=[id]),
                             order_by='MenuItem.posicao', lazy='dynamic')

    @property
    def has_filhos(self):
        """Retorna True se tem filhos ativos."""
        return self.filhos.filter_by(ativo=True).count() > 0

    @property
    def is_folha(self):
        """Retorna True se é item final (não tem filhos ativos)."""
        return not self.has_filhos

    @property
    def tem_arquivo(self):
        """Retorna True se tem arquivo anexo."""
        return bool(self.arquivo_path)

    @property
    def arquivo_tipo(self):
        """Retorna tipo do arquivo: 'image', 'pdf', 'document'."""
        if not self.arquivo_nome:
            return None
        ext = self.arquivo_nome.rsplit('.', 1)[-1].lower()
        if ext in ('jpg', 'jpeg', 'png', 'gif', 'webp'):
            return 'image'
        elif ext == 'pdf':
            return 'pdf'
        else:
            return 'document'

    def filhos_ativos(self):
        """Retorna filhos ativos ordenados por posição."""
        return self.filhos.filter_by(ativo=True).order_by(MenuItem.posicao).all()

    @staticmethod
    def get_menu_raiz():
        """Retorna itens do menu principal (sem pai)."""
        return MenuItem.query.filter_by(parent_id=None, ativo=True)\
            .order_by(MenuItem.posicao).all()


class Knowledge(db.Model):
    """Base de conhecimento — FAQs extraídas de PDFs."""
    __tablename__ = 'conhecimento'

    id = db.Column(db.Integer, primary_key=True)
    pergunta = db.Column(db.Text, nullable=False)
    resposta = db.Column(db.Text, nullable=False)
    categoria = db.Column(db.String(100), default='Geral')
    origem = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default='Pendente')  # Pendente | Aprovado | Rejeitado
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


class SessionControl(db.Model):
    """Controle de sessão — rastreia estado do bot para cada aluno."""
    __tablename__ = 'sessao_controle'

    id = db.Column(db.Integer, primary_key=True)
    remote_jid = db.Column(db.String(50), unique=True, nullable=False)
    ia_ativa = db.Column(db.Boolean, default=True)
    menu_atual_id = db.Column(db.Integer, db.ForeignKey('menu_items.id'), nullable=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # Estado do formulário pré-fila
    fila_etapa = db.Column(db.Integer, default=0)  # 0=não preenchendo, 1=pergunta1, 2=pergunta2...
    fila_dados = db.Column(db.Text, nullable=True)  # JSON com respostas parciais

    menu_atual = db.relationship('MenuItem', foreign_keys=[menu_atual_id])


class BotConfig(db.Model):
    """Configurações editáveis do bot."""
    __tablename__ = 'bot_config'

    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(100), unique=True, nullable=False)
    valor = db.Column(db.Text, nullable=True)

    @staticmethod
    def get(chave, default=''):
        config = BotConfig.query.filter_by(chave=chave).first()
        return config.valor if config and config.valor else default

    @staticmethod
    def set(chave, valor):
        config = BotConfig.query.filter_by(chave=chave).first()
        if config:
            config.valor = valor
        else:
            config = BotConfig(chave=chave, valor=valor)
            db.session.add(config)
        db.session.commit()


class QueueEntry(db.Model):
    """Fila de atendimento — alunos esperando para falar com coordenador."""
    __tablename__ = 'fila_atendimento'

    id = db.Column(db.Integer, primary_key=True)
    remote_jid = db.Column(db.String(50), nullable=False)
    nome = db.Column(db.String(200), nullable=True)  # Nome coletado no formulário
    numero_real = db.Column(db.String(20), nullable=True)  # Telefone resolvido (sem LID)
    dados = db.Column(db.Text, nullable=True)  # JSON com respostas do formulário
    status = db.Column(db.String(20), default='esperando')  # esperando | chamado | atendido
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    chamado_em = db.Column(db.DateTime, nullable=True)

    @property
    def numero(self):
        """Retorna número real ou extrai do JID."""
        if self.numero_real:
            return self.numero_real
        jid = self.remote_jid or ''
        return jid.split('@')[0]

    @property
    def dados_dict(self):
        """Retorna dados do formulário como dict."""
        if self.dados:
            import json
            try:
                return json.loads(self.dados)
            except Exception:
                pass
        return {}

    @staticmethod
    def posicao_na_fila(remote_jid):
        """Retorna a posição do aluno na fila (1-based) ou 0 se não está."""
        espera = QueueEntry.query.filter_by(status='esperando')\
            .order_by(QueueEntry.criado_em).all()
        for i, entry in enumerate(espera, 1):
            if entry.remote_jid == remote_jid:
                return i
        return 0

    @staticmethod
    def total_esperando():
        """Retorna o total de pessoas esperando."""
        return QueueEntry.query.filter_by(status='esperando').count()

    @staticmethod
    def proximo():
        """Retorna o próximo da fila (mais antigo)."""
        return QueueEntry.query.filter_by(status='esperando')\
            .order_by(QueueEntry.criado_em).first()


class UploadedFile(db.Model):
    """Arquivo enviado pelo coordenador (para reuso e consulta)."""
    __tablename__ = 'uploaded_files'

    id = db.Column(db.Integer, primary_key=True)
    original_name = db.Column(db.String(255), nullable=False)
    saved_name = db.Column(db.String(255), unique=True, nullable=False)
    file_type = db.Column(db.String(20), nullable=True)  # pdf, jpg, png, etc.
    file_size = db.Column(db.Integer, nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


def init_db(app):
    """Inicializa o banco e cria dados de exemplo se estiver vazio."""
    db.init_app(app)
    with app.app_context():
        db.create_all()
        if MenuItem.query.count() == 0:
            _seed_menus()


def _seed_menus():
    """Popula o banco com menus de exemplo."""
    # Menu raiz
    m1 = MenuItem(posicao=1, titulo='Matricula')
    m2 = MenuItem(posicao=2, titulo='Notas e Avaliacoes')
    m3 = MenuItem(posicao=3, titulo='Horarios')
    m4 = MenuItem(posicao=4, titulo='Material de Estudo')
    m5 = MenuItem(posicao=5, titulo='Falar com o Coordenador',
                  resposta='__FILA__')  # Marcador especial para fila
    db.session.add_all([m1, m2, m3, m4, m5])
    db.session.flush()

    # Sub-menus de Matrícula
    db.session.add_all([
        MenuItem(parent_id=m1.id, posicao=1, titulo='Como me matricular?',
                 resposta='Para se matricular, acesse o portal do aluno em *portal.edu.br* e clique em "Nova Matrícula". \n\nDocumentos necessários:\n- RG e CPF\n- Comprovante de residencia\n- Historico escolar\n\nFicou alguma duvida?'),
        MenuItem(parent_id=m1.id, posicao=2, titulo='Prazo de matricula',
                 resposta='O prazo de matricula para o proximo semestre e ate *30 dias antes* do inicio das aulas.\n\nFique atento ao calendario academico!'),
        MenuItem(parent_id=m1.id, posicao=3, titulo='Trancamento',
                 resposta='Para trancar a matricula, procure a *secretaria academica* com seu RA e documento de identidade.\n\nO prazo para trancamento e ate a *4a semana de aula*.'),
    ])

    # Sub-menus de Notas
    db.session.add_all([
        MenuItem(parent_id=m2.id, posicao=1, titulo='Onde ver minhas notas?',
                 resposta='Suas notas estao disponiveis no *portal do aluno* - secao "Boletim".\n\nAs notas sao atualizadas pelo professor em ate *7 dias* apos a avaliacao.'),
        MenuItem(parent_id=m2.id, posicao=2, titulo='Media para aprovacao',
                 resposta='A media minima para aprovacao e *7.0*\n\nCalculo: *(P1 + P2) / 2*\n\nSe ficar abaixo de 7.0, voce vai para a *prova final*. A media com final e *(Media + Final) / 2 >= 5.0*.'),
        MenuItem(parent_id=m2.id, posicao=3, titulo='Revisao de prova',
                 resposta='Para solicitar revisao de prova:\n1. Preencha o formulario na secretaria\n2. Prazo: ate *3 dias uteis* apos divulgacao da nota\n3. O professor tem *5 dias* para responder\n\nLeve uma copia da sua prova!'),
    ])

    # Sub-menus de Horários
    db.session.add_all([
        MenuItem(parent_id=m3.id, posicao=1, titulo='Horario das aulas',
                 resposta='Os horarios das aulas estao no *portal do aluno* - "Grade Horaria".\n\n- Manha: 7h30 - 11h30\n- Tarde: 13h30 - 17h30\n- Noite: 19h00 - 22h30'),
        MenuItem(parent_id=m3.id, posicao=2, titulo='Horario da secretaria',
                 resposta='*Secretaria Academica*\n\n- Segunda a Sexta: 8h - 18h\n- Sabado: 8h - 12h\n- Domingo: Fechado'),
    ])

    # Sub-menus de Material
    db.session.add_all([
        MenuItem(parent_id=m4.id, posicao=1, titulo='Como acessar o material?',
                 resposta='O material de estudo esta disponivel no *Ambiente Virtual de Aprendizagem (AVA)*.\n\nAcesse: *ava.edu.br*\nLogin: seu RA\nSenha: mesma do portal'),
        MenuItem(parent_id=m4.id, posicao=2, titulo='Livros obrigatorios',
                 resposta='Os livros obrigatorios para cada disciplina estao listados no *plano de ensino*, disponivel no AVA.\n\nA biblioteca tambem oferece versoes digitais pelo *Minha Biblioteca*.'),
    ])

    db.session.commit()
    print("[OK] Menus de exemplo criados com sucesso!")
