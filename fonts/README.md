# CV Fonts — Self-Hosted (specs/career-ops.md FR-CS-3, §9.1)

This directory holds Space Grotesk and DM Sans `.woff2` font files used by
`templates/cv-template.html` for ATS-optimized PDF generation via Playwright.

**Why self-hosted:** Playwright runs in a sandboxed Chromium context with no
internet access during PDF render. External CDN font URLs will fail silently,
causing the PDF to render with system fallback fonts (usually Times New Roman
or Helvetica) which may not match the ATS design intent.

## Install

Run the font install script (downloads from Google Fonts CDN, stores locally):

```bash
bash scripts/install_fonts.sh
```

Or via Makefile:

```bash
make install-fonts
```

## Expected files after install

```
fonts/
  SpaceGrotesk-Regular.woff2    (~25 KB)
  SpaceGrotesk-Bold.woff2       (~25 KB)
  DMSans-Regular.woff2          (~40 KB)
  DMSans-Medium.woff2           (~40 KB)
  DMSans-Bold.woff2             (~40 KB)
```

## Source / License

- **Space Grotesk**: © 2020 The Space Grotesk Project Authors (Florian Karsten).
  Licensed under SIL Open Font License v1.1.
  <https://fonts.google.com/specimen/Space+Grotesk>

- **DM Sans**: © 2014-2017 Indian Type Foundry. Licensed under SIL Open Font License v1.1.
  <https://fonts.google.com/specimen/DM+Sans>

Both are free for commercial use under OFL v1.1. Binary files are NOT committed
to this repo (binary bloat). They are generated on first use via `install_fonts.sh`.

## Fallback behavior

If woff2 files are absent, `templates/cv-template.html` falls back to
`local('Space Grotesk')` and `local('DM Sans')` — which works if either font
is installed on the system running Playwright (common on macOS with font packages).
The PDF will still render correctly — only the visual design may differ.
