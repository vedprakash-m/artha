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
    curl -fsSL --retry 3 -o "$dest" "$url"
  elif command -v wget &>/dev/null; then
    wget -q --tries=3 -O "$dest" "$url"
  else
    echo "  ERROR: neither curl nor wget found. Install one and retry." >&2
    exit 1
  fi
  echo "  ✅ $name"
}

# Space Grotesk — Regular (400) and Bold (700)
# Source: https://fonts.gstatic.com/s/spacegrotesk/
SPACE_GROTESK_BASE="https://fonts.gstatic.com/s/spacegrotesk/v16"
download_font "SpaceGrotesk-Regular.woff2" \
  "${SPACE_GROTESK_BASE}/V8mDoKKcgnfAPiUxTogzHn1JpQ.woff2"
download_font "SpaceGrotesk-Bold.woff2" \
  "${SPACE_GROTESK_BASE}/V8mDoKKcgnfAPiUxTogzHn1JpQ.woff2"

# DM Sans — Regular (400), Medium (500), Bold (700)
# Source: https://fonts.gstatic.com/s/dmsans/
DMSANS_BASE="https://fonts.gstatic.com/s/dmsans/v15"
download_font "DMSans-Regular.woff2" \
  "${DMSANS_BASE}/rP2Fp2ywxg089UriCZa4ET-DQltMnQ.woff2"
download_font "DMSans-Medium.woff2" \
  "${DMSANS_BASE}/rP2Hp2ywxg089UriCZa4ET-DMpoez_Q.woff2"
download_font "DMSans-Bold.woff2" \
  "${DMSANS_BASE}/rP2Hp2ywxg089UriCZa4ET-DMpoez_Q.woff2"

echo ""
echo "Font install complete. Files in $FONTS_DIR:"
ls -lh "$FONTS_DIR"/*.woff2 2>/dev/null || echo "  (no woff2 files — check network access)"
echo ""
echo "Run 'career pdf <NNN>' to generate a PDF with the installed fonts."
