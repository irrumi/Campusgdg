# Optional FastAPI/UI hosting. Agent Engine uses source deployment instead.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
# Explicit copies omit .env, ADC, .git, data and worker directories.
COPY pyproject.toml /app/pyproject.toml
COPY campuspulse/*.py /app/campuspulse/
COPY campuspulse/static/ /app/campuspulse/static/
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 campuspulse \
    && mkdir -p /app/data && chown campuspulse:campuspulse /app/data
USER campuspulse
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "campuspulse.api:app", "--host", "0.0.0.0", "--port", "8080"]
