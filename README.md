# 🤖 TypeBot Educacional — WhatsApp Bot + Dashboard

Bot educacional para WhatsApp com dashboard administrativa. O coordenador/professor gerencia menus, FAQs, fila de atendimento e arquivos pelo painel web. Os alunos interagem pelo WhatsApp com menus numéricos, consulta de posição na fila e respostas automáticas.

## ✨ Features

- 📋 **Bot com menus em árvore** — profundidade ilimitada, navegação por números
- 📱 **QR Code na dashboard** — conecte o WhatsApp sem sair do painel
- 🧠 **IA (gpt-4o-mini)** — extrai FAQs de PDFs automaticamente
- 👥 **Fila de atendimento** — formulário pré-fila, posição em tempo real
- 💬 **Chat AJAX** — responda alunos direto pela dashboard
- 📎 **Arquivos nos menus** — anexe imagens/PDFs a qualquer item
- 🔇 **Controle do professor** — `!parar` muta o bot, `!bot` reativa
- 🟢 **Toggle global** — liga/desliga o bot sem desconectar o WhatsApp
- 🌙 **Dark/Light mode** — dashboard responsiva com tema escuro
- ☰ **Drag-and-drop** — reordene itens do menu arrastando

## 🛠️ Stack

| Componente | Tecnologia |
|---|---|
| Backend | Python 3.11 + Flask |
| Banco de Dados | SQLite (via SQLAlchemy) |
| WhatsApp API | [WAHA](https://waha.devlike.pro/) (Docker) |
| IA | OpenAI gpt-4o-mini |
| Frontend | HTML/CSS/JS (Jinja2) |

## 🚀 Instalação Rápida

### Pré-requisitos
- Python 3.10+
- Docker (para o WAHA)

### 1. Clone e configure

```bash
git clone https://github.com/PedroDorelande/flask-ai-whatsapp-bot.git
cd flask-ai-whatsapp-bot
cp .env.example .env
# Edite o .env com suas chaves reais
```

### 2. Instale dependências

```bash
pip install -r requirements.txt
```

### 3. Suba o WAHA (WhatsApp)

```bash
docker compose up -d
```

### 4. Inicie o bot

```bash
python app.py
```

### 5. Acesse

- **Dashboard:** http://localhost:5000
- **WAHA:** http://localhost:3000

## 📱 Comandos do Professor (no WhatsApp)

| Comando | Ação |
|---|---|
| `!parar` | Muta o bot para aquele aluno |
| `!bot` | Reativa o bot |

## 📁 Estrutura do Projeto

```
├── app.py                 # Rotas da dashboard + API (774 linhas)
├── bot/
│   ├── webhook.py         # Handler do webhook WAHA
│   ├── menu.py            # Lógica de menus em árvore
│   ├── queue.py           # Sistema de fila
│   ├── session.py         # Controle de sessão/mute
│   ├── waha.py            # API client WAHA
│   └── ai.py              # Integração OpenAI
├── models/
│   └── database.py        # 6 tabelas SQLAlchemy
├── templates/             # 9 templates Jinja2
├── static/
│   ├── style.css          # Dark/light theme
│   └── uploads/           # Arquivos enviados
├── docker-compose.yml     # Container WAHA
├── .env.example           # Template de configuração
└── requirements.txt       # Dependências Python
```

## 📄 Licença

MIT
