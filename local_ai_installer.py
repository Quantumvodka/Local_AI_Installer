#!/usr/bin/env python3
"""Local AI Installer: detect hardware -> pick best uncensored local models -> install -> verify.

Stdlib only. Works on Windows, macOS and Linux.

    python local_ai_installer.py            # interactive: detect, recommend, confirm, install
    python local_ai_installer.py --dry-run  # detect + recommend only, change nothing
    python local_ai_installer.py --yes      # no prompts
    python local_ai_installer.py --no-webui --no-vscode
"""
import argparse
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

OS = platform.system()  # Linux / Darwin / Windows
OLLAMA_API = "http://127.0.0.1:11434"
VERSION = "1.1.0"
REPO = "Quantumvodka/Local_AI_Installer"
STATE_DIR = os.path.join(os.path.expanduser("~"), ".local_ai_installer")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
MANAGED_MARK = "# managed by Local AI Installer"
UPGRADE_MARGIN = 1.10  # only switch models if the new one scores >=10% higher

# ---------------------------------------------------------------- catalog
# Tiers keyed by usable GB of GPU VRAM (or unified/system memory share).
# Each model lists Ollama tags in preference order; the first that pulls wins.
# "Unlocked" = abliterated / Dolphin-style fine-tunes with refusals removed.
TIERS = [
    # (min_gb, label, coder_tags, chat_tags)
    (0, "tiny (<4 GB)",
     ["qwen2.5-coder:1.5b"],
     ["huihui_ai/qwen3-abliterated:1.7b", "dolphin3:8b"]),
    (4, "small (4-6 GB)",
     ["qwen2.5-coder:3b"],
     ["huihui_ai/qwen3-abliterated:4b", "huihui_ai/qwen3-abliterated:1.7b"]),
    (6, "medium (6-10 GB)",
     ["huihui_ai/qwen2.5-coder-abliterated:7b", "qwen2.5-coder:7b"],
     ["huihui_ai/qwen3-abliterated:8b", "dolphin3:8b"]),
    (10, "large (10-16 GB)",
     ["huihui_ai/qwen2.5-coder-abliterated:14b", "qwen2.5-coder:14b"],
     ["huihui_ai/qwen3-abliterated:14b", "dolphin3:8b"]),
    (16, "xlarge (16-28 GB)",
     ["huihui_ai/qwen2.5-coder-abliterated:32b", "qwen2.5-coder:32b"],
     ["huihui_ai/qwen3-abliterated:32b", "huihui_ai/qwen3-abliterated:14b"]),
    (28, "workstation (28+ GB)",
     ["qwen3-coder:30b", "huihui_ai/qwen2.5-coder-abliterated:32b"],
     ["huihui_ai/qwen3-abliterated:32b", "huihui_ai/qwen3-abliterated:14b"]),
]
EMBED_MODEL = "nomic-embed-text"  # for Continue codebase indexing

VSCODE_PLUGINS = [
    ("Continue.continue", "Continue: chat/autocomplete/edit in VS Code using local models"),
]


# ---------------------------------------------------------------- helpers
def run(cmd, timeout=60, shell=False):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=shell)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def say(msg=""):
    print(msg, flush=True)


def step(n, title):
    say(f"\n=== [{n}/4] {title} ===")


def have(cmd):
    return shutil.which(cmd) is not None


def confirm(q, assume_yes):
    if assume_yes:
        return True
    return input(f"{q} [Y/n] ").strip().lower() in ("", "y", "yes")


# ---------------------------------------------------------------- 1. detect
def ram_gb():
    if OS == "Linux":
        m = re.search(r"MemTotal:\s+(\d+)", open("/proc/meminfo").read())
        return int(m.group(1)) / 1024 / 1024 if m else 0
    if OS == "Darwin":
        return int(run(["sysctl", "-n", "hw.memsize"]) or 0) / 1024**3
    out = run(["powershell", "-NoProfile", "-Command",
               "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"])
    return int(out or 0) / 1024**3


def cpu_name():
    if OS == "Linux":
        m = re.search(r"model name\s*:\s*(.+)", open("/proc/cpuinfo").read())
        return m.group(1) if m else platform.processor()
    if OS == "Darwin":
        return run(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor()
    return run(["powershell", "-NoProfile", "-Command",
                "(Get-CimInstance Win32_Processor).Name"]) or platform.processor()


def detect_gpus():
    """Return list of dicts: {name, vram_gb, vendor}."""
    gpus = []
    if have("nvidia-smi"):
        out = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
        for line in out.splitlines():
            name, _, mem = line.rpartition(",")
            gpus.append({"name": name.strip(), "vram_gb": float(mem) / 1024, "vendor": "nvidia"})
    if not gpus and have("rocm-smi"):
        out = run(["rocm-smi", "--showproductname", "--showmeminfo", "vram", "--json"])
        try:
            for card in json.loads(out).values():
                total = int(card.get("VRAM Total Memory (B)", 0)) / 1024**3
                gpus.append({"name": card.get("Card Series", "AMD GPU"), "vram_gb": total, "vendor": "amd"})
        except (ValueError, AttributeError):
            pass
    if not gpus and OS == "Windows":
        # AdapterRAM is a 32-bit field (caps at 4 GB) so only a rough fallback.
        out = run(["powershell", "-NoProfile", "-Command",
                   "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name + '|' + $_.AdapterRAM }"])
        for line in out.splitlines():
            name, _, ram = line.partition("|")
            if any(v in name for v in ("NVIDIA", "AMD", "Radeon", "Arc")):
                gpus.append({"name": name, "vram_gb": int(ram or 0) / 1024**3,
                             "vendor": "amd" if "AMD" in name or "Radeon" in name else "other"})
    if not gpus and OS == "Linux" and have("lspci"):
        for line in run(["lspci"]).splitlines():
            if re.search(r"VGA|3D", line) and re.search(r"AMD|Radeon|NVIDIA|Intel", line):
                gpus.append({"name": line.split(": ", 1)[-1], "vram_gb": 0, "vendor": "unknown"})
    return gpus


def detect():
    apple_silicon = OS == "Darwin" and platform.machine() == "arm64"
    ram = ram_gb()
    gpus = detect_gpus()
    best_vram = max((g["vram_gb"] for g in gpus), default=0)
    if apple_silicon:
        usable, mode = ram * 0.70, "Apple unified memory (Metal)"
    elif best_vram >= 4:
        usable, mode = best_vram, "dedicated GPU VRAM"
    else:
        usable, mode = ram * 0.50, "CPU + system RAM (slower)"
    return {
        "os": f"{OS} {platform.release()}", "arch": platform.machine(),
        "cpu": cpu_name(), "cores": os.cpu_count(), "ram_gb": round(ram, 1),
        "gpus": gpus, "usable_gb": round(usable, 1), "mode": mode,
        "disk_free_gb": round(shutil.disk_usage(os.path.expanduser("~")).free / 1024**3, 1),
    }


# ---------------------------------------------------------------- 2. research
# Live research: query Hugging Face for GGUF models, read the real file sizes,
# keep only quants that fit THIS machine, then rank by capability/recency/popularity.
HF = "https://huggingface.co/api/models"
UNLOCK_TERMS = ["abliterated", "uncensored", "dolphin", "heretic"]
BAD_NAME = re.compile(r"lora|draft|embed|rerank|mmproj|guard|tts|whisper|-base\b|awq|gptq|mlx", re.I)
# quant label -> quality factor (relative to full precision)
QUANTS = {"Q8_0": 1.0, "Q6_K": 0.99, "Q5_K_M": 0.97, "Q5_K_S": 0.96, "Q4_K_M": 0.93,
          "Q4_K_S": 0.91, "IQ4_XS": 0.90, "Q4_0": 0.88, "Q3_K_L": 0.82, "Q3_K_M": 0.80}
QUANT_RE = re.compile(r"[-.](" + "|".join(sorted(QUANTS, key=len, reverse=True)) + r")\.gguf$", re.I)
MAX_AGE_MONTHS = 20


def hf_get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "local-ai-installer"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def months_old(iso):
    try:
        t = time.mktime(time.strptime(iso[:10], "%Y-%m-%d"))
        return max(0.0, (time.time() - t) / (30.4 * 86400))
    except (ValueError, TypeError):
        return 99.0


def best_quant(files, budget_gb):
    """files: [(filename, size_gb)]. Return (quant, size_gb) of the best-quality quant within budget."""
    fits = []
    for name, size in files:
        m = QUANT_RE.search(name)
        if m and "-of-" not in name and size <= budget_gb:  # skip split GGUFs
            q = m.group(1).upper()
            fits.append((QUANTS[q], q, size))
    if not fits:
        return None
    # prefer the largest file that fits (= highest quality), tie-break on quality factor
    fits.sort(key=lambda x: (x[2], x[0]))
    _, q, size = fits[-1]
    return q, size


def score(size_gb, quant, downloads, likes, age_months):
    capability = size_gb * QUANTS[quant]                  # bigger model that fits ~ smarter
    recency = 0.5 ** (age_months / 9.0)                   # halves every 9 months
    popularity = 0.5 + 0.5 * min(1.0, math.log10(downloads + likes * 20 + 1) / 5.5)
    return capability * recency * popularity


def search_models(terms, extra, limit=40):
    seen = {}
    for t in terms:
        q = urllib.parse.quote(f"{t} {extra}".strip())
        try:
            for m in hf_get(f"{HF}?search={q}&filter=gguf&sort=downloads&direction=-1&limit={limit}"):
                seen[m["id"]] = m
        except Exception:
            continue
    return list(seen.values())


def rank_candidates(models, budget_gb, want, detail=hf_get):
    """want: 'coder' or 'chat'. Returns scored candidates, best first."""
    out = []
    for m in models:
        rid = m["id"]
        low = rid.lower()
        if BAD_NAME.search(rid) or not any(t in low for t in UNLOCK_TERMS):
            continue
        is_coder = "cod" in low
        if (want == "coder") != is_coder:
            continue
        age = months_old(m.get("createdAt") or m.get("lastModified"))
        if age > MAX_AGE_MONTHS:
            continue
        try:
            info = detail(f"{HF}/{rid}?blobs=true")
        except Exception:
            continue
        files = [(f["rfilename"], (f.get("size") or 0) / 1024**3) for f in info.get("siblings", [])
                 if f["rfilename"].lower().endswith(".gguf")]
        bq = best_quant(files, budget_gb)
        if not bq:
            continue
        quant, size = bq
        out.append({"repo": rid, "quant": quant, "size_gb": round(size, 1), "age_months": round(age, 1),
                    "downloads": m.get("downloads", 0), "likes": m.get("likes", 0),
                    "score": score(size, quant, m.get("downloads", 0), m.get("likes", 0), age),
                    "tag": f"hf.co/{rid}:{quant}"})
    out.sort(key=lambda c: -c["score"])
    return out[:5]


def research(spec):
    """Live HF research. Returns {'coder': [...], 'chat': [...]} candidate lists (may be empty)."""
    budget = spec["usable_gb"] * 0.85  # headroom for context / KV cache
    res = {}
    res["chat"] = rank_candidates(search_models(UNLOCK_TERMS, ""), budget, "chat")
    res["coder"] = rank_candidates(search_models(UNLOCK_TERMS, "coder"), budget, "coder")
    return res


def recommend(spec):
    """Offline fallback: curated tiers."""
    tier = TIERS[0]
    for t in TIERS:
        if spec["usable_gb"] >= t[0]:
            tier = t
    return {"tier": tier[1], "coder": tier[2], "chat": tier[3]}


def show(cands, label):
    for i, c in enumerate(cands, 1):
        say(f"  {i}. {c['repo']} [{c['quant']}, {c['size_gb']} GB, {c['age_months']} mo old, "
            f"{c['downloads']:,} downloads]")


def est_size_gb(tag, cands):
    """Download size estimate: exact for live candidates, ~Q4 params*0.6 for curated tags."""
    for c in cands:
        if c["tag"] == tag:
            return c["size_gb"]
    m = re.search(r"(\d+(?:\.\d+)?)b", tag.lower())
    return round(float(m.group(1)) * 0.6, 1) if m else 5.0


def free_gb(path):
    while path and not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return shutil.disk_usage(path or ".").free / 1024**3


def models_dir(arg):
    return os.path.abspath(os.path.expanduser(
        arg or os.environ.get("OLLAMA_MODELS") or os.path.join("~", ".ollama", "models")))


# ---------------------------------------------------------------- state / updates
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    state["installer_version"] = VERSION
    state["updated"] = time.strftime("%Y-%m-%d")
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def check_self_update():
    """Tell the user if a newer installer release exists (launchers always fetch the latest anyway)."""
    try:
        tag = hf_get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=8)["tag_name"]
    except Exception:
        return
    if tag.lstrip("v") != VERSION:
        if getattr(sys, "frozen", False):
            say(f"A new installer version ({tag}) is available: https://github.com/{REPO}/releases/latest")
        else:
            say(f"Installer {tag} is out (you have v{VERSION}); the launchers fetch the newest script automatically.")


def decide(kind, opts, live, state):
    """Keep the installed model unless a clearly better one exists. Returns (opts, upgrading)."""
    old = state.get(kind, {}).get("tag")
    if not old:
        return opts, False
    top = live[0] if live else None
    if opts[0] == old:
        say(f"{kind}: {old} is still the best pick - no change.")
        return [old], False
    cur = next((c for c in live if c["tag"] == old), None)
    if cur and top and cur["score"] * UPGRADE_MARGIN >= top["score"]:
        say(f"{kind}: newer option {opts[0]} isn't clearly better than {old} - keeping {old}.")
        return [old], False
    say(f"{kind}: upgrade available: {old} -> {opts[0]}")
    return opts, True


def upgrade_ollama():
    say("Updating Ollama...")
    if OS == "Linux":
        subprocess.call("curl -fsSL https://ollama.com/install.sh | sh", shell=True)
    elif OS == "Darwin" and have("brew"):
        subprocess.call(["brew", "upgrade", "ollama"])
    elif OS == "Windows" and have("winget"):
        subprocess.call(["winget", "upgrade", "-e", "--id", "Ollama.Ollama",
                         "--accept-source-agreements", "--accept-package-agreements"])


# ---------------------------------------------------------------- 3. install
def ollama_up():
    try:
        urllib.request.urlopen(OLLAMA_API, timeout=2)
        return True
    except Exception:
        return False


def install_ollama():
    if have("ollama"):
        say("Ollama already installed.")
        return True
    say("Installing Ollama (official installer)...")
    if OS == "Linux":
        rc = subprocess.call("curl -fsSL https://ollama.com/install.sh | sh", shell=True)
    elif OS == "Darwin":
        rc = subprocess.call(["brew", "install", "ollama"]) if have("brew") else 1
        if rc:
            say("Install Homebrew, or download Ollama from https://ollama.com/download")
    else:
        if have("winget"):
            rc = subprocess.call(["winget", "install", "-e", "--id", "Ollama.Ollama",
                                  "--accept-source-agreements", "--accept-package-agreements"])
        else:
            rc = 1
            say("winget not found. Download Ollama from https://ollama.com/download/windows")
    return rc == 0 and (have("ollama") or OS == "Windows")


def start_ollama():
    if ollama_up():
        return True
    say("Starting Ollama server...")
    kw = {"creationflags": 0x00000008} if OS == "Windows" else {"start_new_session": True}
    try:
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=os.environ.copy(), **kw)
    except OSError:
        return False
    for _ in range(30):
        if ollama_up():
            return True
        time.sleep(1)
    return False


def pull_first(tags):
    for tag in tags:
        say(f"Pulling {tag} ...")
        if subprocess.call(["ollama", "pull", tag]) == 0:
            return tag
        say(f"  {tag} unavailable, trying next option")
    return None


def install_vscode_plugins():
    code = shutil.which("code")
    if not code:
        say("VS Code CLI ('code') not found; skipping extensions. Install from https://code.visualstudio.com")
        return
    for ext, desc in VSCODE_PLUGINS:
        say(f"Installing VS Code extension {ext} ({desc})")
        subprocess.call([code, "--install-extension", ext, "--force"])


def write_continue_config(coder, chat):
    path = os.path.join(os.path.expanduser("~"), ".continue", "config.yaml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        if MANAGED_MARK not in open(path).read():
            say(f"Your Continue config was edited by you, so it was left alone. Point it at: coder={coder} chat={chat}")
            return
        shutil.copy(path, path + ".bak")
    cfg = f"""{MANAGED_MARK} - delete this line to stop automatic updates of this file
name: Local AI
version: 1.0.0
schema: v1
models:
  - name: Local Chat (unlocked)
    provider: ollama
    model: {chat}
    roles: [chat]
  - name: Local Coder
    provider: ollama
    model: {coder}
    roles: [chat, edit, apply, autocomplete]
  - name: Embeddings
    provider: ollama
    model: {EMBED_MODEL}
    roles: [embed]
"""
    with open(path, "w") as f:
        f.write(cfg)
    say(f"Wrote Continue config -> {path}")


def install_webui():
    """Open WebUI: ChatGPT-style browser UI for Ollama. Uses Docker if present, else pip venv."""
    if have("docker"):
        say("Starting Open WebUI via Docker on http://localhost:3000 ...")
        subprocess.call(["docker", "pull", "ghcr.io/open-webui/open-webui:main"])
        subprocess.call(["docker", "rm", "-f", "open-webui"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rc = subprocess.call(["docker", "run", "-d", "--name", "open-webui", "--restart", "always",
                              "--add-host=host.docker.internal:host-gateway", "-p", "3000:8080",
                              "-e", "OLLAMA_BASE_URL=http://host.docker.internal:11434",
                              "-v", "open-webui:/app/backend/data",
                              "ghcr.io/open-webui/open-webui:main"])
        return "http://localhost:3000" if rc == 0 else None
    say("Docker not found; Open WebUI skipped (install Docker Desktop and re-run, or use --no-webui).")
    return None


def verify(model):
    say(f"Smoke test: asking {model} to write code...")
    body = json.dumps({"model": model, "stream": False,
                       "prompt": "Write a Python one-liner that reverses a string. Code only."}).encode()
    req = urllib.request.Request(f"{OLLAMA_API}/api/generate", body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            reply = json.load(r).get("response", "").strip()
        say("  model replied: " + reply[:200].replace("\n", " "))
        return bool(reply)
    except Exception as e:
        say(f"  smoke test failed: {e}")
        return False


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="detect and recommend only")
    ap.add_argument("-y", "--yes", action="store_true", help="don't prompt")
    ap.add_argument("--offline", action="store_true", help="skip live Hugging Face trend lookup")
    ap.add_argument("--no-webui", action="store_true")
    ap.add_argument("--no-vscode", action="store_true")
    ap.add_argument("--prune", action="store_true", help="after an upgrade, delete the old model to free space")
    ap.add_argument("--version", action="version", version=f"Local AI Installer v{VERSION}")
    ap.add_argument("--models-dir", help="store downloaded models here (e.g. a big external SSD). Must be a LOCAL drive, not Google Drive/OneDrive/network")
    a = ap.parse_args()

    state = load_state()
    updating = bool(state.get("coder"))
    say(f"Local AI Installer v{VERSION}" + ("  -  UPDATE MODE (your chats, projects and settings are kept)" if updating else ""))
    if not a.offline:
        check_self_update()

    step(1, "Detecting your PC")
    spec = detect()
    say(f"OS:    {spec['os']} ({spec['arch']})")
    say(f"CPU:   {spec['cpu']} ({spec['cores']} threads)")
    say(f"RAM:   {spec['ram_gb']} GB")
    for g in spec["gpus"] or [{"name": "none detected", "vram_gb": 0}]:
        say(f"GPU:   {g['name']} ({g['vram_gb']:.1f} GB)")
    say(f"Disk:  {spec['disk_free_gb']} GB free")
    say(f"Plan:  run models on {spec['mode']} -> ~{spec['usable_gb']} GB usable")

    step(2, "Researching best unlocked local AI stack")
    rec = recommend(spec)
    live = {"coder": [], "chat": []}
    if not a.offline:
        say("Searching Hugging Face for the newest unlocked models that fit your memory...")
        live = research(spec)
    if live["coder"] and live["chat"]:
        say("Top coding models (unlocked):")
        show(live["coder"], "coder")
        say("Top chat/LLM models (unlocked):")
        show(live["chat"], "chat")
        coder_opts = [c["tag"] for c in live["coder"][:3]] + rec["coder"]
        chat_opts = [c["tag"] for c in live["chat"][:3]] + rec["chat"]
        say(f"Chosen coding: {coder_opts[0]}")
        say(f"Chosen chat:   {chat_opts[0]}")
    else:
        if not a.offline:
            say("Live research unavailable (offline/blocked?) - using built-in curated list.")
        coder_opts, chat_opts = rec["coder"], rec["chat"]
        say(f"Tier:       {rec['tier']}")
        say(f"Coding:     {coder_opts[0]}")
        say(f"Chat/LLM:   {chat_opts[0]}")
    old_models = {}
    if updating:
        say("\nChecking your installed models against the latest research...")
        coder_opts, up_c = decide("coder", coder_opts, live["coder"], state)
        chat_opts, up_h = decide("chat", chat_opts, live["chat"], state)
        old_models = {k: state[k]["tag"] for k, up in (("coder", up_c), ("chat", up_h)) if up}
    say("Runtime:    Ollama (llama.cpp engine; CUDA / ROCm / Metal / CPU auto-selected)")
    say(f"Embeddings: {EMBED_MODEL}")
    say("Plug-ins:   " + "; ".join(d for _, d in VSCODE_PLUGINS) + "; Open WebUI (browser chat UI)")
    if spec["usable_gb"] < 4:
        say("NOTE: low memory - expect slow responses; consider a smaller quant or a GPU.")
    mdir = models_dir(a.models_dir)
    need = 0.0
    for kind, opts in (("coder", coder_opts), ("chat", chat_opts)):
        if not (updating and state.get(kind, {}).get("tag") == opts[0]):  # already on disk -> no new space
            need += est_size_gb(opts[0], live[kind])
    need += 0 if updating else 0.3
    need += 0 if (a.no_webui or updating) else 4.0  # Open WebUI docker image
    free = free_gb(mdir)
    say(f"\nStorage: needs ~{need:.1f} GB (models + extras); {free:.1f} GB free where models are stored ({mdir})")
    say("         Models must live on a local drive - cloud-synced folders (Google Drive, OneDrive) are too slow and can corrupt them.")
    if re.search(r"google ?drive|onedrive|dropbox|icloud", mdir, re.I):
        say("ERROR: that folder looks cloud-synced. Choose a local/external drive with --models-dir.")
        return 1
    low_space = free < need * 1.15
    if low_space:
        say("WARNING: not enough free space. Free some up, choose a bigger drive with --models-dir, or pick a smaller tier.")
    if a.dry_run:
        say("\nDry run: nothing installed.")
        return 0
    if low_space:
        return 1
    if not confirm("\nProceed with install?", a.yes):
        return 0
    if a.models_dir:
        os.environ["OLLAMA_MODELS"] = mdir
        os.makedirs(mdir, exist_ok=True)
        if OS == "Windows":
            subprocess.call(["setx", "OLLAMA_MODELS", mdir], stdout=subprocess.DEVNULL)
        else:
            say(f"Add this to your shell profile so it persists: export OLLAMA_MODELS={mdir}")

    step(3, "Installing")
    if updating and have("ollama"):
        upgrade_ollama()
    if not install_ollama() or not start_ollama():
        say("ERROR: could not install/start Ollama. See https://ollama.com/download")
        return 1
    coder = pull_first(coder_opts)
    chat = pull_first(chat_opts)
    pull_first([EMBED_MODEL])
    if not (coder and chat):
        say("ERROR: model download failed (check network/disk space).")
        return 1
    if not a.no_vscode:
        install_vscode_plugins()
        write_continue_config(coder, chat)
    url = None if a.no_webui else install_webui()

    step(4, "Verifying")
    ok = verify(coder)
    if ok:
        def entry(tag, cands):
            return {"tag": tag, "score": next((c["score"] for c in cands if c["tag"] == tag), None)}
        state.update(coder=entry(coder, live["coder"]), chat=entry(chat, live["chat"]),
                     models_dir=mdir, webui=bool(url))
        save_state(state)
        for kind, old in old_models.items():
            if a.prune or (not a.yes and confirm(f"Upgrade worked. Delete old {kind} model {old} to free space?", False)):
                subprocess.call(["ollama", "rm", old])
            else:
                say(f"Kept old model {old} (remove later with: ollama rm {old})")
    say("\n" + ("READY." if ok else "Installed, but the smoke test failed - run `ollama run " + coder + "`."))
    say(f"  Terminal chat : ollama run {chat}")
    say(f"  Coding model  : ollama run {coder}")
    if not a.no_vscode:
        say("  VS Code       : open VS Code, Continue sidebar (Ctrl+L) is preconfigured")
    if url:
        say(f"  Browser UI    : {url}")
    return 0 if ok else 1


if __name__ == "__main__":
    code = main()
    if getattr(sys, "frozen", False) and sys.stdin.isatty():  # double-clicked .exe: keep window open
        input("\nPress Enter to close...")
    sys.exit(code)
