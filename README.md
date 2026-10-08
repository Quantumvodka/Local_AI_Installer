# Local AI Installer

One click sets up a private, **uncensored AI on your own PC** for chatting and coding. Nothing is sent to anyone.

It looks at your hardware, researches the best current unlocked ("abliterated") models that fit it, installs everything, checks that it works, and **opens the chat in your browser**. It also watches for **PewDiePie's own AI model (Ajax)** and installs it once it's officially out, and can add **Odysseus** (PewDiePie's AI workspace) and a **GitHub connection** for your AI.

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
2. **Researches live** on Hugging Face for the newest unlocked models that fit that memory (see "How the research works"), with a built-in fallback list if you're offline. It only ever installs **unlocked** models: it tests that they really answer instead of refusing, and never falls back to a standard censored one unless you add `--allow-standard`.
3. **Installs**:
   - **Ollama**: the engine that runs the models (uses your GPU automatically).
   - **A coding model, a chat model, a small fast autocomplete model** and a code-search model.
   - **Open WebUI**: a ChatGPT-style chat in your browser (no Docker needed; it fetches its own Python).
   - **VS Code + the Continue extension**, already pointed at your local models, for AI coding in your editor.
4. **Checks everything works** (asks each model a question, tests the chat page) and fixes what it can.
5. **Opens the chat** and puts a **"Local AI Chat"** shortcut on your Desktop and Start menu for next time.

**One place to chat:** the page that opens (http://localhost:3210), also the **Local AI Chat** shortcut. The installer creates your login so you never see the sign-up form: the email and password are shown at the end and saved in `~/.local_ai_installer/webui-login.txt`; change the password inside the chat (Settings -> Account). Everything else below is optional.

**Which model do I pick?** The model list in the chat shows only your unlocked models: one for everyday chat, one for coding (and PewDiePie's Ajax once it's out). The small helper models VS Code uses (autocomplete, code search) are named `helper/...-only-...` and hidden from the list: they aren't for chatting.

**Ollama's own window:** installing Ollama can open its own little chat app. That is just the engine: close it. The installer switches Ollama's cloud models off (they aren't on your PC and aren't unlocked) and keeps that window from opening on updates.

Optional extras (the installer asks; or pass the option):

- **PewDiePie's AI (Ajax)**: checked automatically on every run, see below.
- **Odysseus** (`--odysseus`): PewDiePie's open-source AI workspace: chat, agents that do things on your PC, deep research, documents, notes. Uses the same local models. Gets an **"Odysseus AI"** shortcut.
- **GitHub for your AI** (`--github`): your AI can read your repos, issues and pull requests, in VS Code (Continue, Agent mode) and Odysseus.

## PewDiePie's AI

PewDiePie's own model is **Ajax**: a Qwen3.5-9B fine-tune with refusals trimmed, made to run his **Odysseus** workspace. He announced it on 2 October 2026 and then paused the release to finish it, so **its weights aren't public yet**. Every time you run the installer it looks again, and when Ajax is out (and fits your PC) it's installed next to your other models. Pick it in the chat's model list.

It only trusts **official sources**: PewDiePie's Ajax page ([data.pewdiepie.com](https://data.pewdiepie.com)), the [Odysseus project](https://github.com/odysseus-dev/odysseus), and [`featured.json`](featured.json) in this repo. If the official files are full-size weights only, it uses a GGUF build from a well-known re-packager (bartowski, unsloth, LM Studio, ggml-org, mradermacher), never a random upload.

> **Fake "PewDiePie Ajax" downloads already exist** (GitHub repos offering a "Windows zip" of Ajax, plus an "Ajax" crypto token). They appeared before Ajax was even released, so they aren't it: **don't run them.** A real model is a data file you run through Ollama, never an `.exe`.

When Ajax comes out, adding its official repo to `featured.json` makes every install pick it up straight away, even if the official page changes in a way the installer doesn't recognise.

## AI that does things on your PC: what's safe

You can have an AI that acts on your PC. Some caution is still needed, because no AI agent is safe to give unrestricted access to your PC with no permission checks at all. Two things can go wrong:

- **Mistakes**: a local model misreads what you asked and deletes or overwrites the wrong files.
- **Hijacking ("prompt injection")**: a web page, email or GitHub issue the AI reads contains hidden instructions, and the AI follows them. Uncensored models are *more* likely to obey, because they don't refuse.

So the installer sets things up like this:

| | Without asking you | Asks you first / can't |
|---|---|---|
| **Chat (Open WebUI)** | chats; it has no tools that touch your PC unless you add some | n/a |
| **VS Code (Continue, Agent mode)** | only the tools you switch to "Automatic" in its tools menu | everything else: Continue asks first by default |
| **Odysseus agent** | runs commands and edits files (files: inside its workspace folder) | asks first once it has read web pages, emails or other outside content |
| **GitHub** | reads your repos, issues and pull requests | can't push, merge, comment or delete (read-only), unless you use `--github-write` |

Odysseus's shell is **not sandboxed** (its own docs say so), which is why it's only installed when you ask for it, and why you should **keep backups** of anything important. For an agent with no limits at all, run it on a spare PC or in a virtual machine, where mistakes can't reach your real files.

GitHub uses GitHub's official connector ([github-mcp-server](https://github.com/github/github-mcp-server)) in **read-only** and **lockdown mode** (hides issue and PR text written by strangers in public repos, a common hijacking route). There's no token to create: the first time the AI uses GitHub, your browser opens GitHub's own sign-in page, and the login is kept in memory only.

### Odysseus details

- It's installed natively (no Docker) from the project's curated `main` branch, with its own private Python, and only listens on this PC.
- The installer creates its login: user `admin` and a random password, shown at the end and saved in `~/.local_ai_installer/odysseus-login.txt` (readable only by you). Change it in Odysseus → Settings.
- It finds your Ollama models by itself. Its long-term memory search uses simple keyword matching in this setup, because its vector database only comes with its Docker setup. Everything else works.
- If you connected GitHub, it's added under Settings → MCP. If you changed the admin password, the installer can't add it for you and prints the two values to paste in instead.

## Updating

Run the same download again. It finds your install and, only if a new model scores at least 10% better than yours, downloads it, updates Ollama (only when a newer version exists) and Open WebUI, and checks it works. It also checks for PewDiePie's Ajax and, if you have them, updates Odysseus and the GitHub connector. The `.exe` also downloads the newest installer logic by itself when one is published.

**It cleans up after itself** once everything works: models it downloaded earlier that nothing uses any more (`--keep-old` to keep them), the package download cache (several GB), leftover installers, and half-finished downloads. It only removes what it created: models you installed yourself, your chats and your projects are never touched. If the new model doesn't work out, the one you have stays.

Your stuff is never touched:
- **Chats and settings** live in `~/.local_ai_installer/webui-data` and survive every update. New models simply appear in the model list; older chats keep their original model name, so pick the new model from the dropdown to continue them.
- **Odysseus's chats, settings and login** live in `~/.local_ai_installer/odysseus-data`, separate from the program, so updates keep them.
- **VS Code / Continue** is re-pointed at the new models. If you edited `~/.continue/config.yaml` yourself, it is left alone (a config that Continue itself rewrote is replaced, and the old one is kept as `config.yaml.lai-<time>.bak`). In Continue's "get started" card, close it: don't press "Use local models", which would add a standard Llama model.
- **Your code and projects** are never stored by this tool.

GitHub write access (`--github-write`) is only kept for the run that asks for it: run the installer without it and your AI goes back to read-only.

## Storage

The installer shows how much space it needs and stops if you don't have enough. Expect roughly **6-50 GB** depending on your hardware tier (Odysseus adds about 1.5 GB, Ajax about 6 GB). To keep models on another drive: `--models-dir D:\AIModels`. Models can't run from Google Drive / OneDrive (too slow, and syncing can corrupt them), so the installer refuses cloud-synced folders.

## Options

```
--dry-run         detect + research only, install nothing
--check           health-check an existing install and repair it
--launch          start everything and open the chat (what the shortcut runs)
--launch odysseus start Odysseus and open it (the "Odysseus AI" shortcut)
-y / --yes        no questions (extras are then only added with the options below)
--odysseus        also install Odysseus, PewDiePie's AI workspace
--github          let your AI read your GitHub (read-only)
--github-write    ...and also make changes (push, open issues / pull requests)
--no-pewdiepie    don't look for PewDiePie's Ajax model
--offline         skip the live research (use the built-in list)
--no-webui  --no-vscode  --no-open  --no-shortcut
--models-dir DIR  store models on another local drive
--max-gb N        never pick models needing more than N GB (slow PCs)
--keep-old        don't delete models this installer downloaded earlier and no longer uses
--allow-standard  if no unlocked model works, allow a standard one (it will have refusals)
--no-odysseus     uninstall Odysseus (chats and login are kept) so the chat page is the only place
```

## How the research works

It searches Hugging Face for GGUF models tagged abliterated / uncensored / dolphin / heretic / josiefied / unfiltered (no older than 20 months), reads each repo's real file sizes, keeps only quantizations that fit your usable memory (85%, leaving room for context), and ranks them by **parameters x quality of quantization x recency (halves every 9 months) x popularity**. The winner is downloaded with `ollama pull hf.co/<repo>:<quant>`; if that fails the next one is tried, then the built-in list of unlocked models. Each model must then answer a test question and **pass a refusal check** (three harmless requests that censored models tend to turn down; refusing two of them means it is not really unlocked and the next one is tried). Gemma 4 models that print raw `<|channel>thought` text are fixed with a copy that Ollama understands, or skipped.

This is a heuristic, not a benchmark: nobody publishes a trustworthy "best unlocked model for your PC" ranking, so newer + bigger-that-fits + popular is a good proxy, not a guarantee. CPU-only PCs are capped at about 7 GB models because anything bigger is painfully slow.

## Troubleshooting

- Everything the installer does is written to **`~/.local_ai_installer/install.log`** (on Windows: `%USERPROFILE%\.local_ai_installer\install.log`). Open WebUI's own log is `webui.log` next to it, Odysseus's is `odysseus.log`. Send those if something fails (`odysseus-login.txt` holds your password: don't send that one).
- `--check` re-tests everything and restarts whatever stopped (including that your chat login works and the model list is tidy).
- To remove the extra places to chat: `--no-odysseus`. In PowerShell, run `$env:LAI_ARGS="--no-odysseus"` before the one-line install command.
- The chat and Odysseus are only reachable from your own PC (they listen on `127.0.0.1`).

## Safety and licence

"Unlocked" community models have no content filtering and vary in quality; you are responsible for how you use them. Models are downloaded from third parties under their own licences. Review the code before running it: it downloads and runs the official installers for Ollama and uv, GitHub's github-mcp-server (MIT) when you connect GitHub, and Odysseus (AGPL-3.0) when you ask for it. MIT licensed, provided as-is.

## For developers

`python -m unittest discover -s tests -v` runs the offline tests (`LAI_ONLINE=1` adds live Hugging Face / Ollama registry checks). CI runs them on Windows, macOS and Linux. The **End-to-end install test** workflow does a real install on all three. Raising `VERSION` in `local_ai_installer.py` and merging to `main` publishes a new release automatically.
