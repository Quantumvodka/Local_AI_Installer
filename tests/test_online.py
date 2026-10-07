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
                self.assertIn(tags[0], found, f"tier '{label}': the primary {group} pick {tags[0]} no longer exists")
        for t in [lai.EMBED_MODEL]:
            self.assertTrue(ollama_tag_exists(t), t)
        self.assertTrue(any(ollama_tag_exists(t) for t in lai.AUTOCOMPLETE_TAGS))
        print("\nCurated tags that do NOT exist on the registry (fix or remove):", missing)

    def test_gguf_builds_found_by_base_model(self):
        """The Hugging Face 'quantized from' search the PewDiePie lookup relies on, on a model that has many."""
        builds = lai.gguf_builds("Qwen/Qwen3-8B", lai.http_json)
        print("\nGGUF builds of Qwen/Qwen3-8B by trusted makers:", [(r, len(f)) for r, f in builds])
        self.assertTrue(builds, "the base_model:quantized search found no trusted GGUF build")
        for rid, files in builds:
            self.assertIn(rid.split("/")[0].lower(), lai.GGUF_MAKERS + ("qwen",))
            self.assertTrue(lai.best_quant(files, 20.0), rid)

    def test_pewdiepie_lookup_reaches_official_sources(self):
        refs = lai.official_refs()
        res = lai.pewdiepie_ai(8.0)
        print("\nofficial sources reached:", refs["reached"], "| refs:", refs["hf"], refs["ollama"], "| result:", res)
        self.assertNotEqual(res["status"], "unreachable")

    def test_github_connector_download_exists(self):
        asset = lai.gh_mcp_asset()
        if not asset:
            self.skipTest("no GitHub connector build for this machine")
        req = urllib.request.Request(f"https://github.com/{lai.GH_MCP_REPO}/releases/latest/download/{asset}",
                                     method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            self.assertEqual(r.status, 200)
        print("\nrelease info (None if the GitHub API is rate-limited here):", lai.gh_mcp_release(asset))


if __name__ == "__main__":
    unittest.main()
