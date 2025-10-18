#!/bin/bash
set -e

# Switch between public access modes

CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CURRENT_DIR"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
    echo "Usage: $0 {simple|nginx|localhost}"
    echo ""
    echo "Modes:"
    echo "  simple    - Direct 0.0.0.0 binding (quick, less secure)"
    echo "  nginx     - Nginx reverse proxy with rate limiting (more secure)"
    echo "  localhost - Localhost only (most secure, default)"
    echo ""
    exit 1
}

if [ -z "$1" ]; then
    usage
fi

MODE="$1"

echo -e "${BLUE}Switching to $MODE mode...${NC}"
echo ""

# Stop current services
echo -e "${BLUE}Stopping current services...${NC}"
docker compose -f docker-compose.prod.yml down 2>/dev/null || true
docker compose -f docker-compose.public.yml down 2>/dev/null || true
docker compose -f docker-compose.nginx.yml down 2>/dev/null || true

case "$MODE" in
    simple)
        echo -e "${YELLOW}WARNING: This exposes IPFS API and Gateway directly to the internet${NC}"
        echo -e "${YELLOW}Anyone can use your node. Consider using nginx mode for better security.${NC}"
        echo ""
        echo -e "${BLUE}Starting services with simple public access...${NC}"
        docker compose -f docker-compose.public.yml up -d
        echo -e "${GREEN}✓ Services started${NC}"
        echo ""
        echo -e "${GREEN}Access:${NC}"
        echo -e "  API:     http://<your-ip>:5001/api/v0/..."
        echo -e "  Gateway: http://<your-ip>:8080/ipfs/..."
        ;;

    nginx)
        echo -e "${BLUE}Starting services with nginx reverse proxy...${NC}"
        docker compose -f docker-compose.nginx.yml up -d
        echo -e "${GREEN}✓ Services started${NC}"
        echo ""
        echo -e "${GREEN}Access (via nginx with rate limiting):${NC}"
        echo -e "  API:     http://<your-ip>:5001/api/v0/..."
        echo -e "  Gateway: http://<your-ip>:8080/ipfs/..."
        echo ""
        echo -e "${GREEN}Security features enabled:${NC}"
        echo -e "  - Rate limiting (100 req/s API, 50 req/s Gateway)"
        echo -e "  - Connection limits"
        echo -e "  - Security headers"
        echo -e "  - Request logging"
        ;;

    localhost)
        echo -e "${BLUE}Starting services with localhost-only access...${NC}"
        docker compose -f docker-compose.prod.yml up -d
        echo -e "${GREEN}✓ Services started${NC}"
        echo ""
        echo -e "${GREEN}Access (localhost only):${NC}"
        echo -e "  API:     http://localhost:5001/api/v0/..."
        echo -e "  Gateway: http://localhost:8080/ipfs/..."
        echo ""
        echo -e "${YELLOW}Note: Only accessible from the server itself or via SSH tunnel${NC}"
        ;;

    *)
        echo -e "${YELLOW}Invalid mode: $MODE${NC}"
        usage
        ;;
esac

echo ""
echo -e "${BLUE}Checking service status...${NC}"
sleep 3
docker compose -f docker-compose.*.yml ps 2>/dev/null | head -10 || docker ps | grep ipfs
