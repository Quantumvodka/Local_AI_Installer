#!/usr/bin/env python3
"""Local AI Installer: detect your PC -> research the best unlocked local AI for it -> install -> open it.

Stdlib only. Works on Windows, macOS and Linux.

    python local_ai_installer.py             # install or update everything, then open the chat
    python local_ai_installer.py --dry-run   # detect + research only, change nothing
    python local_ai_installer.py --check     # health-check (and repair) an existing install
    python local_ai_installer.py --launch    # start everything and open the chat (what the desktop shortcut runs)
    python local_ai_installer.py -y          # no questions
"""
import argparse
import glob
import json
import math
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

# Not every code path uses these, but they are imported so the frozen .exe bundles them: the .exe
# can download a newer copy of this script and run it, and that copy may import any of them.
import base64  # noqa: F401
import hashlib  # noqa: F401
import runpy
import secrets
import signal
import ssl  # noqa: F401
import textwrap  # noqa: F401
import traceback
import uuid  # noqa: F401
import zipfile  # noqa: F401

OS = platform.system()  # Linux / Darwin / Windows
IS_WIN = OS == "Windows"
IS_MAC = OS == "Darwin"
HOME = os.path.expanduser("~")
VERSION = "1.2.0"
REPO = "Quantumvodka/Local_AI_Installer"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/main/local_ai_installer.py"
OLLAMA_API = "http://127.0.0.1:11434"
STATE_DIR = os.path.join(HOME, ".local_ai_installer")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
LOG_FILE = os.path.join(STATE_DIR, "install.log")
WEBUI_DIR = os.path.join(STATE_DIR, "webui")        # private Python environment holding Open WebUI
WEBUI_DATA = os.path.join(STATE_DIR, "webui-data")  # chats + settings: kept across updates
WEBUI_LOG = os.path.join(STATE_DIR, "webui.log")
DEFAULT_PORT = 3210
MANAGED_MARK = "# managed by Local AI Installer"
UPGRADE_MARGIN = 1.10  # only switch models if the new one scores >=10% higher
CPU_CAP_GB = 7.0       # CPU-only PCs: bigger models than this are painfully slow

# ---------------------------------------------------------------- catalog
# Offline fallback, used when live research finds nothing. Tiers are keyed by usable GB of GPU
# VRAM (or unified/system memory share). Each model lists Ollama tags in preference order; the first
# that downloads wins. "Unlocked" = abliterated / Dolphin-style fine-tunes with refusals removed;
# the last entry of each list is a standard model used only if nothing unlocked can be downloaded.
TIERS = [
    # (min_gb, label, coder_tags, chat_tags)
    (0, "tiny (<4 GB)",
     ["huihui_ai/qwen2.5-coder-abliterated:1.5b", "qwen2.5-coder:1.5b"],
     ["huihui_ai/qwen3-abliterated:1.7b", "qwen3:1.7b"]),
    (4, "small (4-6 GB)",
     ["huihui_ai/qwen2.5-coder-abliterated:3b", "qwen2.5-coder:3b"],
     ["huihui_ai/qwen3-abliterated:4b", "qwen3:4b"]),
    (6, "medium (6-10 GB)",
     ["huihui_ai/qwen2.5-coder-abliterated:7b", "qwen2.5-coder:7b"],
     ["huihui_ai/qwen3-abliterated:8b", "dolphin3:8b", "qwen3:8b"]),
    (10, "large (10-16 GB)",
     ["huihui_ai/qwen2.5-coder-abliterated:14b", "qwen2.5-coder:14b"],
     ["huihui_ai/qwen3-abliterated:14b", "dolphin3:8b", "qwen3:14b"]),
    (16, "xlarge (16-28 GB)",
     ["huihui_ai/qwen2.5-coder-abliterated:32b", "qwen2.5-coder:32b"],
     ["huihui_ai/qwen3-abliterated:32b", "huihui_ai/qwen3-abliterated:14b", "qwen3:32b"]),
    (28, "workstation (28+ GB)",
     ["huihui_ai/qwen3-coder-abliterated:30b", "huihui_ai/qwen2.5-coder-abliterated:32b", "qwen3-coder:30b"],
     ["huihui_ai/qwen3-abliterated:32b", "qwen3:32b"]),
]
EMBED_MODEL = "nomic-embed-text"  # for Continue codebase search
AUTOCOMPLETE_TAGS = ["qwen2.5-coder:1.5b-base", "qwen2.5-coder:1.5b"]  # small + fast: typing suggestions
VSCODE_EXTENSION = "Continue.continue"
VSCODE_PLUGINS = [
    (VSCODE_EXTENSION, "Continue: chat / edit / autocomplete in VS Code using your local models"),
]


# ---------------------------------------------------------------- helpers
def init_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # never crash on characters the console can't show
        except Exception:
            pass
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1_000_000:
            os.replace(LOG_FILE, LOG_FILE + ".old")
    except OSError:
        pass


def _log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + str(msg) + "\n")
    except OSError:
        pass


def say(msg=""):
    try:
        print(msg, flush=True)
    except Exception:
        try:
            print(str(msg).encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass
    _log(msg)


def step(n, title):
    say(f"\n=== [{n}/4] {title} ===")


def have(cmd):
    return shutil.which(cmd) is not None


def confirm(q, assume_yes, default=True):
    if assume_yes:
        return True
    try:
        ans = input(f"{q} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    except EOFError:  # no terminal attached: take the default
        return default
    if not ans:
        return default
    return ans in ("y", "yes")


def vtuple(v):
    nums = [int(x) for x in re.findall(r"\d+", str(v))[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def ps_env(extra=None):
    """Environment for launching Windows PowerShell. A PSModulePath inherited from PowerShell 7 stops 5.1 loading
    its built-in modules, so drop it and let Windows PowerShell compute its own."""
    env = dict(os.environ)
    env.pop("PSModulePath", None)
    env.update(extra or {})
    return env


def run(cmd, timeout=60, env=None):
    """Run a command quietly; return its stdout if it succeeded, else ''."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, env=env)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def run_stream(cmd, env=None):
    """Run a command, echoing its output live. Returns (returncode, full_output)."""
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True,
                             encoding="utf-8", errors="replace", bufsize=1)
    except OSError as e:
        say(f"  could not run {cmd[0]}: {e}")
        return 1, ""
    lines = []
    for line in p.stdout:
        line = line.rstrip()
        lines.append(line)
        print(line, flush=True)
    p.wait()
    _log("\n".join(lines[-40:]))
    return p.returncode, "\n".join(lines)


def detached_kwargs():
    if IS_WIN:
        return {"creationflags": 0x00000008 | 0x00000200}  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return {"start_new_session": True}


def http_open(url, timeout=15, data=None, headers=None):
    h = {"User-Agent": "local-ai-installer/" + VERSION}
    h.update(headers or {})
    return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=timeout)


def http_json(url, timeout=15):
    with http_open(url, timeout) as r:
        return json.load(r)


def fetch_text(url, timeout=15):
    with http_open(url, timeout) as r:
        return r.read().decode("utf-8", "replace")


def download(url, dest, label=None):
    say(f"Downloading {label or url} ...")
    with http_open(url, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, mark = 0, 20
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total and done * 100 // total >= mark:
                say(f"  {done * 100 // total}%")
                mark += 20


# ---------------------------------------------------------------- 1. detect
def win_ram_gb():
    import ctypes

    class MemStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]

    s = MemStatus()
    s.dwLength = ctypes.sizeof(MemStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return s.ullTotalPhys / 1024**3


def ram_gb():
    try:
        if OS == "Linux":
            with open("/proc/meminfo") as f:
                m = re.search(r"MemTotal:\s+(\d+)", f.read())
            return int(m.group(1)) / 1024 / 1024 if m else 0.0
        if IS_MAC:
            return int(run(["sysctl", "-n", "hw.memsize"]) or 0) / 1024**3
        return win_ram_gb()
    except (OSError, ValueError, AttributeError):
        return 0.0


def cpu_name():
    name = ""
    try:
        if OS == "Linux":
            with open("/proc/cpuinfo") as f:
                m = re.search(r"model name\s*:\s*(.+)", f.read())
            name = m.group(1) if m else ""
        elif IS_MAC:
            name = run(["sysctl", "-n", "machdep.cpu.brand_string"])
        else:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                name = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except (OSError, ImportError):
        pass
    return name or platform.processor() or platform.machine()


def gpu_entry(name, vram_gb):
    low = name.lower()
    vendor = ("nvidia" if "nvidia" in low else "amd" if ("amd" in low or "radeon" in low)
              else "intel" if "intel" in low else "other")
    return {"name": name.strip(), "vram_gb": float(vram_gb), "vendor": vendor}


def parse_nvidia(out):
    gpus = []
    for line in out.splitlines():
        name, _, mem = line.rpartition(",")
        try:
            gpus.append({"name": name.strip(), "vram_gb": float(mem) / 1024, "vendor": "nvidia"})
        except ValueError:
            continue
    return gpus


def windows_gpus():
    """Real VRAM from the registry (WMI's AdapterRAM caps at 4 GB)."""
    import winreg
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    gpus = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return gpus
    i = 0
    while True:
        try:
            sub = winreg.EnumKey(root, i)
        except OSError:
            break
        i += 1
        try:
            with winreg.OpenKey(root, sub) as k:
                name = winreg.QueryValueEx(k, "DriverDesc")[0]
                mem = 0
                for value in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                    try:
                        mem = winreg.QueryValueEx(k, value)[0]
                        break
                    except OSError:
                        continue
        except OSError:
            continue
        if isinstance(mem, (bytes, bytearray)):
            mem = int.from_bytes(mem[:8], "little")
        if "microsoft" in name.lower():  # Basic Display / Remote Display adapters
            continue
        gpus.append(gpu_entry(name, (int(mem) / 1024**3) if mem else 0.0))
    return gpus


def linux_amd_gpus():
    gpus = []
    for p in sorted(glob.glob("/sys/class/drm/card[0-9]*/device/mem_info_vram_total")):
        try:
            with open(p) as f:
                total = int(f.read().strip()) / 1024**3
        except (OSError, ValueError):
            continue
        gpus.append({"name": "AMD GPU", "vram_gb": total, "vendor": "amd"})
    return gpus


def detect_gpus():
    """Return list of dicts: {name, vram_gb, vendor}."""
    gpus = []
    if have("nvidia-smi"):
        gpus = parse_nvidia(run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]))
    if not gpus and IS_WIN:
        try:
            gpus = windows_gpus()
        except ImportError:
            gpus = []
    if not gpus and OS == "Linux":
        gpus = linux_amd_gpus()
        if not gpus and have("lspci"):
            for line in run(["lspci"]).splitlines():
                if re.search(r"VGA|3D", line) and re.search(r"AMD|Radeon|NVIDIA|Intel", line):
                    gpus.append(gpu_entry(line.split(": ", 1)[-1], 0))
    return gpus


def detect(max_gb=None):
    apple_silicon = IS_MAC and platform.machine() == "arm64"
    ram = ram_gb()
    cpu = cpu_name()
    gpus = detect_gpus()
    if apple_silicon:
        usable, mode = ram * 0.70, "Apple unified memory (Metal)"
        gpus = [{"name": cpu + " (integrated GPU, shares RAM)", "vram_gb": usable, "vendor": "apple"}]
    else:
        best_vram = max((g["vram_gb"] for g in gpus), default=0)
        if best_vram >= 4:
            usable, mode = best_vram, "dedicated GPU VRAM"
        else:
            usable, mode = min(ram * 0.50, CPU_CAP_GB), "CPU + system RAM (slower, so model size is capped)"
    if max_gb and max_gb < usable:
        usable, mode = max_gb, mode + f", limited to {max_gb} GB by --max-gb"
    try:
        disk = shutil.disk_usage(HOME).free / 1024**3
    except OSError:
        disk = 0.0
    return {
        "os": f"{OS} {platform.release()}", "arch": platform.machine(),
        "cpu": cpu, "cores": os.cpu_count(), "ram_gb": round(ram, 1),
        "gpus": gpus, "usable_gb": round(usable, 1), "mode": mode,
        "disk_free_gb": round(disk, 1),
    }


# ---------------------------------------------------------------- 2. research
# Live research: query Hugging Face for GGUF models, read the real file sizes, keep only quants
# that fit THIS machine, then rank by capability / recency / popularity.
HF = "https://huggingface.co/api/models"
UNLOCK_TERMS = ["abliterated", "uncensored", "dolphin", "heretic", "josiefied", "unfiltered"]
BAD_NAME = re.compile(r"(?<![a-z0-9])(lora|draft|embed\w*|rerank\w*|mmproj|guard|tts|whisper|awq|gptq|mlx|base|"
                      r"encoders?|decoders?|images?|flux\d*|sdxl|diffusion|vae|clip|siglip)(?![a-z0-9])", re.I)
CODER_RE = re.compile(r"(?<![a-z])(coder|coding|code|codestral|codellama|codegemma|starcoder\d*|devstral)(?![a-z])")
# quant label -> quality factor (relative to full precision) and nominal bits per weight
QUANTS = {"Q8_0": 1.0, "Q6_K_L": 0.995, "Q6_K": 0.99, "Q5_K_L": 0.975, "Q5_K_M": 0.97, "Q5_K_S": 0.96,
          "Q4_K_L": 0.94, "Q4_K_M": 0.93, "Q4_K_S": 0.91, "IQ4_XS": 0.90, "Q4_0": 0.88,
          "Q3_K_L": 0.82, "Q3_K_M": 0.80}
BITS = {"Q8_0": 8.5, "Q6_K_L": 6.6, "Q6_K": 6.6, "Q5_K_L": 5.7, "Q5_K_M": 5.7, "Q5_K_S": 5.5,
        "Q4_K_L": 4.9, "Q4_K_M": 4.85, "Q4_K_S": 4.6, "IQ4_XS": 4.3, "Q4_0": 4.5, "Q3_K_L": 4.1, "Q3_K_M": 3.9}
QUANT_RE = re.compile(r"[-._](" + "|".join(sorted(QUANTS, key=len, reverse=True)) + r")\.gguf$", re.I)
MAX_AGE_MONTHS = 20
UNKNOWN_AGE = 12.0


def months_old(iso):
    """Age in months, or None if unknown."""
    try:
        t = time.mktime(time.strptime(iso[:10], "%Y-%m-%d"))
    except (ValueError, TypeError):
        return None
    return max(0.0, (time.time() - t) / (30.4 * 86400))


def param_b(name):
    """Parameter count in billions guessed from a repo name ('Qwen3-14B' -> 14), or None."""
    n = name.lower()
    m = re.search(r"(?<![a-z0-9.])(\d+)x(\d+(?:\.\d+)?)b(?![a-z0-9])", n)
    if m:
        return int(m.group(1)) * float(m.group(2))
    m = re.search(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)b(?![a-z0-9])", n)
    return float(m.group(1)) if m else None


def best_quant(files, budget_gb):
    """files: [(filename, size_gb)]. Return (quant, size_gb) of the largest single-file quant within budget."""
    fits = []
    for name, size in files:
        m = QUANT_RE.search(name)
        if m and "-of-" not in name and 0 < size <= budget_gb:  # skip split GGUFs
            fits.append((size, QUANTS[m.group(1).upper()], m.group(1).upper()))
    if not fits:
        return None
    fits.sort()
    size, _, quant = fits[-1]
    return quant, size


def popularity(downloads, likes):
    return 0.5 + 0.5 * min(1.0, math.log10(downloads + likes * 20 + 1) / 5.5)


def score(params_b, quant, downloads, likes, age_months):
    capability = params_b * QUANTS[quant]            # more parameters (at decent quality) ~ smarter
    recency = 0.5 ** (age_months / 9.0)              # halves every 9 months
    return capability * recency * popularity(downloads, likes)


def search_models(terms, limit=100):
    """Merge Hugging Face GGUF search results for each term (most downloaded first)."""
    seen = {}
    for t in terms:
        base = (f"{HF}?search={urllib.parse.quote(t)}&filter=gguf&sort=downloads&direction=-1&limit={limit}")
        for extra in ("&expand[]=downloads&expand[]=likes&expand[]=createdAt&expand[]=lastModified", ""):
            try:
                for m in http_json(base + extra):
                    seen[m["id"]] = m
                break
            except Exception:
                continue
    return list(seen.values())


def rank_candidates(models, budget_gb, want, detail=None, top_n=12, stats=None):
    """want: 'coder' or 'chat'. Returns up to 5 scored candidates, best first."""
    detail = detail or http_json
    pre = []
    for m in models:
        rid = m.get("id") or ""
        low = rid.lower()
        if not rid or BAD_NAME.search(rid) or not any(t in low for t in UNLOCK_TERMS):
            continue
        if bool(CODER_RE.search(low)) != (want == "coder"):
            continue
        age = months_old(m.get("createdAt") or m.get("lastModified"))
        if age is not None and age > MAX_AGE_MONTHS:
            continue
        p = param_b(rid)
        est = p * 0.62 if p else budget_gb * 0.5           # ~GB at Q4
        if est > budget_gb * 1.15:
            continue
        a = UNKNOWN_AGE if age is None else age
        proxy = est * 0.5 ** (a / 9.0) * popularity(m.get("downloads") or 0, m.get("likes") or 0)
        pre.append((proxy, m, a, p, age))
    pre.sort(key=lambda x: -x[0])
    if stats is not None:
        stats[want + "_prefiltered"] = len(pre)
    out = []
    for _, m, a, p, age in pre[:top_n]:
        rid = m["id"]
        try:
            info = detail(f"{HF}/{rid}?blobs=true")
        except Exception:
            continue
        files = []
        for f in info.get("siblings") or []:
            fn = f.get("rfilename", "")
            if fn.lower().endswith(".gguf"):
                size = f.get("size") or (f.get("lfs") or {}).get("size") or 0
                files.append((fn, size / 1024**3))
        bq = best_quant(files, budget_gb)
        if not bq:
            continue
        quant, size = bq
        params = p or size * 8 / BITS[quant]
        dl, likes = m.get("downloads") or 0, m.get("likes") or 0
        out.append({"repo": rid, "quant": quant, "size_gb": round(size, 1),
                    "age_months": None if age is None else round(age, 1),
                    "downloads": dl, "likes": likes, "score": score(params, quant, dl, likes, a),
                    "tag": f"hf.co/{rid}:{quant}"})
    out.sort(key=lambda c: -c["score"])
    if stats is not None:
        stats[want + "_ranked"] = len(out)
    return out[:5]


def research(spec, stats=None):
    """Live Hugging Face research. Returns {'coder': [...], 'chat': [...]} (lists may be empty)."""
    budget = spec["usable_gb"] * 0.85  # headroom for context / KV cache
    pool = search_models(UNLOCK_TERMS)
    if stats is not None:
        stats["search_results"] = len(pool)
    return {"chat": rank_candidates(pool, budget, "chat", stats=stats),
            "coder": rank_candidates(pool, budget, "coder", stats=stats)}


def recommend(spec):
    """Offline fallback: curated tiers."""
    tier = TIERS[0]
    for t in TIERS:
        if spec["usable_gb"] >= t[0]:
            tier = t
    return {"tier": tier[1], "coder": tier[2], "chat": tier[3]}


def is_unlocked(tag):
    return any(t in tag.lower() for t in UNLOCK_TERMS)


def show(cands):
    for i, c in enumerate(cands, 1):
        age = "?" if c["age_months"] is None else c["age_months"]
        say(f"  {i}. {c['repo']}  [{c['quant']}, {c['size_gb']} GB, {age} months old, {c['downloads']:,} downloads]")


def est_size_gb(tag, cands):
    """Download size estimate: exact for live candidates, ~Q4 (0.6 GB per billion params) for curated tags."""
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
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        state["installer_version"] = VERSION
        state["updated"] = time.strftime("%Y-%m-%d")
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError as e:
        say(f"  (could not save settings: {e})")


def maybe_self_update(argv):
    """Frozen .exe / binary only: if a newer script is on GitHub, download it and run that instead."""
    if not getattr(sys, "frozen", False) or os.environ.get("LAI_NO_SELF_UPDATE") or "--no-self-update" in argv:
        return
    try:
        src = fetch_text(RAW_URL, timeout=6)
    except Exception:
        return  # offline: just use the bundled version
    m = re.search(r'^VERSION = "([^"]+)"', src, re.M)
    if not m or vtuple(m.group(1)) <= vtuple(VERSION):
        return
    try:
        folder = os.path.join(STATE_DIR, "latest")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "local_ai_installer.py")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
    except OSError:
        return
    say(f"A newer installer (v{m.group(1)}) was found - using it instead of the bundled v{VERSION}.")
    os.environ["LAI_NO_SELF_UPDATE"] = "1"
    old_argv0 = sys.argv[0]
    sys.argv[0] = path
    try:
        runpy.run_path(path, run_name="__main__")  # ends with sys.exit(), which passes through
    except ImportError as e:
        say(f"  could not use the newer version ({e}); continuing with the bundled one.")
        sys.argv[0] = old_argv0
        return
    sys.exit(0)


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


# ---------------------------------------------------------------- 3a. Ollama (the engine)
def find_ollama():
    path = shutil.which("ollama")
    if path:
        return path
    if IS_WIN:
        local, pf = os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")
        cands = [os.path.join(local, "Programs", "Ollama", "ollama.exe"), os.path.join(pf, "Ollama", "ollama.exe")]
    elif IS_MAC:
        cands = ["/opt/homebrew/bin/ollama", "/usr/local/bin/ollama",
                 os.path.join(HOME, "Applications", "Ollama.app", "Contents", "Resources", "ollama"),
                 "/Applications/Ollama.app/Contents/Resources/ollama"]
    else:
        cands = ["/usr/local/bin/ollama", "/usr/bin/ollama", os.path.join(HOME, ".local", "bin", "ollama")]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def ollama_bin():
    return find_ollama() or "ollama"


def ollama_up():
    try:
        with http_open(OLLAMA_API + "/api/version", timeout=3):
            return True
    except Exception:
        return False


def installed_models():
    try:
        return [m.get("name", "") for m in http_json(OLLAMA_API + "/api/tags", timeout=10).get("models", [])]
    except Exception:
        return []


def has_model(tag, names):
    t = tag.lower()
    return any(n.lower() in (t, t + ":latest") for n in names)


def install_ollama():
    if find_ollama():
        say("Ollama (the AI engine) is already installed.")
        return True
    say("Installing Ollama (the AI engine)...")
    try:
        if OS == "Linux":
            script = os.path.join(tempfile.gettempdir(), "ollama-install.sh")
            download("https://ollama.com/install.sh", script, "the official Ollama installer")
            subprocess.call(["sh", script])
        elif IS_MAC:
            if have("brew"):
                subprocess.call(["brew", "install", "ollama"])
            if not find_ollama():
                zip_path = os.path.join(tempfile.gettempdir(), "Ollama-darwin.zip")
                download("https://ollama.com/download/Ollama-darwin.zip", zip_path, "Ollama for Mac")
                dest = os.path.join(HOME, "Applications")
                os.makedirs(dest, exist_ok=True)
                subprocess.call(["unzip", "-q", "-o", zip_path, "-d", dest])
        else:
            if have("winget"):
                subprocess.call(["winget", "install", "-e", "--id", "Ollama.Ollama", "--silent",
                                 "--accept-source-agreements", "--accept-package-agreements"])
            if not find_ollama():
                setup = os.path.join(tempfile.gettempdir(), "OllamaSetup.exe")
                download("https://ollama.com/download/OllamaSetup.exe", setup, "the official Ollama installer")
                subprocess.call([setup, "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"])
    except Exception as e:
        say(f"  Ollama install problem: {e}")
    if not find_ollama():
        say("Could not install Ollama automatically. Install it from https://ollama.com/download and run this again.")
        return False
    return True


def ollama_version():
    try:
        return http_json(OLLAMA_API + "/api/version", timeout=3).get("version", "")
    except Exception:
        return ""


def upgrade_ollama():
    """The Windows/Mac apps update themselves; Linux and Homebrew need a nudge (only if a newer version exists)."""
    say("Checking for an Ollama update...")
    try:
        if OS == "Linux":
            latest = http_json("https://api.github.com/repos/ollama/ollama/releases/latest", timeout=10).get("tag_name", "")
            current = ollama_version()
            if not latest or not current or vtuple(latest) <= vtuple(current):
                say(f"  Ollama {current or '?'} is up to date.")
                return
            say(f"  Updating Ollama {current} -> {latest} ...")
            script = os.path.join(tempfile.gettempdir(), "ollama-install.sh")
            download("https://ollama.com/install.sh", script, "the official Ollama installer")
            subprocess.call(["sh", script])
        elif IS_MAC and have("brew"):
            subprocess.call(["brew", "upgrade", "ollama"])
        elif IS_WIN and have("winget"):
            subprocess.call(["winget", "upgrade", "-e", "--id", "Ollama.Ollama", "--silent",
                             "--accept-source-agreements", "--accept-package-agreements"])
    except Exception as e:
        say(f"  (skipped Ollama update: {e})")


def start_ollama(wait=90):
    if ollama_up():
        return True
    if IS_WIN or (OS == "Linux" and os.path.isdir("/run/systemd/system")):
        for _ in range(10):  # the installed tray app / system service starts its own server; give it a moment
            time.sleep(1)
            if ollama_up():
                return True
    say("Starting the Ollama engine...")
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, "ollama.log"), "ab") as lf:
            subprocess.Popen([ollama_bin(), "serve"], stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
                             env=os.environ.copy(), **detached_kwargs())
    except OSError as e:
        say(f"  could not start Ollama: {e}")
        return False
    for _ in range(wait):
        if ollama_up():
            return True
        time.sleep(1)
    say(f"  Ollama did not start. Details: {os.path.join(STATE_DIR, 'ollama.log')}")
    return False


def pull_first(tags):
    for tag in tags:
        say(f"Downloading model {tag} ...")
        if subprocess.call([ollama_bin(), "pull", tag]) == 0:
            return tag
        say(f"  {tag} could not be downloaded, trying the next option...")
    return None


CODE_PROMPT = "Write a Python one-liner that reverses a string. Code only."
CHAT_PROMPT = "Say hello in one short sentence."


def pull_working(tags, prompt, label):
    """Download the first model that both downloads AND actually runs here (new architectures may not)."""
    for tag in tags:
        say(f"Downloading model {tag} ...")
        if subprocess.call([ollama_bin(), "pull", tag]) != 0:
            say(f"  {tag} could not be downloaded, trying the next option...")
            continue
        if verify(tag, prompt, label):
            return tag
        say(f"  {tag} downloaded but does not run on this PC / Ollama version; removing it and trying the next option...")
        subprocess.call([ollama_bin(), "rm", tag])
    return None


def verify(model, prompt, label):
    say(f"Testing the {label} ({model}) - the first load can take a minute or two...")
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "options": {"num_predict": 48}}).encode()
    try:
        with http_open(OLLAMA_API + "/api/generate", timeout=900, data=body,
                       headers={"Content-Type": "application/json"}) as r:
            data = json.load(r)
    except Exception as e:
        say(f"  test failed: {e}")
        return False
    reply = (data.get("response") or data.get("thinking") or "").strip()
    say("  it replied: " + reply[:120].replace("\n", " "))
    return bool(reply)


# ---------------------------------------------------------------- 3b. VS Code + Continue (coding plug-in)
def find_code():
    path = shutil.which("code")
    if path:
        return path
    if IS_WIN:
        local, pf = os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")
        cands = [os.path.join(local, "Programs", "Microsoft VS Code", "bin", "code.cmd"),
                 os.path.join(pf, "Microsoft VS Code", "bin", "code.cmd")]
    elif IS_MAC:
        cands = ["/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
                 os.path.join(HOME, "Applications", "Visual Studio Code.app", "Contents", "Resources", "app", "bin", "code")]
    else:
        cands = ["/usr/bin/code", "/snap/bin/code", "/usr/local/bin/code"]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def install_vscode():
    say("Installing Visual Studio Code...")
    if IS_WIN and have("winget"):
        subprocess.call(["winget", "install", "-e", "--id", "Microsoft.VisualStudioCode", "--silent",
                         "--accept-source-agreements", "--accept-package-agreements"])
    elif IS_MAC and have("brew"):
        subprocess.call(["brew", "install", "--cask", "visual-studio-code"])
    else:
        say("  Please install VS Code from https://code.visualstudio.com and run this installer again.")
        return None
    return find_code()


def has_extension(code, ext):
    return ext.lower() in [x.strip().lower() for x in run([code, "--list-extensions"], timeout=90).splitlines()]


def install_vscode_plugins(code):
    ok = True
    for ext, desc in VSCODE_PLUGINS:
        say(f"Installing VS Code extension {ext} ({desc})")
        if subprocess.call([code, "--install-extension", ext, "--force"]) != 0:
            ok = False
    return ok


def continue_config_path():
    return os.path.join(HOME, ".continue", "config.yaml")


def write_continue_config(coder, chat, auto):
    path = continue_config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                if MANAGED_MARK not in f.read():
                    say(f"Your Continue config was edited by you, so it was left alone. Point it at: "
                        f"coder={coder} chat={chat} autocomplete={auto}")
                    return False
            shutil.copy(path, path + ".bak")
        auto_block = (f'  - name: Local Autocomplete\n    provider: ollama\n    model: "{auto}"\n'
                      f'    roles: [autocomplete]\n') if auto else ""
        coder_roles = "[chat, edit, apply]" if auto else "[chat, edit, apply, autocomplete]"
        cfg = (f"{MANAGED_MARK} - delete this line to stop automatic updates of this file\n"
               f"name: Local AI\nversion: 1.0.1\nschema: v1\nmodels:\n"
               f'  - name: Local Chat (unlocked)\n    provider: ollama\n    model: "{chat}"\n    roles: [chat]\n'
               f'  - name: Local Coder\n    provider: ollama\n    model: "{coder}"\n    roles: {coder_roles}\n'
               f"{auto_block}"
               f'  - name: Embeddings\n    provider: ollama\n    model: "{EMBED_MODEL}"\n    roles: [embed]\n')
        with open(path, "w", encoding="utf-8") as f:
            f.write(cfg)
    except OSError as e:
        say(f"  could not write the Continue config: {e}")
        return False
    say(f"Wrote Continue config -> {path}")
    return True


def continue_config_ok(coder, chat):
    try:
        with open(continue_config_path(), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    return coder in text and chat in text


# ---------------------------------------------------------------- 3c. Open WebUI (chat in the browser)
def uv_bin():
    exe = "uv.exe" if IS_WIN else "uv"
    for p in (os.path.join(STATE_DIR, "uv", exe), shutil.which("uv"),
              os.path.join(HOME, ".local", "bin", exe), os.path.join(HOME, ".cargo", "bin", exe)):
        if p and os.path.isfile(p):
            return p
    return None


def uv_asset():
    """Name of the prebuilt uv archive for this OS / CPU, or None."""
    machine = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(
        platform.machine().lower(), platform.machine().lower())
    return {
        ("Windows", "x86_64"): "uv-x86_64-pc-windows-msvc.zip",
        ("Windows", "aarch64"): "uv-aarch64-pc-windows-msvc.zip",
        ("Darwin", "x86_64"): "uv-x86_64-apple-darwin.tar.gz",
        ("Darwin", "aarch64"): "uv-aarch64-apple-darwin.tar.gz",
        ("Linux", "x86_64"): "uv-x86_64-unknown-linux-gnu.tar.gz",
        ("Linux", "aarch64"): "uv-aarch64-unknown-linux-gnu.tar.gz",
    }.get((OS, machine))


def install_uv():
    """Download uv straight from its GitHub release (no PowerShell / shell script), verify it, unpack it."""
    if uv_bin():
        return True
    asset = uv_asset()
    if not asset:
        say(f"  There is no prebuilt uv for {OS} / {platform.machine()}.")
        return False
    say("Installing uv (a small tool that fetches Python for Open WebUI; nothing system-wide changes)...")
    url = f"https://github.com/astral-sh/uv/releases/latest/download/{asset}"
    work = tempfile.mkdtemp(prefix="lai-uv-")
    try:
        arc = os.path.join(work, asset)
        download(url, arc, "uv")
        want = None
        try:
            want = fetch_text(url + ".sha256", timeout=20).split()[0].lower()
        except Exception:
            _log("uv checksum file unavailable; continuing without verifying it")
        if want:
            with open(arc, "rb") as f:
                got = hashlib.sha256(f.read()).hexdigest()
            if got != want:
                say("  The uv download failed its checksum, so it was not used. Run this again.")
                return False
        if asset.endswith(".zip"):
            with zipfile.ZipFile(arc) as z:
                z.extractall(work)
        else:
            with tarfile.open(arc) as t:
                try:
                    t.extractall(work, filter="data")
                except TypeError:  # older Python without extraction filters
                    t.extractall(work)
        dest = os.path.join(STATE_DIR, "uv")
        os.makedirs(dest, exist_ok=True)
        for folder, _, names in os.walk(work):
            for name in names:
                if name in ("uv", "uv.exe", "uvx", "uvx.exe"):
                    target = os.path.join(dest, name)
                    shutil.copy2(os.path.join(folder, name), target)
                    if not IS_WIN:
                        os.chmod(target, 0o755)
    except Exception as e:
        say(f"  uv install problem: {e}")
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return uv_bin() is not None


def webui_paths():
    bindir = os.path.join(WEBUI_DIR, "Scripts" if IS_WIN else "bin")
    return (os.path.join(bindir, "python.exe" if IS_WIN else "python"),
            os.path.join(bindir, "open-webui.exe" if IS_WIN else "open-webui"))


def install_webui_pkg():
    """Create the private Python env and install/upgrade open-webui. Returns (ok, changed)."""
    uv = uv_bin()
    py, exe = webui_paths()
    if not os.path.exists(os.path.join(WEBUI_DIR, "pyvenv.cfg")):
        say("Setting up a private Python 3.11 for Open WebUI...")
        rc, _ = run_stream([uv, "venv", "--python", "3.11", WEBUI_DIR])
        if rc != 0:
            return False, False
    say("Installing / updating Open WebUI (a few minutes the first time; several GB)...")
    rc, out = run_stream([uv, "pip", "install", "--python", py, "-U", "open-webui"])
    if rc != 0 or not os.path.exists(exe):
        return False, False
    return True, ("Installed" in out or "Uninstalled" in out)


def webui_health(port):
    try:
        with http_open(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            return r.status == 200 and b"status" in r.read(300)
    except Exception:
        return False


def port_free(port):
    s = socket.socket()
    try:
        if not IS_WIN:  # lets us reuse a port whose old connections are still winding down (TIME_WAIT)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def choose_port(state):
    pref = state.get("webui_port", DEFAULT_PORT)
    if webui_health(pref):
        return pref
    for p in range(pref, pref + 20):
        if port_free(p):
            return p
    return pref


def webui_secret():
    path = os.path.join(WEBUI_DATA, ".installer_secret")
    try:
        with open(path, encoding="utf-8") as f:
            s = f.read().strip()
            if s:
                return s
    except OSError:
        pass
    s = secrets.token_hex(32)
    try:
        os.makedirs(WEBUI_DATA, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(s)
        if not IS_WIN:
            os.chmod(path, 0o600)
    except OSError:
        pass
    return s


def webui_env(chat_tag=None):
    env = dict(os.environ)
    env.update(DATA_DIR=WEBUI_DATA, OLLAMA_BASE_URL=OLLAMA_API, WEBUI_SECRET_KEY=webui_secret(),
               SCARF_NO_ANALYTICS="true", DO_NOT_TRACK="true", ANONYMIZED_TELEMETRY="false",
               HF_HUB_DISABLE_TELEMETRY="1", PYTHONUTF8="1")
    if chat_tag:
        env["DEFAULT_MODELS"] = chat_tag
    return env


def start_webui(state, port, chat_tag, wait=900):
    """Start Open WebUI in the background (localhost only) and wait until it answers."""
    _, exe = webui_paths()
    if not os.path.exists(exe):
        return False
    os.makedirs(WEBUI_DATA, exist_ok=True)
    with open(WEBUI_LOG, "ab") as lf:
        proc = subprocess.Popen([exe, "serve", "--host", "127.0.0.1", "--port", str(port)],
                                env=webui_env(chat_tag), cwd=WEBUI_DATA, stdout=lf, stderr=lf,
                                stdin=subprocess.DEVNULL, **detached_kwargs())
    state["webui_pid"] = proc.pid
    save_state(state)
    t0 = last = time.time()
    while time.time() - t0 < wait:
        if webui_health(port):
            return True
        if proc.poll() is not None:
            say("  Open WebUI stopped unexpectedly. Last lines of its log:")
            try:
                with open(WEBUI_LOG, encoding="utf-8", errors="replace") as f:
                    for line in f.read().splitlines()[-12:]:
                        say("    " + line)
            except OSError:
                pass
            return False
        if time.time() - last > 30:
            say(f"  ...still starting ({int(time.time() - t0)}s; the first start downloads a few files)")
            last = time.time()
        time.sleep(2)
    say(f"  Open WebUI is taking too long. Details: {WEBUI_LOG}")
    return False


def stop_webui(state, port):
    """Stop the Open WebUI process we started earlier (only if its command line really is Open WebUI)."""
    pid = state.get("webui_pid")
    if not pid:
        return False
    try:
        pid = int(pid)
        if IS_WIN:
            cmd = run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=20)
        else:
            cmd = run(["ps", "-p", str(pid), "-o", "command="])
        if "open-webui" not in cmd.lower():
            return False
        if IS_WIN:
            subprocess.call(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        return False
    for _ in range(20):
        if not webui_health(port):
            return True
        time.sleep(1)
    return False


def setup_webui(state, chat_tag):
    """Install or update Open WebUI (no Docker needed) and make sure it is running. Returns its URL or None."""
    if not install_uv():
        say("Could not install uv, which Open WebUI needs. Check your internet connection and run this again.")
        return None
    old_port = state.get("webui_port", DEFAULT_PORT)
    if webui_health(old_port):
        say("Pausing Open WebUI while it updates...")
        if not stop_webui(state, old_port):
            say("  (could not stop it automatically; if the update fails, restart your PC and run this again)")
    ok, _ = install_webui_pkg()
    if not ok:
        say("Open WebUI could not be installed - see the messages above.")
        return None
    port = choose_port(state)
    state["webui_port"] = port
    say("Starting Open WebUI (first start can take a couple of minutes)...")
    if not webui_health(port) and not start_webui(state, port, chat_tag):
        return None
    return f"http://localhost:{port}"


# ---------------------------------------------------------------- 3d. opening things + shortcuts
def open_url(url):
    say(f"Opening {url} in your browser...")
    try:
        ok = webbrowser.open(url)
    except Exception:
        ok = False
    if not ok:
        say(f"  (couldn't open a browser automatically - open this address yourself: {url})")


def open_terminal_chat(model):
    """Fallback when the browser chat isn't available: a new terminal window chatting with the model."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        ob = ollama_bin()
        if IS_WIN:
            path = os.path.join(STATE_DIR, "chat.bat")
            with open(path, "w", newline="") as f:
                f.write(f'@echo off\r\ntitle Local AI chat\r\n"{ob}" run "{model}"\r\npause\r\n')
            os.startfile(path)  # noqa: B606
        elif IS_MAC:
            path = os.path.join(STATE_DIR, "chat.command")
            with open(path, "w") as f:
                f.write(f'#!/bin/sh\n"{ob}" run "{model}"\n')
            os.chmod(path, 0o755)
            subprocess.Popen(["open", path])
        else:
            path = os.path.join(STATE_DIR, "chat.sh")
            with open(path, "w") as f:
                f.write(f'#!/bin/sh\n"{ob}" run "{model}"\n')
            os.chmod(path, 0o755)
            for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"], ["konsole", "-e"],
                         ["xfce4-terminal", "-e"], ["xterm", "-e"]):
                if have(term[0]):
                    subprocess.Popen(term + ["sh", path])
                    return
            say(f"  Open a terminal and run:  ollama run {model}")
    except OSError as e:
        say(f"  couldn't open a terminal chat ({e}). Run this yourself:  ollama run {model}")


def install_self_copy():
    """Keep a copy of this installer in the settings folder so the shortcut can start everything later."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        if getattr(sys, "frozen", False):
            src, dest = sys.executable, os.path.join(STATE_DIR, "LocalAIInstaller" + (".exe" if IS_WIN else ""))
        else:
            src, dest = os.path.abspath(__file__), os.path.join(STATE_DIR, "local_ai_installer.py")
        if os.path.abspath(src) != os.path.abspath(dest):
            shutil.copy2(src, dest)
        if not IS_WIN:
            os.chmod(dest, 0o755)
        return dest
    except OSError as e:
        say(f"  (couldn't save a launcher copy: {e})")
        return None


def launcher_cmd(copy):
    if getattr(sys, "frozen", False):
        return [copy, "--launch"]
    py = webui_paths()[0]
    return [py if os.path.exists(py) else sys.executable, copy, "--launch"]


def special_folder(name):
    """Desktop / Start-menu folder of the current user (follows OneDrive redirection) via the Windows API."""
    import ctypes
    csidl = {"Desktop": 0x10, "Programs": 0x02}[name]
    buf = ctypes.create_unicode_buffer(520)
    try:
        ctypes.windll.shell32.SHGetFolderPathW(0, csidl, 0, 0, buf)
    except (OSError, AttributeError):
        return None
    return buf.value or None


def make_windows_shortcut(path, cmd):
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LAI_LNK);"
          "$s.TargetPath=$env:LAI_TARGET;$s.Arguments=$env:LAI_ARGS;$s.WorkingDirectory=$env:LAI_WD;"
          "$s.Description='Start Local AI and open the chat';$s.Save()")
    env = ps_env(dict(LAI_LNK=path, LAI_TARGET=cmd[0], LAI_ARGS=subprocess.list2cmdline(cmd[1:]), LAI_WD=STATE_DIR))
    try:
        subprocess.call(["powershell", "-NoProfile", "-Command", ps], env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return os.path.exists(path)


def make_shortcuts(cmd):
    """Create 'Local AI Chat' on the Desktop (and Start menu / app menu). Returns the paths created."""
    made = []
    try:
        if IS_WIN:
            for folder in (special_folder("Desktop"), special_folder("Programs")):
                if folder and os.path.isdir(folder):
                    p = os.path.join(folder, "Local AI Chat.lnk")
                    if make_windows_shortcut(p, cmd):
                        made.append(p)
                    else:  # no PowerShell / COM: a plain batch file does the same job
                        p = os.path.join(folder, "Local AI Chat.bat")
                        with open(p, "w", newline="") as f:
                            f.write("@echo off\r\n" + subprocess.list2cmdline(cmd) + "\r\n")
                        made.append(p)
        elif IS_MAC:
            desk = os.path.join(HOME, "Desktop")
            if os.path.isdir(desk):
                p = os.path.join(desk, "Local AI Chat.command")
                with open(p, "w") as f:
                    f.write("#!/bin/sh\n" + " ".join('"%s"' % c for c in cmd) + "\n")
                os.chmod(p, 0o755)
                made.append(p)
        else:
            entry = ("[Desktop Entry]\nType=Application\nName=Local AI Chat\n"
                     "Comment=Start Local AI and open the chat\nTerminal=true\nIcon=utilities-terminal\n"
                     "Exec=" + " ".join('"%s"' % c for c in cmd) + "\n")
            desk = run(["xdg-user-dir", "DESKTOP"]) if have("xdg-user-dir") else ""
            for folder in (desk or os.path.join(HOME, "Desktop"), os.path.join(HOME, ".local", "share", "applications")):
                if os.path.isdir(folder):
                    p = os.path.join(folder, "Local AI Chat.desktop")
                    with open(p, "w") as f:
                        f.write(entry)
                    os.chmod(p, 0o755)
                    made.append(p)
    except OSError as e:
        say(f"  (couldn't create a shortcut: {e})")
    return made


# ---------------------------------------------------------------- 4. health check
def health_check(state, repair=True, generate=True, verified=()):
    """Check every piece. Returns a list of (name, ok, detail, critical)."""
    results = []

    def add(name, ok, detail="", critical=True):
        results.append((name, ok, detail, critical))
        say(f"  [{'OK' if ok else '!!'}] {name}" + (f"  ({detail})" if detail else ""))

    up = ollama_up() or (repair and start_ollama())
    add("Ollama engine is running", bool(up))
    names = installed_models() if up else []
    models = [("coder", "Coding model", CODE_PROMPT), ("chat", "Chat model", CHAT_PROMPT)]
    for kind, label, prompt in models:
        tag = (state.get(kind) or {}).get("tag")
        if not tag:
            continue
        present = has_model(tag, names)
        add(f"{label} is downloaded", present, tag)
        if generate and present:
            if tag in verified:
                add(f"{label} answers a test question", True, "tested during the download")
            else:
                add(f"{label} answers a test question", verify(tag, prompt, label.lower()))
    for key, label in (("embed", "Code-search model"), ("autocomplete", "Autocomplete model")):
        tag = state.get(key)
        if tag:
            add(f"{label} is downloaded", has_model(tag, names), tag, critical=False)
    if state.get("webui"):
        port = state.get("webui_port", DEFAULT_PORT)
        chat_tag = (state.get("chat") or {}).get("tag")
        ok = webui_health(port) or (repair and start_webui(state, port, chat_tag))
        add(f"Open WebUI chat is running at http://localhost:{port}", bool(ok))
    if state.get("vscode"):
        code = find_code()
        add("VS Code has the Continue extension", bool(code and has_extension(code, VSCODE_EXTENSION)),
            critical=False)
        coder = (state.get("coder") or {}).get("tag", "")
        chat = (state.get("chat") or {}).get("tag", "")
        add("Continue is configured for your models", continue_config_ok(coder, chat), critical=False)
    return results


def all_ok(results):
    return all(ok for _, ok, _, crit in results if crit)


def check_mode(a):
    state = load_state()
    if not (state.get("coder") and state.get("chat")):
        say("No installation found. Run the installer first (without --check).")
        return 1
    say("\nChecking everything (and fixing what it can)...")
    results = health_check(state)
    save_state(state)
    ok = all_ok(results)
    say("\n" + ("Everything is working." if ok else "Something needs attention - see the [!!] lines above."))
    return 0 if ok else 1


def launch(a):
    """Start everything that isn't running and open the chat. Fast; no research or installs."""
    state = load_state()
    chat = (state.get("chat") or {}).get("tag")
    if not chat:
        say("Nothing is installed yet. Run the installer first.")
        return 1
    say("Starting Local AI...")
    if not start_ollama():
        say("The Ollama engine could not be started. Run the installer again to repair it.")
        return 1
    if state.get("webui"):
        port = state.get("webui_port", DEFAULT_PORT)
        if not webui_health(port):
            say("Starting the chat (can take a minute)...")
            start_webui(state, port, chat)
        if webui_health(port):
            if not a.no_open:
                open_url(f"http://localhost:{port}")
            time.sleep(2)
            return 0
        say("The browser chat did not start; opening a terminal chat instead.")
    if not a.no_open:
        open_terminal_chat(chat)
    return 0


# ---------------------------------------------------------------- main
def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="detect and research only; install nothing")
    ap.add_argument("-y", "--yes", action="store_true", help="don't ask questions")
    ap.add_argument("--offline", action="store_true", help="skip the live Hugging Face research (use the built-in list)")
    ap.add_argument("--check", action="store_true", help="health-check (and repair) an existing install")
    ap.add_argument("--launch", action="store_true", help="start everything and open the chat (used by the shortcut)")
    ap.add_argument("--no-webui", action="store_true", help="skip Open WebUI (the browser chat)")
    ap.add_argument("--no-vscode", action="store_true", help="skip the VS Code coding plug-in")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser when finished")
    ap.add_argument("--no-shortcut", action="store_true", help="don't create Desktop / Start-menu shortcuts")
    ap.add_argument("--no-self-update", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--prune", action="store_true", help="after an upgrade, delete the old model to free space")
    ap.add_argument("--models-dir", help="store downloaded models here (e.g. a big external SSD). Must be a LOCAL "
                                         "drive, not Google Drive/OneDrive/network")
    ap.add_argument("--max-gb", type=float, help="never pick models needing more than this much memory")
    ap.add_argument("--debug", action="store_true", help="print research details")
    ap.add_argument("--version", action="version", version=f"Local AI Installer v{VERSION}")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    init_console()
    if a.launch:
        return launch(a)
    say(f"Local AI Installer v{VERSION}   (log file: {LOG_FILE})")
    if a.check:
        return check_mode(a)

    state = load_state()
    updating = bool(state.get("coder") and state.get("chat"))
    if updating:
        say("Existing install found -> UPDATE MODE (your chats, projects and settings are kept)")

    step(1, "Detecting your PC")
    spec = detect(a.max_gb)
    say(f"OS:    {spec['os']} ({spec['arch']})")
    say(f"CPU:   {spec['cpu']} ({spec['cores']} threads)")
    say(f"RAM:   {spec['ram_gb']} GB")
    for g in spec["gpus"] or [{"name": "none detected", "vram_gb": 0}]:
        say(f"GPU:   {g['name']} ({g['vram_gb']:.1f} GB)")
    say(f"Disk:  {spec['disk_free_gb']} GB free")
    say(f"Plan:  run models on {spec['mode']} -> ~{spec['usable_gb']} GB usable")

    step(2, "Researching the best unlocked local AI for this PC")
    rec = recommend(spec)
    live = {"coder": [], "chat": []}
    if not a.offline:
        say("Searching Hugging Face for the newest unlocked models that fit your memory...")
        stats = {}
        live = research(spec, stats)
        if a.debug or not (live["coder"] or live["chat"]):
            say(f"  (research details: {stats})")
    coder_opts = [c["tag"] for c in live["coder"][:3]] + rec["coder"]
    chat_opts = [c["tag"] for c in live["chat"][:3]] + rec["chat"]
    for kind, title in (("coder", "coding"), ("chat", "chat / general")):
        if live[kind]:
            say(f"Top {title} models found live:")
            show(live[kind])
        else:
            say(f"No live {title} result - using the built-in {rec['tier']} list.")
    old_models = {}
    if updating:
        say("\nChecking your installed models against the latest research...")
        coder_opts, up_c = decide("coder", coder_opts, live["coder"], state)
        chat_opts, up_h = decide("chat", chat_opts, live["chat"], state)
        old_models = {k: state[k]["tag"] for k, up in (("coder", up_c), ("chat", up_h)) if up}
    for kind, title, opts in (("coder", "Coding model", coder_opts), ("chat", "Chat model  ", chat_opts)):
        note = "unlocked" if is_unlocked(opts[0]) else "standard model - no unlocked one fits this PC"
        say(f"{title}: {opts[0]}  ({note})")
    say("Engine:       Ollama (runs on NVIDIA / AMD / Apple GPUs, or the CPU)")
    say(f"Plug-ins:     {'; '.join(d for _, d in VSCODE_PLUGINS)}; Open WebUI (chat in your browser)")
    if spec["usable_gb"] < 4:
        say("NOTE: low memory - expect slow answers; a smaller model or a GPU would help.")

    mdir = models_dir(a.models_dir)
    need = 0.0
    for kind, opts in (("coder", coder_opts), ("chat", chat_opts)):
        if not (updating and state.get(kind, {}).get("tag") == opts[0]):  # already on disk -> no new space
            need += est_size_gb(opts[0], live[kind])
    if not updating:
        need += 0.3                                                     # code-search model
        need += 0 if a.no_vscode else 1.1                              # autocomplete model
        need += 0 if a.no_webui else 5.0                               # Open WebUI + private Python
    free = free_gb(mdir)
    say(f"\nStorage: needs about {need:.1f} GB; {free:.1f} GB free where models are stored ({mdir})")
    say("         Models must live on a local drive - cloud-synced folders (Google Drive, OneDrive) are too slow "
        "and can corrupt them.")
    if re.search(r"google ?drive|onedrive|dropbox|icloud", mdir, re.I):
        say("ERROR: that folder looks cloud-synced. Choose a local/external drive with --models-dir.")
        return 1
    low_space = free < need * 1.15
    if low_space:
        say("WARNING: not enough free space. Free some up, choose a bigger drive with --models-dir, or use a "
            "smaller model with --max-gb.")
    if a.dry_run:
        say("\nDry run: nothing installed.")
        return 0
    if low_space:
        return 1
    if not confirm("\nProceed with install?", a.yes):
        return 0

    step(3, "Installing")
    if a.models_dir:
        os.environ["OLLAMA_MODELS"] = mdir
        os.makedirs(mdir, exist_ok=True)
        if IS_WIN:
            subprocess.call(["setx", "OLLAMA_MODELS", mdir], stdout=subprocess.DEVNULL)
        else:
            say(f"Add this to your shell profile so it persists: export OLLAMA_MODELS={mdir}")
        if ollama_up():
            say("NOTE: Ollama is already running with its old models folder. Quit it (system tray / menu bar), "
                "then run this again to use the new folder.")
    if updating and find_ollama():
        start_ollama()  # the running version is needed to know whether an update exists
        upgrade_ollama()
    if not install_ollama() or not start_ollama():
        say("ERROR: could not install/start Ollama. See https://ollama.com/download")
        return 1
    coder = pull_working(coder_opts, CODE_PROMPT, "coding model")
    chat = pull_working(chat_opts, CHAT_PROMPT, "chat model")
    embed = pull_first([EMBED_MODEL])
    if not (coder and chat):
        say("ERROR: model download failed (check your internet connection and free disk space).")
        return 1

    code_ok, auto = False, None
    if not a.no_vscode:
        code = find_code()
        if not code and confirm("\nVisual Studio Code (the code editor) isn't installed. Install it now?", a.yes):
            code = install_vscode()
        if code:
            auto = pull_first(AUTOCOMPLETE_TAGS)
            code_ok = install_vscode_plugins(code) and write_continue_config(coder, chat, auto)
        else:
            say("VS Code not found - skipping the coding plug-in (install VS Code and run this again).")

    url = None if a.no_webui else setup_webui(state, chat)

    made = []
    copy = install_self_copy()
    if copy and not a.no_shortcut:
        made = make_shortcuts(launcher_cmd(copy))
        for p in made:
            say(f"Created shortcut: {p}")

    def entry(tag, cands):
        return {"tag": tag, "score": next((c["score"] for c in cands if c["tag"] == tag), None)}

    state.update(coder=entry(coder, live["coder"]), chat=entry(chat, live["chat"]), embed=embed,
                 autocomplete=auto, models_dir=mdir, vscode=code_ok, webui=bool(url), shortcuts=made)
    save_state(state)

    step(4, "Checking that everything works")
    results = health_check(state, verified={coder, chat})
    if not a.no_webui and not url:
        results.append(("Open WebUI chat", False, "", True))
        say("  [!!] Open WebUI (the browser chat) could not be set up - see the messages above. "
            "Run this installer again to retry.")
    ready = all_ok(results)
    if ready:
        for kind, old in old_models.items():
            if a.prune or (not a.yes and confirm(f"Upgrade worked. Delete the old {kind} model {old} to free "
                                                 f"space?", False, default=False)):
                subprocess.call([ollama_bin(), "rm", old])
            else:
                say(f"Kept old model {old} (remove it later with: ollama rm {old})")

    bar = "=" * 64
    say(f"\n{bar}")
    say(" READY - everything is installed and working." if ready else
        " INSTALLED, but something above needs attention ([!!] lines).")
    say(bar)
    if url:
        say(f"  Chat in your browser : {url}")
        say("      first time: click 'Get started' and create a local account (it never leaves your PC)")
    if code_ok:
        say("  Coding in VS Code    : open VS Code -> Continue panel (Ctrl+L / Cmd+L)")
    say(f"  Terminal chat        : ollama run {chat}")
    if made:
        say("  Next time            : double-click 'Local AI Chat' on your Desktop")
    say("  Update later         : run this installer again (it only downloads what is new)")
    say(f"  Log file             : {LOG_FILE}")
    if not a.no_open:
        if url:
            open_url(url)
        elif not a.no_webui:
            say("  The browser chat is not available, so opening a terminal chat instead.")
            open_terminal_chat(chat)
    return 0 if ready else 1


if __name__ == "__main__":
    try:
        maybe_self_update(sys.argv[1:])
        code = main()
    except KeyboardInterrupt:
        say("\nCancelled.")
        code = 130
    except Exception:
        tb = traceback.format_exc()
        _log(tb)
        say("\nSomething went wrong:\n" + tb)
        say(f"A log was saved to {LOG_FILE} - please send it to whoever gave you this installer.")
        code = 1
    interactive = getattr(sys, "frozen", False) and sys.stdin is not None and sys.stdin.isatty()
    if interactive and not any(x in sys.argv for x in ("--launch", "--check")):
        try:
            input("\nPress Enter to close...")  # double-clicked .exe: keep the window open
        except EOFError:
            pass
    sys.exit(code)
