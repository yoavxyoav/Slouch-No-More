#!/bin/bash
# Assembles dist/Slouch No More.app - a double-clickable macOS bundle.
# The launcher inside bootstraps on first run: installs uv if missing and
# syncs the bundled project into ~/Library/Application Support/SlouchNoMore.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
APP="$DIST/Slouch No More.app"
RES="$APP/Contents/Resources"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RES/app"

# --- bundle the project (source, model, lockfile - no venv, no logs) ---
rsync -a --delete \
  --include='src/***' --include='models/***' --include='assets/***' \
  --include='pyproject.toml' --include='uv.lock' --include='README.md' \
  --exclude='*' "$ROOT/" "$RES/app/"

# --- launcher ---
cat > "$APP/Contents/MacOS/slouch-no-more" <<'LAUNCHER'
#!/bin/bash
set -e
HERE="$(cd "$(dirname "$0")/../Resources/app" && pwd)"
SUPPORT="$HOME/Library/Application Support/SlouchNoMore"
LOG="$SUPPORT/launcher.log"
mkdir -p "$SUPPORT"
exec >>"$LOG" 2>&1
echo "--- launch $(date) ---"

fail() {
  /usr/bin/osascript -e "display dialog \"Slouch No More could not start: $1\" buttons {\"OK\"} with icon caution" >/dev/null 2>&1 || true
  exit 1
}

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found - installing"
  curl -LsSf https://astral.sh/uv/install.sh | sh || fail "could not install uv (is there an internet connection?)"
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || fail "uv installation did not land on PATH"

cd "$HERE"
export UV_PROJECT_ENVIRONMENT="$SUPPORT/venv"
uv sync --frozen --quiet || fail "dependency install failed (see launcher.log)"
exec uv run --frozen posture-guard
LAUNCHER
chmod +x "$APP/Contents/MacOS/slouch-no-more"

# --- Info.plist (LSUIElement: menu bar app, no Dock icon) ---
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Slouch No More</string>
    <key>CFBundleDisplayName</key><string>Slouch No More</string>
    <key>CFBundleIdentifier</key><string>com.yoavxyoav.slouch-no-more</string>
    <key>CFBundleVersion</key><string>0.1.0</string>
    <key>CFBundleShortVersionString</key><string>0.1.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>slouch-no-more</string>
    <key>CFBundleIconFile</key><string>app.icns</string>
    <key>LSUIElement</key><true/>
    <key>LSMinimumSystemVersion</key><string>12.0</string>
    <key>NSCameraUsageDescription</key>
    <string>Slouch No More watches your posture through the camera. Frames never leave your Mac.</string>
</dict>
</plist>
PLIST

# --- icon from the logo ---
if [ -f "$ROOT/assets/logo.png" ]; then
  ICONSET="$DIST/app.iconset"
  rm -rf "$ICONSET"; mkdir -p "$ICONSET"
  for size in 16 32 64 128 256 512; do
    sips -z $size $size "$ROOT/assets/logo.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    sips -z $((size*2)) $((size*2)) "$ROOT/assets/logo.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$RES/app.icns"
  rm -rf "$ICONSET"
fi

# --- zip for distribution ---
cd "$DIST"
rm -f SlouchNoMore.zip
ditto -c -k --keepParent "Slouch No More.app" SlouchNoMore.zip
echo "Built: $APP"
echo "Zip:   $DIST/SlouchNoMore.zip"
