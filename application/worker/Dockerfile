FROM python:3.12-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Create non-root application user
RUN useradd --create-home appuser

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Ensure non-root ownership
RUN chown -R appuser:appuser /app

# Run as non-root user
USER appuser

# Prometheus metrics port
EXPOSE 8001

# Start worker service
CMD ["python", "-m", "app.main"]
