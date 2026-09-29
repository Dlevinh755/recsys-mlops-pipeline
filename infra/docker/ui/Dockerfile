FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements/ui.txt /tmp/ui.txt
RUN pip install --no-cache-dir -r /tmp/ui.txt

COPY ui/app.py /app/app.py
COPY ui/.streamlit /app/.streamlit

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
