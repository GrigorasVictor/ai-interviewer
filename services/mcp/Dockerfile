FROM python:3.12-slim

WORKDIR /app

COPY shared /app/shared
COPY services/mcp/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir /app/shared -r /app/requirements.txt

COPY services/mcp/mcp_server /app/mcp_server

ENV PYTHONUNBUFFERED=1
EXPOSE 8001

CMD ["python", "-m", "mcp_server.server"]
