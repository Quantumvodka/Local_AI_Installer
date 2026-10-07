# Local AI Installer

One click sets up a private, **uncensored AI on your own PC** for chatting and coding. Nothing is sent to anyone.

It looks at your hardware, researches the best current unlocked ("abliterated") models that fit it, installs everything, checks that it works, and **opens the chat in your browser**.

## Download

Go to the **[latest release](https://github.com/Quantumvodka/Local_AI_Installer/releases/latest)**:

| Your PC | Download | Then |
|---|---|---|
| **Windows** | `LocalAIInstaller-Windows.exe` | Double-click. If Windows says *"protected your PC"*: **More info -> Run anyway** (the app is not code-signed). |
| Windows (alternative) | `Install-Windows.bat` | Double-click. Always fetches the newest version. |
| **Mac** | `Install-Mac.zip` | Unzip, then **right-click `Install-Mac.command` -> Open** (first time only). |
| **Linux** | `Install-Linux.sh` | `sh Install-Linux.sh` |

Or paste one line in a terminal:

```powershell
# Windows (PowerShell)
irm https://raw.githubusercontent.com/Quantumvodka/Local_AI_Installer/main/install.ps1 | iex
```
```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/Quantumvodka/Local_AI_Installer/main/install.sh | sh
```

## What you get

1. **Detects** your OS, CPU, RAM and graphics card (NVIDIA, AMD, Apple Silicon, or CPU-only) and how much memory the AI can use.
2. **Researches live** on Hugging Face for the newest unlocked models that fit that memory (see "How the research works"), with a built-in fallback list if you're offline.
3. **Installs**:
   - **Ollama**: the engine that runs the models (uses your GPU automatically).
   - **A coding model, a chat model, a small fast autocomplete model** and a code-search model.
   - **Open WebUI**: a ChatGPT-style chat in your browser (no Docker needed; it fetches its own Python).
   - **VS Code + the Continue extension**, already pointed at your local models, for AI coding in your editor.
4. **Checks everything works** (asks each model a question, tests the chat page) and fixes what it can.
5. **Opens the chat** and puts a **"Local AI Chat"** shortcut on your Desktop and Start menu for next time.

First time in the chat: click **Get started** and create a local account. It stays on your PC.

## Updating

Run the same download again. It finds your install and, only if a new model scores at least 10% better than yours, downloads it, updates Ollama and Open WebUI, checks it works, then offers to delete the old model (`--prune` does it automatically). The `.exe` also downloads the newest installer logic by itself when one is published.

Your stuff is never touched:
- **Chats and settings** live in `~/.local_ai_installer/webui-data` and survive every update. New models simply appear in the model list; older chats keep their original model name, so pick the new model from the dropdown to continue them.
- **VS Code / Continue** is re-pointed at the new models. If you edited `~/.continue/config.yaml` yourself, it is left alone.
- **Your code and projects** are never stored by this tool.

## Storage

The installer shows how much space it needs and stops if you don't have enough. Expect roughly **6-50 GB** depending on your hardware tier. To keep models on another drive: `--models-dir D:\AIModels`. Models can't run from Google Drive / OneDrive (too slow, and syncing can corrupt them), so the installer refuses cloud-synced folders.

## Options

```
--dry-run        detect + research only, install nothing
--check          health-check an existing install and repair it
--launch         start everything and open the chat (what the shortcut runs)
-y / --yes       no questions
--offline        skip the live research (use the built-in list)
--no-webui  --no-vscode  --no-open  --no-shortcut
--models-dir DIR store models on another local drive
--max-gb N       never pick models needing more than N GB (slow PCs)
--prune          delete the old model after an upgrade
```

## How the research works

It searches Hugging Face for GGUF models tagged abliterated / uncensored / dolphin / heretic / josiefied / unfiltered (no older than 20 months), reads each repo's real file sizes, keeps only quantizations that fit your usable memory (85%, leaving room for context), and ranks them by **parameters x quality of quantization x recency (halves every 9 months) x popularity**. The winner is downloaded with `ollama pull hf.co/<repo>:<quant>`; if that fails the next one is tried, then the built-in list.

This is a heuristic, not a benchmark: nobody publishes a trustworthy "best unlocked model for your PC" ranking, so newer + bigger-that-fits + popular is a good proxy, not a guarantee. CPU-only PCs are capped at about 9 GB models because anything bigger is painfully slow.

## Troubleshooting

- Everything the installer does is written to **`~/.local_ai_installer/install.log`** (on Windows: `%USERPROFILE%\.local_ai_installer\install.log`). Open WebUI's own log is `webui.log` next to it. Send those if something fails.
- `--check` re-tests everything and restarts whatever stopped.
- The chat is only reachable from your own PC (it listens on `127.0.0.1`).

## Safety and licence

"Unlocked" community models have no content filtering and vary in quality; you are responsible for how you use them. Models are downloaded from third parties under their own licences. Review the code before running it: it downloads and runs the official installers for Ollama and uv. MIT licensed, provided as-is.

## For developers

`python -m unittest discover -s tests -v` runs the offline tests (`LAI_ONLINE=1` adds live Hugging Face / Ollama registry checks). CI runs them on Windows, macOS and Linux. The **End-to-end install test** workflow does a real install on all three. Raising `VERSION` in `local_ai_installer.py` and merging to `main` publishes a new release automatically.
