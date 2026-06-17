#!/usr/bin/env bash
# paseo-fleet installer — sets up the CLI + Claude Code skill + example registry.
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/oyzh888/paseo-fleet/main"
SKILL_DIR="${HOME}/.claude/skills/paseo-fleet"
BIN_DIR="${HOME}/.local/bin"
REGISTRY="${PASEO_FLEET_REGISTRY:-${HOME}/.paseo/paseo-fleet.json}"

echo "==> installing paseo-fleet"

mkdir -p "$SKILL_DIR" "$BIN_DIR" "$(dirname "$REGISTRY")"

# If running from a clone, copy local files; else fetch from GitHub.
if [[ -f "$(dirname "$0")/paseo-fleet.py" ]]; then
  SRC="$(cd "$(dirname "$0")" && pwd)"
  cp "$SRC/paseo-fleet.py"        "$SKILL_DIR/paseo-fleet.py"
  cp "$SRC/skill/SKILL.md"        "$SKILL_DIR/SKILL.md"
  [[ -f "$REGISTRY" ]] || cp "$SRC/examples/paseo-fleet.example.json" "$REGISTRY"
else
  curl -fsSL "$REPO_RAW/paseo-fleet.py"  -o "$SKILL_DIR/paseo-fleet.py"
  curl -fsSL "$REPO_RAW/skill/SKILL.md"  -o "$SKILL_DIR/SKILL.md"
  [[ -f "$REGISTRY" ]] || curl -fsSL "$REPO_RAW/examples/paseo-fleet.example.json" -o "$REGISTRY"
fi

chmod +x "$SKILL_DIR/paseo-fleet.py"
chmod 600 "$REGISTRY"
ln -sf "$SKILL_DIR/paseo-fleet.py" "$BIN_DIR/paseo-fleet"

echo "==> done."
echo "    CLI:      $BIN_DIR/paseo-fleet   (ensure $BIN_DIR is on PATH)"
echo "    skill:    $SKILL_DIR/SKILL.md"
echo "    registry: $REGISTRY   (edit it: paste each machine's offer URL)"
echo
echo "Get an offer URL on each machine with:  paseo daemon pair"
echo "Then:  paseo-fleet overview"
