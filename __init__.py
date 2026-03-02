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

# curl_cffi (브라우저 TLS 핑거프린트 위장)
try:
    import curl_cffi
except ImportError:
    _run(["pip", "install", "curl-cffi", "-q", "--break-system-packages"])
