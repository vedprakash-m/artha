# Artha — Supported CLI Setup

Artha works with any AI CLI that supports a system prompt or instruction file.
This page covers setup for the three primary supported CLIs.

---

## Claude Code (Anthropic)

Claude Code reads `CLAUDE.md` from the project root as its system prompt.

### Setup

```bash
# Install Claude Code
npm install -g @anthropic-ai/claude-code

# From the Artha project root:
claude
```

`CLAUDE.md` in the repo root contains the Artha system prompt. Claude Code
automatically loads it when you run `claude` from the Artha directory.

### Usage

```
/catch-up
/goals
/items
```

All Artha commands work natively in Claude Code.

---

## Gemini CLI (Google DeepMind)

Gemini CLI reads `GEMINI.md` from the project root.

### Setup

```bash
# Install Gemini CLI
npm install -g @google/gemini-cli

# From the Artha project root:
gemini
```

### Authentication

```bash
gemini auth login
```

### Usage

Same commands as Claude Code. `GEMINI.md` loads automatically.

---

## GitHub Copilot (VS Code)

Copilot reads `.github/copilot-instructions.md` and the workspace
`AGENTS.md` file.

### Setup

1. Install the GitHub Copilot extension in VS Code.
2. Open the Artha folder as a VS Code workspace.
3. Copilot automatically loads `.github/copilot-instructions.md`.

### Chat Usage

Open Copilot Chat (⇧⌘I / Ctrl+Shift+I) and type Artha commands:

```
@workspace /catch-up
@workspace /goals
```

---

## Comparison

| Feature | Claude Code | Gemini CLI | Copilot Chat |
|---------|------------|-----------|--------------|
| System prompt file | `CLAUDE.md` | `GEMINI.md` | `.github/copilot-instructions.md` |
| Terminal-native | ✅ | ✅ | ❌ (VS Code only) |
| File read/write | ✅ | ✅ | ✅ (with workspace) |
| Script execution | ✅ | ✅ | ✅ (with tools) |
| Best for | Power users, scripting | Google ecosystem | Editor-integrated workflow |

---

## Custom / Other CLIs

Any CLI that supports a Markdown system prompt can be used with Artha.
Point it at `config/Artha.md` as the system prompt, and set the working
directory to the Artha project root.

The system prompt expects:
- Read access to `state/`, `config/`, `briefings/`
- Write access to `state/` and `briefings/`
- Ability to run Python scripts in `scripts/`
