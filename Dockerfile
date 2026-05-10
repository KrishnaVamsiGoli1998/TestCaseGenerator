# Python + Node.js image (needed for pytest and Jest)
FROM python:3.11-slim

# Install Node.js 20 (for Jest via npx)
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Jest globally so npx resolves it without network calls
RUN npm install -g jest

# Copy application code
COPY . .

# Create runtime directories
RUN mkdir -p uploads generated_tests temp

EXPOSE 8080

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
