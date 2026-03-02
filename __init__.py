import os
import subprocess

def _run(cmd):
    subprocess.run(cmd, capture_output=True)

# pyyaml
try:
    import yaml
except ImportError:
    _run(["pip", "install", "pyyaml", "-q", "--break-system-packages"])

# playwright 패키지
try:
    import playwright
except ImportError:
    _run(["pip", "install", "playwright", "-q", "--break-system-packages"])

# Chromium 시스템 의존성 (libglib, libnss 등)
_run(["playwright", "install-deps", "chromium"])

# Chromium 브라우저 바이너리
_run(["playwright", "install", "chromium"])
