# Local AI Installer

One script that sizes up your PC and sets up an uncensored local AI for coding and chat.

1. **Detects** OS, CPU, RAM, GPU/VRAM (NVIDIA, AMD, Apple Silicon, CPU-only).
2. **Picks** the best model tier for your memory: a coding model plus an "abliterated" (refusals removed) chat model, with fallbacks. The model list is researched live from Hugging Face every run (see below), with a built-in fallback list if offline.
3. **Installs** Ollama, pulls the models, adds the Continue VS Code extension (pre-configured), and optionally Open WebUI (Docker).
4. **Verifies** with a real prompt and prints how to use it.

## Download (easiest - send people this)

Go to the **[Releases page](https://github.com/Quantumvodka/Local_AI_Installer/releases/latest)** and download:

| Your PC | Download | Then |
|---|---|---|
| Windows | `LocalAIInstaller-Windows.exe` (or `Install-Windows.bat`) | double-click. If SmartScreen warns, click *More info -> Run anyway* (the app is unsigned) |
| Mac | `Install-Mac.command` | right-click -> Open (first time only) |
| Linux | `Install-Linux.sh` | `sh Install-Linux.sh` |

The `.bat`/`.command`/`.sh` files are tiny launchers that fetch the latest installer; the `.exe`/binaries are self-contained.

## Quick start (command line)

**Windows** (PowerShell):
```powershell
irm https://raw.githubusercontent.com/Quantumvodka/Local_AI_Installer/main/install.ps1 | iex
```
**macOS / Linux**:
```bash
curl -fsSL https://raw.githubusercontent.com/Quantumvodka/Local_AI_Installer/main/install.sh | sh
```
Add `-s -- --dry-run` after `sh` (Linux/macOS) to preview without installing.

## Manual use

```bash
python local_ai_installer.py --dry-run   # see what it would do
python local_ai_installer.py             # install (asks first)
python local_ai_installer.py -y --no-webui
```

Requires Python 3.8+ only (no pip packages). Windows needs `winget`, macOS needs Homebrew, Linux uses the official Ollama installer (may ask for sudo).

## Updating (just run it again)
Run the same launcher any time. If it finds a previous install (`~/.local_ai_installer/state.json`) it switches to **update mode**: it re-researches, and only if a new model scores at least 10% better than the one you have does it download it, update Ollama and Open WebUI, and verify before offering to delete the old model (`--prune` deletes it automatically). Nothing of yours is touched:
- **Chats and settings** live in Open WebUI's Docker volume (`open-webui`) and are kept; new models just appear in its model list. Old chats keep their original model name, so switch the model in the dropdown to continue them with the new one.
- **VS Code / Continue** config is re-pointed at the new models. If you have edited `~/.continue/config.yaml` yourself, it is left alone.
- **Your code and projects** are never stored by this tool.
- Models themselves are not "data": old and new coexist until you delete the old one.

## Storage
The installer shows the estimated download size and your free space before installing, and stops if there isn't enough. Expect roughly **5-45 GB** depending on your hardware tier (+ ~4 GB for the optional Open WebUI). To keep models on another drive: `python local_ai_installer.py --models-dir D:\\AIModels`. Models can't run from Google Drive/OneDrive (too slow, and sync can corrupt files); the installer refuses cloud-synced folders. If Ollama is already running as a service, restart it after changing the models folder.

## Notes
- **How the live research works:** it searches Hugging Face for GGUF models tagged abliterated / uncensored / dolphin / heretic, reads each repo's real file sizes, keeps only quantizations that fit your usable memory (85%, leaving room for context), and ranks them by size-that-fits x quality of quant x recency (halves every 9 months, max 20 months old) x popularity. It then pulls the winner via `ollama pull hf.co/<repo>:<quant>`.
- This is a heuristic, not a benchmark: no source gives a trustworthy "best model for your PC" ranking for unlocked models. Newer + bigger-that-fits + popular is a good proxy, not a guarantee.
- If Hugging Face can't be reached (or `--offline`), it falls back to the curated `TIERS` list in the script.
- "Unlocked" community models vary in quality and carry no safety guardrails; you are responsible for how you use them.
- Review the script before running it: it downloads and runs the official installers.

## Disclaimer
Provided as-is under the MIT license. Models are downloaded from third parties under their own licenses. Unlocked models have no content filtering; use them responsibly and legally.
