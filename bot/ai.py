import os
import json
from openai import OpenAI
from models.database import db, Knowledge
from bot import waha

client = None


def get_client():
    """Lazy-init do cliente OpenAI."""
    global client
    if client is None:
        client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    return client


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
        waha.send_text(chat_id, answer)
        waha.send_text(chat_id, '📋 Envie *menu* para ver as opções.')
    except Exception as e:
        print(f'[ERRO] OpenAI: {e}')
        waha.send_text(chat_id,
            '⚠️ Desculpe, tive um problema ao processar sua pergunta.\n'
            'Envie *menu* para ver as opções ou aguarde o professor.')


def process_pdf_text(text: str, filename: str, instrucao: str = '') -> list[dict]:
    """Usa IA para transformar texto de PDF em pares de FAQ."""
    ai = get_client()
    # Divide em chunks de 4000 caracteres
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    all_faqs = []

    # Monta prompt com instrução do coordenador
    instrucao_extra = ''
    if instrucao:
        instrucao_extra = f'\n\nINSTRUÇÃO DO COORDENADOR: {instrucao}\nFoque as perguntas no tema solicitado acima.'

    for i, chunk in enumerate(chunks):
        try:
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
            # Limpa possíveis artefatos de markdown
            content = content.replace('```json', '').replace('```', '').strip()
            parsed = json.loads(content)

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

        except Exception as e:
            print(f'[ERRO] Chunk {i+1}: {e}')

    db.session.commit()
    return all_faqs

