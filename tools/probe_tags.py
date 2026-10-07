"""Print which candidate Ollama tags exist on the public registry (run in CI to keep the built-in list valid)."""
import urllib.request

CANDIDATES = {
    "unlocked coders": [
        "huihui_ai/qwen2.5-coder-abliterate:0.5b", "huihui_ai/qwen2.5-coder-abliterate:1.5b",
        "huihui_ai/qwen2.5-coder-abliterate:3b", "huihui_ai/qwen2.5-coder-abliterate:7b",
        "huihui_ai/qwen2.5-coder-abliterate:14b", "huihui_ai/qwen2.5-coder-abliterate:32b",
        "huihui_ai/qwen2.5-coder-abliterated:7b", "huihui_ai/qwen3-coder-abliterated:30b",
        "huihui_ai/deepseek-coder-v2-abliterated:16b", "huihui_ai/devstral-abliterated:24b",
    ],
    "unlocked chat": [
        "huihui_ai/qwen3-abliterated:0.6b", "huihui_ai/qwen3-abliterated:1.7b", "huihui_ai/qwen3-abliterated:4b",
        "huihui_ai/qwen3-abliterated:8b", "huihui_ai/qwen3-abliterated:14b", "huihui_ai/qwen3-abliterated:30b",
        "huihui_ai/qwen3-abliterated:32b", "huihui_ai/gemma3-abliterated:1b", "huihui_ai/gemma3-abliterated:4b",
        "huihui_ai/gemma3-abliterated:12b", "huihui_ai/gemma3-abliterated:27b", "huihui_ai/llama3.2-abliterate:3b",
        "huihui_ai/mistral-small-abliterated:24b", "huihui_ai/gpt-oss-abliterated:20b",
        "huihui_ai/phi4-abliterated:14b", "dolphin3:8b", "dolphin-mistral:7b", "dolphin-mixtral:8x7b",
    ],
    "standard fallbacks": [
        "qwen2.5-coder:1.5b", "qwen2.5-coder:3b", "qwen2.5-coder:7b", "qwen2.5-coder:14b", "qwen2.5-coder:32b",
        "qwen3-coder:30b", "qwen3:1.7b", "qwen3:4b", "qwen3:8b", "qwen3:14b", "qwen3:32b",
        "qwen2.5-coder:1.5b-base", "nomic-embed-text",
    ],
}


def exists(tag):
    name, _, t = tag.partition(":")
    if "/" not in name:
        name = "library/" + name
    req = urllib.request.Request(f"https://registry.ollama.ai/v2/{name}/manifests/{t or 'latest'}", method="HEAD",
                                 headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except Exception:
        return False


for group, tags in CANDIDATES.items():
    print(f"\n== {group}")
    for tag in tags:
        print(f"  {'EXISTS ' if exists(tag) else 'missing'}  {tag}")
