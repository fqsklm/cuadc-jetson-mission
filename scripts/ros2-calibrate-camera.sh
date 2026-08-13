#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
用法：
  ros2-calibrate-camera.sh DEVICE WIDTH HEIGHT FPS BOARD_SIZE SQUARE_M [CAMERA_MODEL]

示例（棋盘有 9x7 个方格，即 8x6 个内部角点；格长 25 mm）：
  ./scripts/ros2-calibrate-camera.sh \
    /dev/v4l/by-id/usb-CAMERA 1920 1080 30 8x6 0.025 pinhole

CAMERA_MODEL 可选 pinhole（默认）或 fisheye。
EOF
}

if [ "$#" -lt 6 ] || [ "$#" -gt 7 ]; then
  usage >&2
  exit 2
fi

device=$1
width=$2
height=$3
fps=$4
board_size=$5
square_m=$6
camera_model=${7:-pinhole}

if [ "${ROS_VERSION:-}" != "2" ] || [ -z "${ROS_DISTRO:-}" ]; then
  echo "请先 source ROS 2 环境，例如：source /opt/ros/humble/setup.bash" >&2
  exit 1
fi
if [ ! -e "$device" ]; then
  echo "相机设备不存在：$device" >&2
  exit 1
fi
if ! command -v ros2 >/dev/null 2>&1; then
  echo "找不到 ros2 命令" >&2
  exit 1
fi
if ! ros2 pkg prefix usb_cam >/dev/null 2>&1; then
  echo "缺少 usb_cam：sudo apt install ros-${ROS_DISTRO}-usb-cam" >&2
  exit 1
fi
if ! ros2 pkg prefix camera_calibration >/dev/null 2>&1; then
  echo "缺少 camera_calibration：sudo apt install ros-${ROS_DISTRO}-camera-calibration" >&2
  exit 1
fi
if [ "$camera_model" != "pinhole" ] && [ "$camera_model" != "fisheye" ]; then
  echo "CAMERA_MODEL 只能是 pinhole 或 fisheye" >&2
  exit 2
fi

echo "启动 USB 相机：${width}x${height}@${fps}，设备 ${device}"
ros2 run usb_cam usb_cam_node_exe --ros-args \
  -r __ns:=/camera \
  -p video_device:="$device" \
  -p image_width:="$width" \
  -p image_height:="$height" \
  -p framerate:="$fps" \
  -p pixel_format:="mjpeg2rgb" &
camera_pid=$!

cleanup() {
  kill "$camera_pid" 2>/dev/null || true
  wait "$camera_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for _ in $(seq 1 30); do
  if ros2 topic list 2>/dev/null | grep -Fxq /camera/image_raw; then
    break
  fi
  if ! kill -0 "$camera_pid" 2>/dev/null; then
    echo "usb_cam 已退出；请检查相机设备、权限和支持的格式" >&2
    exit 1
  fi
  sleep 0.2
done
if ! ros2 topic list | grep -Fxq /camera/image_raw; then
  echo "等待 /camera/image_raw 超时" >&2
  exit 1
fi

calibrator_args=(
  --size "$board_size"
  --square "$square_m"
  image:=/camera/image_raw
  camera:=/camera
)

echo "在窗口中覆盖 X/Y/Size/Skew，点击 CALIBRATE；完成后点击 SAVE。"
if [ "$camera_model" = "fisheye" ]; then
  echo "收到首帧后，请把窗口顶部的 Camera type 滑块从 0 调到 1（fisheye）。"
fi
echo "结果通常保存为 /tmp/calibrationdata.tar.gz，其中包含 ost.yaml。"
ros2 run camera_calibration cameracalibrator "${calibrator_args[@]}"
