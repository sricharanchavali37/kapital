FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Expose default port (Railway overrides this with $PORT)
EXPOSE 8000

# Use $PORT if Railway sets it, otherwise fall back to 8000
# --reload removed — that's for development only, not production
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]