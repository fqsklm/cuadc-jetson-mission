#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
  echo "请使用 sudo 运行本脚本" >&2
  exit 1
fi

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
install_dir=/opt/cuadc-mission
config_dir=/etc/cuadc-mission
data_dir=/var/lib/cuadc-mission

apt-get update
apt-get install -y \
  python3-venv python3-pip python3-gi gir1.2-gstreamer-1.0 \
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  v4l-utils ffmpeg usbutils

if ! id cuadc >/dev/null 2>&1; then
  useradd --system --home-dir "$data_dir" --create-home --shell /usr/sbin/nologin cuadc
fi
usermod -a -G video,dialout cuadc

install -d -o root -g root -m 0755 "$install_dir" "$install_dir/models" "$config_dir"
install -d -o cuadc -g cuadc -m 0750 "$data_dir" "$data_dir/photos"
cp -a "$project_dir/src" "$project_dir/scripts" "$project_dir/pyproject.toml" "$install_dir/"
python3 -m venv --system-site-packages "$install_dir/.venv"
"$install_dir/.venv/bin/pip" install --upgrade pip
"$install_dir/.venv/bin/pip" install "$install_dir"

if [ ! -e "$config_dir/config.json" ]; then
  install -m 0640 -o root -g cuadc "$project_dir/config/config.example.json" "$config_dir/config.json"
fi
install -m 0644 "$project_dir/systemd/cuadc-mission.service" /etc/systemd/system/cuadc-mission.service
systemctl daemon-reload
echo "安装完成。先编辑 $config_dir/config.json 并执行配置校验；脚本没有启用或启动飞行服务。"
echo "还需按当前 JetPack 安装 NVIDIA CUDA 版 PyTorch、TorchVision 与 Ultralytics，不能用普通 PyPI torch 覆盖 NVIDIA 构建。"
echo "将最终 .pt 模型安装为 $install_dir/models/target.pt；当前项目不使用 engine 或 ONNX。"
