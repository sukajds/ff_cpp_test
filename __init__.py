import os
import subprocess

def _install(pkg):
    subprocess.run(
        ["pip", "install", pkg, "-q", "--break-system-packages"],
        capture_output=True
    )

# pyyaml
try:
    import yaml
except ImportError:
    _install("pyyaml")

# playwright
try:
    import playwright
except ImportError:
    _install("playwright")
    # Chromium 브라우저 바이너리 설치
    subprocess.run(
        ["python3", "-m", "playwright", "install", "chromium"],
        capture_output=True
    )
