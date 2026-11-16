FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt
ENV PYTHONPATH=/app
COPY backend backend
COPY frontend frontend
COPY infra/seed_ids.json infra/seed_ids.json

COPY infra/postgres infra/postgres

COPY .streamlit .streamlit
