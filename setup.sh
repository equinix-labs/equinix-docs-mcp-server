#!/bin/bash
# Setup script for Equinix MCP Server development environment

set -e

echo "🚀 Setting up Equinix MCP Server development environment..."

# Check Python version
python_version=$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1,2)
min_version="3.8"

if [ "$(printf '%s\n' "$min_version" "$python_version" | sort -V | head -n1)" != "$min_version" ]; then
    echo "❌ Python $min_version or higher is required. Found: $python_version"
    exit 1
fi

echo "✅ Python version check passed: $python_version"

# Install in development mode
echo "📦 Installing package in development mode..."
pip install -e .[dev]

# Verify installation
echo "🔍 Verifying installation..."
if ! command -v equinix-mcp-server &> /dev/null; then
    echo "❌ Installation failed - command not found"
    exit 1
fi

echo "✅ Installation verified"

# Run basic tests
echo "🧪 Running basic tests..."
python -m pytest tests/test_config.py -v

# Check configuration
echo "📋 Validating configuration..."
python -c "from equinix_mcp_server.config import Config; Config.load('config/apis.yaml'); print('✅ Configuration is valid')"

# Check CLI help
echo "🔧 Testing CLI..."
equinix-mcp-server --help > /dev/null

echo ""
echo "🎉 Setup completed successfully!"
echo ""
echo "Next steps:"
echo "1. Set your Equinix API credentials:"
echo "   export EQUINIX_CLIENT_ID='your_client_id'"
echo "   export EQUINIX_CLIENT_SECRET='your_client_secret'"
echo "   export EQUINIX_METAL_TOKEN='your_metal_token'  # Optional"
echo ""
echo "2. Start the server:"
echo "   equinix-mcp-server"
echo ""
echo "3. Or update API specs:"
echo "   equinix-mcp-server --update-specs"
echo ""
echo "For more information, see README.md"