"""Tests that need the internet (Hugging Face + the Ollama registry). Only run when LAI_ONLINE=1."""
import importlib.util
import os
import sys
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("lai_online", os.path.join(ROOT, "local_ai_installer.py"))
lai = importlib.util.module_from_spec(_spec)
sys.modules["lai_online"] = lai
_spec.loader.exec_module(lai)

ONLINE = os.environ.get("LAI_ONLINE") == "1"


def ollama_tag_exists(tag):
    """True if the tag is published on the Ollama registry (HEAD request on its manifest)."""
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


@unittest.skipUnless(ONLINE, "set LAI_ONLINE=1 to run network tests")
class Online(unittest.TestCase):
    def test_huggingface_search_and_ranking(self):
        pool = lai.search_models(["abliterated"], limit=50)
        print(f"\nHF search returned {len(pool)} repos; keys of first: {sorted(pool[0]) if pool else None}")
        self.assertTrue(pool, "Hugging Face search returned nothing")
        stats = {}
        res = lai.research({"usable_gb": 16}, stats)
        print("research stats:", stats)
        for kind in ("chat", "coder"):
            print(kind, [(c["repo"], c["quant"], c["size_gb"], c["age_months"]) for c in res[kind]])
        self.assertTrue(res["chat"], f"no live chat models found; stats={stats}")

    def test_curated_tags_exist_on_ollama(self):
        missing = []
        for _, label, coder, chat in lai.TIERS:
            for group, tags in (("coder", coder), ("chat", chat)):
                found = [t for t in tags if ollama_tag_exists(t)]
                gone = [t for t in tags if t not in found]
                missing += gone
                self.assertTrue(found, f"tier '{label}' has no downloadable {group} model: {tags}")
        for t in [lai.EMBED_MODEL]:
            self.assertTrue(ollama_tag_exists(t), t)
        self.assertTrue(any(ollama_tag_exists(t) for t in lai.AUTOCOMPLETE_TAGS))
        print("\nCurated tags that do NOT exist on the registry (fix or remove):", missing)


if __name__ == "__main__":
    unittest.main()
