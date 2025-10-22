FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for DR operations
# - docker-cli: For docker exec commands to ipfs-node
# - curl/jq: For direct IPFS API calls
# - awscli: For S3 backup uploads
# - unzip: Required for AWS CLI installation
RUN apt-get update && apt-get install -y \
    curl \
    jq \
    docker.io \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install AWS CLI v2
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" \
    && unzip awscliv2.zip \
    && ./aws/install \
    && rm -rf awscliv2.zip aws

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3000"]
