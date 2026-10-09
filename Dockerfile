FROM python:3.13-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

# Serve MCP over Streamable HTTP at :$PORT/mcp (hosting platforms such as
# Smithery set PORT). Run with --transport stdio for a stdio container.
# Binding 0.0.0.0 is needed for a published port to reach the server; limit
# exposure on the host side (-p 127.0.0.1:8000:8000) or set
# EQUINIX_MCP_AUTH_TOKEN to require a bearer token.
ENV EQUINIX_MCP_TRANSPORT=http \
    EQUINIX_MCP_HOST=0.0.0.0 \
    PORT=8000
EXPOSE 8000

CMD ["equinix-docs-mcp-server"]
