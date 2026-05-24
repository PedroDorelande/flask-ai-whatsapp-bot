FROM python:3.11-slim

WORKDIR /app

# Instalar dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Instalar Docker CLI (para o botão de restart nas configurações)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && \
    curl -fsSL https://get.docker.com -o get-docker.sh && \
    sh get-docker.sh && \
    rm get-docker.sh && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copiar código
COPY . .

# Criar pastas necessárias
RUN mkdir -p instance static/uploads

# Variáveis de ambiente
ENV PYTHONUNBUFFERED=1
ENV FLASK_DEBUG=false

# Gunicorn com 1 worker + threads (deduplicação in-memory precisa de processo único)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "120", "--access-logfile", "-", "app:app"]
