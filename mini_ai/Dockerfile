FROM python:3.12-slim
WORKDIR /app
COPY mini_ai/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY mini_ai /app/mini_ai
COPY services/__init__.py services/supabase_admin.py /app/services/
RUN useradd --system --uid 10001 --create-home nexxus
USER nexxus
CMD ["sh", "-c", "exec uvicorn mini_ai.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]
