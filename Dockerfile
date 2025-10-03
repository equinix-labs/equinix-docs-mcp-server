FROM python:3.13-slim

WORKDIR /app
COPY . .
RUN pip install -e .

CMD ["python", "-m", "src.equinix_docs_mcp_server.main"]
