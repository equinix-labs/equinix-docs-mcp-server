# Using the OpenAI MCP-Compatible Search and Fetch Tools

This document demonstrates how to use the new `search` and `fetch` tools that comply with OpenAI's MCP specification for ChatGPT Connectors and deep research integration.

## Overview

The Equinix Docs MCP Server now provides two OpenAI MCP-compatible tools:

- **`search`**: Full-text search of Equinix documentation
- **`fetch`**: Retrieve full markdown content from documentation URLs

These tools work together to enable deep research capabilities in ChatGPT and other AI systems.

## Tool: `search`

### Description
Search Equinix documentation using full-text search. Returns URLs that can be fetched with the 'fetch' tool to retrieve full content.

### Parameters
- `query` (required): The search query string
- `limit` (optional): Maximum number of results to return (default: 8)

### Example Usage

```python
# Search for Metal API documentation
results = await search(query="metal api authentication", limit=5)
```

### Example Output

```markdown
# Search Results for 'metal api authentication'

**Metal API Authentication**
  https://docs.equinix.com/metal/api/authentication

**Getting Started with Metal API**
  https://docs.equinix.com/metal/api/getting-started

**API Tokens and Keys**
  https://docs.equinix.com/metal/security/api-keys

Showing top 3 results. Refine your query for more specific results.
```

## Tool: `fetch`

### Description
Fetch the full markdown content of an Equinix documentation page by URL. Use URLs returned from the 'search' tool.

### Parameters
- `url` (required): The URL of the documentation page (e.g., from search results)

### URL Format Support

The `fetch` tool automatically handles various URL formats:

1. **Full URLs without .md extension**
   ```python
   fetch(url="https://docs.equinix.com/metal/getting-started")
   # Fetches: https://docs.equinix.com/metal/getting-started.md
   ```

2. **Full URLs with .md extension**
   ```python
   fetch(url="https://docs.equinix.com/fabric/overview.md")
   # Fetches: https://docs.equinix.com/fabric/overview.md (no change)
   ```

3. **Relative URLs**
   ```python
   fetch(url="metal/api-reference")
   # Fetches: https://docs.equinix.com/metal/api-reference.md
   ```

4. **URLs with trailing slashes**
   ```python
   fetch(url="https://docs.equinix.com/metal/getting-started/")
   # Fetches: https://docs.equinix.com/metal/getting-started.md
   ```

### Example Usage

```python
# Fetch a specific documentation page
content = await fetch(url="https://docs.equinix.com/metal/getting-started")
```

### Example Output

```markdown
# Getting Started with Equinix Metal

Equinix Metal provides on-demand, bare metal infrastructure...

## Prerequisites

Before you begin, you'll need:
- An Equinix Metal account
- API credentials...

[Full markdown content of the page]
```

## Integrated Workflow: Search then Fetch

The typical workflow combines both tools:

```python
# Step 1: Search for relevant documentation
search_results = await search(query="fabric virtual connections", limit=5)

# Step 2: Extract URLs from search results
# (URLs are displayed in the search results)

# Step 3: Fetch full content for relevant pages
content = await fetch(url="https://docs.equinix.com/fabric/connections/virtual")
```

## Error Handling

The `fetch` tool provides informative error messages:

### HTTP Errors (404, 500, etc.)
```
Error fetching document: HTTP 404 - https://docs.equinix.com/nonexistent.md

The document may not be available in markdown format.
```

### Network Errors
```
Error fetching document: Connection timeout

URL: https://docs.equinix.com/metal/getting-started.md
```

### Invalid URLs
```
Error fetching document: Invalid URL format

URL: [invalid-url]
```

## Benefits of OpenAI MCP Compatibility

1. **ChatGPT Connectors**: Works seamlessly with ChatGPT's connector framework
2. **Deep Research**: Enables AI systems to perform comprehensive documentation research
3. **Standard Interface**: Uses consistent naming and signatures across MCP servers
4. **Content Access**: Provides full markdown content instead of just URLs

## Backward Compatibility

The following tools remain available for existing workflows:

- **`list_docs`**: List and filter documentation by keywords
- **`find_docs`**: Find documentation by filename/title matching

These tools complement the new `search` and `fetch` tools but don't follow the OpenAI MCP specification.

## Best Practices

1. **Start with search**: Use `search` to find relevant documentation
2. **Refine queries**: Use specific terms to get better results
3. **Limit results**: Adjust the `limit` parameter to control the number of results
4. **Fetch selectively**: Only fetch full content for the most relevant pages
5. **Handle errors**: Check for error messages in fetch responses

## Example: Complete Research Flow

```python
# 1. Search for Metal API documentation
search_results = await search(
    query="metal api create device server",
    limit=5
)

# Results show:
# - Metal API Reference
# - Device Creation Guide
# - Server Provisioning
# - API Authentication
# - Best Practices

# 2. Fetch the most relevant document
device_guide = await fetch(
    url="https://docs.equinix.com/metal/api/devices/create"
)

# 3. Process the markdown content
# The content is now available for analysis, summarization, or Q&A

# 4. Fetch related documentation if needed
auth_guide = await fetch(
    url="https://docs.equinix.com/metal/api/authentication"
)
```

## Integration with ChatGPT

When using with ChatGPT's connector framework or deep research:

1. ChatGPT automatically calls `search` based on user questions
2. It analyzes search results to find relevant pages
3. It uses `fetch` to retrieve full content
4. It synthesizes information from multiple pages
5. It provides comprehensive answers with source citations

This enables ChatGPT to provide accurate, up-to-date information directly from Equinix documentation.
