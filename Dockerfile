FROM python:3.13-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

# Serve MCP over Streamable HTTP at :$PORT/mcp (hosting platforms such as
# Smithery set PORT). Run with --transport stdio for a stdio container.
ENV EQUINIX_MCP_TRANSPORT=http \
    EQUINIX_MCP_HOST=0.0.0.0 \
    PORT=8000
EXPOSE 8000

CMD ["equinix-docs-mcp-server"]
