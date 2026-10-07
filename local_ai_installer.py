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
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request

OS = platform.system()  # Linux / Darwin / Windows
OLLAMA_API = "http://127.0.0.1:11434"

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
def hf_trending(limit=5):
    """Best-effort live look at trending GGUF coding/abliterated models on Hugging Face."""
    out = {}
    for label, q in (("coding", "coder gguf"), ("unlocked", "abliterated gguf")):
        url = f"https://huggingface.co/api/models?search={q.replace(' ', '+')}&sort=trendingScore&limit={limit}"
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                out[label] = [m["id"] for m in json.load(r)]
        except Exception:
            out[label] = []
    return out


def recommend(spec):
    tier = TIERS[0]
    for t in TIERS:
        if spec["usable_gb"] >= t[0]:
            tier = t
    return {"tier": tier[1], "coder": tier[2], "chat": tier[3]}


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
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
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
        shutil.copy(path, path + ".bak")
    cfg = f"""name: Local AI
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
    a = ap.parse_args()

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
    say(f"Tier:       {rec['tier']}")
    say(f"Runtime:    Ollama (llama.cpp engine; CUDA / ROCm / Metal / CPU auto-selected)")
    say(f"Coding:     {rec['coder'][0]}  (fallbacks: {', '.join(rec['coder'][1:]) or '-'})")
    say(f"Chat/LLM:   {rec['chat'][0]}  (abliterated = refusals removed)")
    say(f"Embeddings: {EMBED_MODEL}")
    say("Plug-ins:   " + "; ".join(d for _, d in VSCODE_PLUGINS) + "; Open WebUI (browser chat UI)")
    if not a.offline:
        t = hf_trending()
        if t["coding"] or t["unlocked"]:
            say("Live Hugging Face trending (for reference):")
            say("  coding:   " + ", ".join(t["coding"]))
            say("  unlocked: " + ", ".join(t["unlocked"]))
    if spec["usable_gb"] < 4:
        say("NOTE: low memory - expect slow responses; consider a smaller quant or a GPU.")
    if a.dry_run:
        say("\nDry run: nothing installed.")
        return 0
    if not confirm("\nProceed with install?", a.yes):
        return 0

    step(3, "Installing")
    if not install_ollama() or not start_ollama():
        say("ERROR: could not install/start Ollama. See https://ollama.com/download")
        return 1
    coder = pull_first(rec["coder"])
    chat = pull_first(rec["chat"])
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
    say("\n" + ("READY." if ok else "Installed, but the smoke test failed - run `ollama run " + coder + "`."))
    say(f"  Terminal chat : ollama run {chat}")
    say(f"  Coding model  : ollama run {coder}")
    if not a.no_vscode:
        say("  VS Code       : open VS Code, Continue sidebar (Ctrl+L) is preconfigured")
    if url:
        say(f"  Browser UI    : {url}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
