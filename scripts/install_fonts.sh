#!/usr/bin/env bash
# scripts/install_fonts.sh — Download self-hosted CV fonts for Playwright PDF generation
# Ref: specs/career-ops.md FR-CS-3, §9.1; fonts/README.md
#
# Downloads Space Grotesk and DM Sans woff2 files from Google Fonts CDN.
# These are stored in fonts/ and referenced by templates/cv-template.html.
# No internet access required during PDF rendering (Playwright sandboxed).

set -euo pipefail

FONTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/fonts"
mkdir -p "$FONTS_DIR"

echo "Installing CV fonts to $FONTS_DIR ..."

# Google Fonts CSS2 API — fetches woff2 subset URLs
# We hardcode known stable Google Fonts CDN URLs for offline reproducibility.
# If these become stale, update the URLs from: https://fonts.google.com/download

# Google Fonts now ships DM Sans + Space Grotesk as variable fonts — a single
# latin-subset woff2 covers all weights. We still save them under
# Regular/Medium/Bold filenames so the template's @font-face rules resolve
# without refactor; the font engine picks the right weight axis at render.
# Refresh these URLs via: curl -A "<chrome-ua>" fonts.googleapis.com/css2?...

UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
DM_LATIN="https://fonts.gstatic.com/s/dmsans/v17/rP2Yp2ywxg089UriI5-g4vlH9VoD8Cmcqbu0-K6z9mXg.woff2"
SG_LATIN="https://fonts.gstatic.com/s/spacegrotesk/v22/V8mDoQDjQSkFtoMM3T6r8E7mPbF4C_k3HqU.woff2"

download_font() {
  local name="$1"
  local url="$2"
  local dest="$FONTS_DIR/$name"
  if [[ -f "$dest" ]]; then
    echo "  [skip] $name already exists"
    return
  fi
  echo "  Downloading $name ..."
  if command -v curl &>/dev/null; then
    curl -fsSL --retry 3 -A "$UA" -o "$dest" "$url"
  elif command -v wget &>/dev/null; then
    wget -q --tries=3 --user-agent="$UA" -O "$dest" "$url"
  else
    echo "  ERROR: neither curl nor wget found. Install one and retry." >&2
    exit 1
  fi
  echo "  ✅ $name"
}

download_font "SpaceGrotesk-Regular.woff2" "$SG_LATIN"
download_font "SpaceGrotesk-Bold.woff2"    "$SG_LATIN"
download_font "DMSans-Regular.woff2"       "$DM_LATIN"
download_font "DMSans-Medium.woff2"        "$DM_LATIN"
download_font "DMSans-Bold.woff2"          "$DM_LATIN"

echo ""
echo "Font install complete. Files in $FONTS_DIR:"
ls -lh "$FONTS_DIR"/*.woff2 2>/dev/null || echo "  (no woff2 files — check network access)"
echo ""
echo "Run 'career pdf <NNN>' to generate a PDF with the installed fonts."
