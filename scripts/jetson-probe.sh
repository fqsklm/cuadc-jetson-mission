#!/usr/bin/env bash
set -euo pipefail

echo "== Jetson =="
cat /etc/nv_tegra_release 2>/dev/null || true
uname -a
cat /proc/device-tree/model 2>/dev/null || true
echo
dpkg-query -W nvidia-jetpack nvidia-l4t-core 2>/dev/null || true

echo "== Power mode =="
nvpmodel -q 2>/dev/null || true

echo "== USB / video =="
lsusb
v4l2-ctl --list-devices || true
for dev in /dev/video*; do
  [ -e "$dev" ] || continue
  echo "-- $dev"
  v4l2-ctl -d "$dev" --list-formats-ext || true
done

echo "== Serial =="
ls -l /dev/serial/by-id /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true

echo "== Network =="
ip -brief address

echo "== NVIDIA runtime =="
nvidia-smi 2>/dev/null || tegrastats --interval 1000 --count 1 2>/dev/null || true
nvcc --version 2>/dev/null || true
dpkg-query -W 'libnvinfer*' 'libcudnn*' 2>/dev/null || true

echo "== Python AI stack =="
python3 --version
python3 - <<'PY' 2>/dev/null || true
import importlib
import json

result = {}
for name in ("numpy", "cv2", "torch", "torchvision", "tensorrt", "ultralytics"):
    try:
        module = importlib.import_module(name)
        result[name] = getattr(module, "__version__", "installed/version unavailable")
    except Exception as exc:
        result[name] = f"unavailable: {type(exc).__name__}: {exc}"
try:
    import torch
    result["torch_cuda_available"] = torch.cuda.is_available()
    result["torch_cuda_version"] = torch.version.cuda
    result["torch_device"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
except Exception:
    pass
print(json.dumps(result, indent=2, ensure_ascii=False))
PY
