FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

ARG REQUIREMENTS_FILE=requirements-prod.txt

COPY requirements.txt requirements-prod.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && cp "${REQUIREMENTS_FILE}" /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY . .

RUN mkdir -p /app/data /app/knowledge

EXPOSE 5000

CMD ["sh", "-lc", "exec gunicorn app:app --bind 0.0.0.0:${PORT:-5000} --workers 2 --threads 4 --timeout 120 --access-logfile - --error-logfile -"]
