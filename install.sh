#!/usr/bin/env bash
# One-command setup for cashew-mcp.
# Run from inside the cloned repo: bash install.sh

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> cashew-mcp installer"
echo "    Repo: $REPO_DIR"
echo ""

# 1. Check uv
if ! command -v uv &>/dev/null; then
  echo "ERROR: uv is not installed."
  echo "Install it with:  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

# 2. Check Claude Code CLI
if ! command -v claude &>/dev/null; then
  echo "ERROR: Claude Code CLI ('claude') not found in PATH."
  echo "Install Claude Code from https://claude.ai/code"
  exit 1
fi

# 3. Check the database exists
DB="${CASHEW_DB:-$HOME/Downloads/cashew.sqlite}"
if [ ! -f "$DB" ]; then
  echo "WARNING: Database not found at $DB"
  echo "  Export it from the Cashew app:"
  echo "  Settings → Export Data → Export as SQLite database"
  echo "  Then re-run this script, or set CASHEW_DB=/your/path before running."
  echo ""
  read -r -p "Continue anyway? [y/N] " ans
  if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
    exit 1
  fi
fi

# 4. Register MCP server
echo "==> Registering MCP server with Claude Code..."
claude mcp add cashew -- uv run --project "$REPO_DIR" cashew-mcp
echo "    Done."

# 5. Install slash command
echo "==> Installing /cashew slash command..."
mkdir -p ~/.claude/commands
cp "$REPO_DIR/.claude/commands/cashew.md" ~/.claude/commands/cashew.md
echo "    Installed to ~/.claude/commands/cashew.md"

echo ""
echo "All done! Restart Claude Code, then type /cashew to try it."
