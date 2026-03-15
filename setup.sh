#!/usr/bin/env bash
# Artha Turbo Setup — from zero to first briefing in 60 seconds
#
# Usage:
#   bash setup.sh
#
# What this does:
#   1. Checks prerequisites (Python 3.11+, Git, age)
#   2. Creates a virtual environment at ~/.artha-venvs/.venv
#   3. Installs Python dependencies
#   4. Copies the profile template (if not already present)
#   5. Activates the PII safety git hook
#   6. Runs a demo briefing so you see Artha's output immediately
#   7. Prints a boxed next-steps mission card

set -euo pipefail

# ── Colors (only when stdout is a terminal) ──────────────────────────────────
if [ -t 1 ]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
  BLUE='\033[0;34m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; BOLD=''; DIM=''; RESET=''
fi

pass()  { echo -e "  ${GREEN}✓${RESET}  $1"; }
fail()  { echo -e "  ${RED}✗${RESET}  $1" >&2; }
warn()  { echo -e "  ${YELLOW}!${RESET}  $1"; }
info()  { echo -e "  ${BLUE}→${RESET}  $1"; }
blank() { echo ""; }

# ── Header ────────────────────────────────────────────────────────────────────
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}   A R T H A  —  Personal Intelligence OS                        ${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
blank

# ── [1/4] Prerequisite checks ─────────────────────────────────────────────────
echo -e "${BOLD}[1/4] Checking prerequisites...${RESET}"

ERRORS=0

# Python 3.11+
if command -v python3 &>/dev/null; then
  PYMAJ=$(python3 -c 'import sys; print(sys.version_info.major)')
  PYMIN=$(python3 -c 'import sys; print(sys.version_info.minor)')
  PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')
  if [ "$PYMAJ" -ge 3 ] && [ "$PYMIN" -ge 11 ]; then
    pass "Python ${PYVER}"
  else
    fail "Python ${PYVER} found — Artha requires Python 3.11+."
    fail "Install a newer version from https://www.python.org/downloads/"
    ERRORS=$((ERRORS + 1))
  fi
else
  fail "Python 3 not found — install from https://www.python.org/downloads/"
  ERRORS=$((ERRORS + 1))
fi

# Git
if command -v git &>/dev/null; then
  GITVER=$(git --version | awk '{print $3}')
  pass "Git ${GITVER}"
else
  fail "Git not found — install from https://git-scm.com/"
  ERRORS=$((ERRORS + 1))
fi

# age — optional, warn but don't block
if command -v age &>/dev/null; then
  pass "age (encryption available)"
else
  warn "age not found — encryption will be unavailable until installed"
  warn "Install later: brew install age  (macOS) | sudo apt install age  (Debian/Ubuntu)"
fi

if [ "$ERRORS" -gt 0 ]; then
  blank
  echo -e "${RED}${BOLD}Setup cannot continue — fix the issues above and re-run setup.sh${RESET}"
  exit 1
fi

blank

# ── [2/4] Virtual environment ─────────────────────────────────────────────────
echo -e "${BOLD}[2/4] Setting up virtual environment...${RESET}"
VENV_DIR="$HOME/.artha-venvs/.venv"

if [ -d "$VENV_DIR" ]; then
  pass "Virtual environment already exists at ~/.artha-venvs/.venv"
else
  info "Creating virtual environment at ~/.artha-venvs/.venv ..."
  python3 -m venv "$VENV_DIR"
  pass "Virtual environment created"
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"
blank

# ── [3/4] Dependencies ────────────────────────────────────────────────────────
echo -e "${BOLD}[3/4] Installing dependencies...${RESET}"
info "This takes ~30 seconds the first time (cached on subsequent runs)"
pip install -q --disable-pip-version-check -r scripts/requirements.txt
pass "Dependencies installed"

# PII safety git hook
if git rev-parse --git-dir &>/dev/null 2>&1; then
  git config core.hooksPath .githooks 2>/dev/null && pass "PII safety hook activated" || true
fi

blank

# ── [4/4] Demo briefing ────────────────────────────────────────────────────────
echo -e "${BOLD}[4/4] Running demo briefing...${RESET}"
echo -e "${DIM}(Fictional data — no real accounts connected)${RESET}"
blank
python scripts/demo_catchup.py
blank

# ── Interactive wizard or next-steps ─────────────────────────────────────────
if [ -t 0 ] && [ -t 1 ]; then
  # Running in an interactive terminal — offer the guided wizard
  echo -e "${BOLD}  Ready to personalize Artha for YOUR life?${RESET}"
  read -rp "  Run the 2-minute setup wizard now? [yes/no]: " WIZARD_ANSWER
  if [[ "$WIZARD_ANSWER" =~ ^[Yy] ]]; then
    blank
    python artha.py --setup
  else
    blank
    # Copy minimal starter profile for manual editing
    if [ ! -f "config/user_profile.yaml" ]; then
      if [ -f "config/user_profile.starter.yaml" ]; then
        cp config/user_profile.starter.yaml config/user_profile.yaml
        pass "Minimal profile template copied → config/user_profile.yaml"
      else
        cp config/user_profile.example.yaml config/user_profile.yaml
        pass "Profile template copied → config/user_profile.yaml"
      fi
    fi
    echo -e "${BOLD}┌─────────────────────────────────────────────────────────────────┐${RESET}"
    echo -e "${BOLD}│  3 steps to your first real briefing:                           │${RESET}"
    echo -e "${BOLD}│                                                                 │${RESET}"
    echo -e "${BOLD}│  1.  Edit config/user_profile.yaml   ← your name, email, tz    │${RESET}"
    echo -e "${BOLD}│  2.  python scripts/generate_identity.py                        │${RESET}"
    echo -e "${BOLD}│  3.  Open your AI CLI and say:  catch me up                     │${RESET}"
    echo -e "${BOLD}│                                                                 │${RESET}"
    echo -e "${BOLD}│  Or re-run the wizard anytime:   python artha.py --setup        │${RESET}"
    echo -e "${BOLD}└─────────────────────────────────────────────────────────────────┘${RESET}"
    blank
  fi
else
  # Non-interactive (CI / piped) — just copy the starter profile silently
  if [ ! -f "config/user_profile.yaml" ]; then
    src="config/user_profile.starter.yaml"
    [ -f "$src" ] || src="config/user_profile.example.yaml"
    cp "$src" config/user_profile.yaml
  fi
fi
