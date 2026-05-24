import os
import sys
import json
from openai import OpenAI
from models.database import db, Knowledge
from bot import waha

client = None


def get_client():
    """Lazy-init do cliente OpenAI. Recria se a key mudar."""
    global client
    key = os.getenv('OPENAI_API_KEY', '')
    if client is None or getattr(client, '_custom_key', '') != key:
        client = OpenAI(api_key=key)
        client._custom_key = key  # Track which key we initialized with
    return client


def _log_ai(msg: str):
    """Log para debug de chamadas OpenAI."""
    line = f'[AI] {msg}'
    print(line, file=sys.stderr, flush=True)


def test_openai_key() -> dict:
    """Testa se a API key da OpenAI está funcionando.
    Retorna dict com 'ok', 'message', e opcionalmente 'error'.
    """
    key = os.getenv('OPENAI_API_KEY', '')
    if not key:
        return {'ok': False, 'message': 'OPENAI_API_KEY não está definida no .env', 'error': 'missing_key'}

    masked = key[:8] + '...' + key[-4:]
    _log_ai(f'Testing key: {masked}')

    try:
        ai = get_client()
        response = ai.chat.completions.create(
            model='gpt-4o-mini',
            temperature=0,
            max_tokens=10,
            messages=[
                {'role': 'user', 'content': 'Responda apenas: OK'}
            ]
        )
        answer = response.choices[0].message.content
        _log_ai(f'Test OK: {answer}')
        return {'ok': True, 'message': f'API Key funcionando! Resposta: {answer}', 'key_masked': masked}
    except Exception as e:
        error_msg = str(e)
        _log_ai(f'Test FAILED: {error_msg}')
        return {'ok': False, 'message': f'Erro: {error_msg}', 'error': type(e).__name__, 'key_masked': masked}


def search_knowledge(question: str) -> str | None:
    """Busca resposta na base de conhecimento (FAQs aprovadas)."""
    faqs = Knowledge.query.filter_by(status='Aprovado').all()
    if not faqs:
        return None

    # Busca simples por palavras-chave
    question_lower = question.lower().strip()
    best_match = None
    best_score = 0

    for faq in faqs:
        # Score baseado em palavras em comum
        faq_words = set(faq.pergunta.lower().split())
        question_words = set(question_lower.split())
        common = faq_words & question_words
        score = len(common) / max(len(faq_words), 1)

        if score > best_score and score > 0.3:
            best_score = score
            best_match = faq

    if best_match:
        return best_match.resposta
    return None


def ai_fallback(chat_id: str, question: str):
    """Quando nenhum menu ou FAQ atende, usa IA para responder."""
    # Primeiro tenta buscar no conhecimento local
    local_answer = search_knowledge(question)
    if local_answer:
        waha.send_text(chat_id, local_answer)
        waha.send_text(chat_id, '📋 Envie *menu* para ver as opções.')
        return

    # Se não encontrou, usa IA com contexto das FAQs
    faqs = Knowledge.query.filter_by(status='Aprovado').limit(20).all()
    contexto = '\n'.join([f'P: {f.pergunta}\nR: {f.resposta}' for f in faqs])

    if not contexto:
        waha.send_text(chat_id,
            '🤔 Não encontrei essa informação no material.\n\n'
            'Vou encaminhar sua dúvida para o professor. 📚\n\n'
            'Enquanto isso, envie *menu* para ver as opções disponíveis.')
        return

    try:
        ai = get_client()
        _log_ai(f'AI fallback for {chat_id}: "{question[:60]}"')
        response = ai.chat.completions.create(
            model='gpt-4o-mini',
            temperature=0.3,
            max_tokens=400,
            messages=[
                {
                    'role': 'system',
                    'content': (
                        'Você é um assistente educacional. Responda APENAS com informações '
                        'do material de referência. Se não encontrar, diga que vai encaminhar '
                        'para o professor. Use formatação WhatsApp (*negrito*, _itálico_). '
                        'Máximo 2 emojis. Seja objetivo.'
                    )
                },
                {
                    'role': 'user',
                    'content': f'Material:\n{contexto}\n\nPergunta: {question}'
                }
            ]
        )
        answer = response.choices[0].message.content
        _log_ai(f'AI response OK: {answer[:80]}...')
        waha.send_text(chat_id, answer)
        waha.send_text(chat_id, '📋 Envie *menu* para ver as opções.')
    except Exception as e:
        _log_ai(f'AI fallback ERROR: {type(e).__name__}: {e}')
        waha.send_text(chat_id,
            '⚠️ Desculpe, tive um problema ao processar sua pergunta.\n'
            'Envie *menu* para ver as opções ou aguarde o professor.')


def process_pdf_text(text: str, filename: str, instrucao: str = '') -> list[dict]:
    """Usa IA para transformar texto de PDF em pares de FAQ."""
    key = os.getenv('OPENAI_API_KEY', '')
    if not key:
        _log_ai('ERROR: OPENAI_API_KEY não está definida!')
        raise ValueError('OPENAI_API_KEY não está definida no .env. Configure a chave antes de processar PDFs.')

    masked = key[:8] + '...' + key[-4:]
    _log_ai(f'Processing PDF "{filename}" with key {masked}')

    ai = get_client()
    # Divide em chunks de 4000 caracteres
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    all_faqs = []

    _log_ai(f'PDF has {len(text)} chars, split into {len(chunks)} chunks')

    # Monta prompt com instrução do coordenador
    instrucao_extra = ''
    if instrucao:
        instrucao_extra = f'\n\nINSTRUÇÃO DO COORDENADOR: {instrucao}\nFoque as perguntas no tema solicitado acima.'

    for i, chunk in enumerate(chunks):
        try:
            _log_ai(f'Processing chunk {i+1}/{len(chunks)} ({len(chunk)} chars)...')
            response = ai.chat.completions.create(
                model='gpt-4o-mini',
                temperature=0.2,
                max_tokens=3000,
                messages=[
                    {
                        'role': 'system',
                        'content': (
                            'Transforme o texto em pares de Pergunta e Resposta (FAQ). '
                            'Retorne APENAS JSON válido:\n'
                            '{"faqs": [{"pergunta": "...", "resposta": "...", "categoria": "..."}]}\n'
                            'Perguntas devem soar naturais, como um aluno realmente perguntaria. '
                            'Extraia TODAS as informações possíveis — gere entre 5 e 15 FAQs por trecho. '
                            'Não invente informações. Categorize cada FAQ em uma categoria temática '
                            '(ex: Matrícula, Avaliação, Horários, Regulamento, etc).'
                            + instrucao_extra
                        )
                    },
                    {
                        'role': 'user',
                        'content': f'Texto do PDF "{filename}" (parte {i+1}/{len(chunks)}):\n\n{chunk}'
                    }
                ]
            )

            content = response.choices[0].message.content
            _log_ai(f'Chunk {i+1} response received ({len(content)} chars)')
            # Limpa possíveis artefatos de markdown
            content = content.replace('```json', '').replace('```', '').strip()
            parsed = json.loads(content)

            faqs_count = 0
            for faq in parsed.get('faqs', []):
                knowledge = Knowledge(
                    pergunta=faq['pergunta'],
                    resposta=faq['resposta'],
                    categoria=faq.get('categoria', 'Geral'),
                    origem=filename,
                    status='Pendente'
                )
                db.session.add(knowledge)
                all_faqs.append(faq)
                faqs_count += 1

            _log_ai(f'Chunk {i+1}: {faqs_count} FAQs extracted')

        except json.JSONDecodeError as e:
            _log_ai(f'ERROR chunk {i+1}: JSON parse error: {e}')
            _log_ai(f'Raw content: {content[:200]}...')
        except Exception as e:
            _log_ai(f'ERROR chunk {i+1}: {type(e).__name__}: {e}')

    db.session.commit()
    _log_ai(f'DONE: {len(all_faqs)} total FAQs from "{filename}"')
    return all_faqs
