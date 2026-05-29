#!/bin/bash
# =====================================================
# Script para configurar SSL com Let's Encrypt
# Execute UMA VEZ após configurar os DNS
# =====================================================

set -e

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

DOMAINS="bot.3wstech.com.br waha.3wstech.com.br"
PRIMARY_DOMAIN="bot.3wstech.com.br"
EMAIL="${1:-}"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  SSL Setup - Let's Encrypt / Certbot   ${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Verifica se email foi passado
if [ -z "$EMAIL" ]; then
    echo -e "${YELLOW}Uso: ./init-ssl.sh seuemail@exemplo.com${NC}"
    echo ""
    echo "O email é necessário para o Let's Encrypt enviar avisos de renovação."
    echo ""
    read -p "Digite seu email: " EMAIL
    if [ -z "$EMAIL" ]; then
        echo -e "${RED}Email é obrigatório! Abortando.${NC}"
        exit 1
    fi
fi

echo -e "${GREEN}[1/5]${NC} Verificando DNS dos domínios..."
echo ""
for domain in $DOMAINS; do
    if host "$domain" > /dev/null 2>&1; then
        IP=$(host "$domain" | head -1 | awk '{print $NF}')
        echo -e "  ✅ ${domain} → ${IP}"
    else
        echo -e "  ${RED}❌ ${domain} — DNS NÃO ENCONTRADO!${NC}"
        echo -e "  ${YELLOW}   Configure o registro A no painel do seu domínio primeiro.${NC}"
        echo ""
        echo -e "${RED}Abortando. Configure os DNS e tente novamente.${NC}"
        exit 1
    fi
done
echo ""

echo -e "${GREEN}[2/5]${NC} Criando configuração temporária do nginx (HTTP apenas)..."

# Salva nginx.conf atual e cria um temporário sem SSL
cp nginx.conf nginx.conf.ssl.bak

cat > nginx.conf.tmp << 'NGINX_TMP'
server {
    listen 80 default_server;
    server_name _;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location /static/ {
        alias /usr/share/nginx/static/;
    }

    location /waha/ {
        proxy_pass http://waha:3000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location / {
        proxy_pass http://app:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 50M;
    }
}
NGINX_TMP

# Usa config temporária
cp nginx.conf.tmp nginx.conf
echo "  ✅ Configuração HTTP temporária criada"
echo ""

echo -e "${GREEN}[3/5]${NC} Reiniciando nginx com configuração HTTP..."
docker compose up -d nginx
sleep 3
echo "  ✅ Nginx rodando"
echo ""

echo -e "${GREEN}[4/5]${NC} Obtendo certificados SSL do Let's Encrypt..."
echo ""

docker compose run --rm certbot certonly \
    --webroot \
    -w /var/www/certbot \
    -d "$PRIMARY_DOMAIN" \
    -d "waha.3wstech.com.br" \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    --force-renewal

echo ""
echo "  ✅ Certificados obtidos!"
echo ""

echo -e "${GREEN}[5/5]${NC} Restaurando nginx com configuração SSL completa..."

# Restaura a config SSL
cp nginx.conf.ssl.bak nginx.conf
rm -f nginx.conf.tmp nginx.conf.ssl.bak

# Reinicia nginx com SSL
docker compose restart nginx
sleep 2

echo "  ✅ Nginx reiniciado com SSL!"
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}  ✅ SSL CONFIGURADO COM SUCESSO!       ${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "  🤖 Bot:  ${GREEN}https://bot.3wstech.com.br${NC}"
echo -e "  📱 WAHA: ${GREEN}https://waha.3wstech.com.br/dashboard${NC}"
echo ""
echo -e "  ${YELLOW}Os certificados serão renovados automaticamente.${NC}"
echo ""
