import os
import subprocess

def _run(cmd):
    try:
        subprocess.run(cmd, capture_output=True)
    except Exception as e:
        print(f"[ff_cpp_test] install error: {e}")

# pyyaml
try:
    import yaml
except ImportError:
    _run(["pip", "install", "pyyaml", "-q", "--break-system-packages"])
