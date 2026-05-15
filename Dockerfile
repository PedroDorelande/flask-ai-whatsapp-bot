FROM python:3.11-slim

WORKDIR /app

# Instalar dependências primeiro (cache de build)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copiar código
COPY . .

# Criar pastas necessárias
RUN mkdir -p instance static/uploads

# Variáveis de ambiente
ENV PYTHONUNBUFFERED=1
ENV FLASK_DEBUG=false

# Gunicorn com 2 workers
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "2", "--timeout", "120", "--access-logfile", "-", "app:app"]
