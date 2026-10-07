# Local AI Installer

One script that sizes up your PC and sets up an uncensored local AI for coding and chat.

1. **Detects** OS, CPU, RAM, GPU/VRAM (NVIDIA, AMD, Apple Silicon, CPU-only).
2. **Picks** the best model tier for your memory: a coding model plus an "abliterated" (refusals removed) chat model, with fallbacks. The model list is researched live from Hugging Face every run (see below), with a built-in fallback list if offline.
3. **Installs** Ollama, pulls the models, adds the Continue VS Code extension (pre-configured), and optionally Open WebUI (Docker).
4. **Verifies** with a real prompt and prints how to use it.

## Quick start (share this)

**Windows** (PowerShell):
```powershell
irm https://raw.githubusercontent.com/quantumvodka/local_ai_installer/main/install.ps1 | iex
```
**macOS / Linux**:
```bash
curl -fsSL https://raw.githubusercontent.com/quantumvodka/local_ai_installer/main/install.sh | sh
```
Add `-s -- --dry-run` after `sh` (Linux/macOS) to preview without installing.

## Manual use

```bash
python local_ai_installer.py --dry-run   # see what it would do
python local_ai_installer.py             # install (asks first)
python local_ai_installer.py -y --no-webui
```

Requires Python 3.8+ only (no pip packages). Windows needs `winget`, macOS needs Homebrew, Linux uses the official Ollama installer (may ask for sudo).

## Notes
- **How the live research works:** it searches Hugging Face for GGUF models tagged abliterated / uncensored / dolphin / heretic, reads each repo's real file sizes, keeps only quantizations that fit your usable memory (85%, leaving room for context), and ranks them by size-that-fits x quality of quant x recency (halves every 9 months, max 20 months old) x popularity. It then pulls the winner via `ollama pull hf.co/<repo>:<quant>`.
- This is a heuristic, not a benchmark: no source gives a trustworthy "best model for your PC" ranking for unlocked models. Newer + bigger-that-fits + popular is a good proxy, not a guarantee.
- If Hugging Face can't be reached (or `--offline`), it falls back to the curated `TIERS` list in the script.
- "Unlocked" community models vary in quality and carry no safety guardrails; you are responsible for how you use them.
- Review the script before running it: it downloads and runs the official installers.

## Disclaimer
Provided as-is under the MIT license. Models are downloaded from third parties under their own licenses. Unlocked models have no content filtering; use them responsibly and legally.
