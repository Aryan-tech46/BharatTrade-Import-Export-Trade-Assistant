# Use official lightweight Python 3.11 image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# Set working directory
WORKDIR /app

# Install system dependencies (curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file first for Docker layer caching
COPY requirements.txt .

# Install CPU-only PyTorch first (reduces image by ~1.5GB) then install remaining packages
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Expose port
EXPOSE 8080

# Run multi-worker multi-threaded Gunicorn WSGI server
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "3", "--threads", "4", "--timeout", "120", "app:app"]
