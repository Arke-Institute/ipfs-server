FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for DR operations
# - docker-cli: For docker exec commands to ipfs-node
# - curl/jq: For direct IPFS API calls
RUN apt-get update && apt-get install -y \
    curl \
    jq \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3000"]
