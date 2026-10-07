"""Print the installer's VERSION (used by the release workflow)."""
import os
import re

path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "local_ai_installer.py")
with open(path, encoding="utf-8") as f:
    print(re.search(r'^VERSION = "([^"]+)"', f.read(), re.M).group(1))
