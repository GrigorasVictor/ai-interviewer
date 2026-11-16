FROM python:3.12-slim

WORKDIR /app

# Install the shared contracts package first, then service dependencies.
COPY shared /app/shared
COPY services/agent/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir /app/shared -r /app/requirements.txt

# Copy the ADK agent package into /app/agents/interviewer_app so `adk web /app/agents` discovers it.
COPY services/agent/interviewer_app /app/agents/interviewer_app

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["adk", "web", "--host", "0.0.0.0", "--port", "8000", "/app/agents"]
