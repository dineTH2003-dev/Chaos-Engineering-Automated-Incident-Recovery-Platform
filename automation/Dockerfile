FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi==0.115.12 uvicorn[standard]==0.34.3 pydantic==2.14.1

COPY webhook_receiver.py .

RUN mkdir -p /reports/incidents && useradd --create-home appuser && chown -R appuser:appuser /app /reports
USER appuser

EXPOSE 5001

HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5001/health')"

CMD ["uvicorn", "webhook_receiver:app", "--host", "0.0.0.0", "--port", "5001"]
