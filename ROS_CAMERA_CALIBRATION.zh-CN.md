# 使用 ROS 2 标定 CUADC 摄像头

这套流程只在标定时使用 ROS 2。飞行任务仍由 `cuadc-mission` 直接通过 OpenCV/V4L2 读取 USB 摄像头，避免增加飞行运行时依赖。

## 1. 保持飞行相机参数一致

标定必须使用任务配置中的同一个设备、分辨率、镜头焦距、对焦和裁剪方式。标定期间停止任务服务，避免两个进程争用摄像头：

```bash
sudo systemctl stop cuadc-mission.service
v4l2-ctl --device=/dev/v4l/by-id/你的相机 --list-formats-ext
```

## 2. 安装并加载 ROS 2 工具

以下命令使用当前已安装的 ROS 2 发行版：

```bash
source /opt/ros/humble/setup.bash  # 按实际发行版修改
sudo apt install ros-${ROS_DISTRO}-usb-cam ros-${ROS_DISTRO}-camera-calibration python3-yaml
```

## 3. 准备标定板并运行

`BOARD_SIZE` 填棋盘内部角点数，不是方格数。例如 9x7 个方格是 `8x6` 个内部角点。`SQUARE_M` 填单格边长（米）。

```bash
cd jetson_mission
chmod +x scripts/ros2-calibrate-camera.sh scripts/import-ros-calibration.py
./scripts/ros2-calibrate-camera.sh \
  /dev/v4l/by-id/你的相机 \
  1920 1080 30 \
  8x6 0.025 pinhole
```

普通镜头使用 `pinhole`。明显鱼眼镜头使用 `fisheye`；收到首帧后，按脚本提示把界面顶部 Camera type 滑块调到 fisheye，不要混用模型。

缓慢移动棋盘，使其覆盖画面中央、四边和四角，并改变距离及俯仰角度。界面的 X、Y、Size、Skew 都有充分覆盖后点击 `CALIBRATE`，检查校正画面中的直线，再点击 `SAVE`。

## 4. 导入任务配置

保存结果通常位于 `/tmp/calibrationdata.tar.gz`：

```bash
mkdir -p /tmp/cuadc-camera-calibration
tar -xzf /tmp/calibrationdata.tar.gz -C /tmp/cuadc-camera-calibration
sudo ./scripts/import-ros-calibration.py \
  --calibration /tmp/cuadc-camera-calibration/ost.yaml \
  --config /etc/cuadc-mission/config.json
```

导入器会：

- 拒绝分辨率不一致的标定；
- 写入 `fx/fy/cx/cy`、ROS 畸变模型和全部畸变系数；
- 在同目录保存带 UTC 时间戳的 `config.json.before-camera-calibration-*.bak`；
- 原子替换配置文件，避免写到一半损坏。

然后验证配置并前台试运行：

```bash
/opt/cuadc-mission/.venv/bin/cuadc-mission --config /etc/cuadc-mission/config.json --validate
sudo -u cuadc /opt/cuadc-mission/.venv/bin/cuadc-mission \
  --config /etc/cuadc-mission/config.json --log-level DEBUG
```

最终还需用地面已知控制点验证像素落点；内参标定不能代替 `camera_to_body_rpy_deg` 的相机外参/安装角标定。
