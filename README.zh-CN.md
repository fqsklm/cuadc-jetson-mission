# CUADC Jetson-only 全流程任务程序

这是一个面向固定翼竞赛任务的 Jetson-only 原型：NVIDIA Jetson 直接连接 USB
UVC 摄像头和 ZeroOne X6 Ultra 飞控，在本机完成图像采集、YOLO11 OBB
识别、目标融合、坐标估计和 MAVLink 任务管理。

> 当前仍处于台架验证阶段。默认配置固定为 `dry_run=true`、动态航点上传关闭、
> 释放关闭。对应验收通过且获得操作者明确授权前，不得打开这些开关，也不得
> 启用开机服务。

## 核心决策

- 机载计算只使用 Jetson，不引入树莓派或第二台机载计算机；
- 摄像头直接接入 Jetson，图像在内存中送入推理，不使用 NFS 或 MQTT 图像链路；
- 正式目标模型选择 `yolo11n-obb.pt`，项目只接受 PyTorch `.pt` 模型；
- Ultralytics 固定为 `8.3.176`，Jetson 保留 NVIDIA 定制 PyTorch/TorchVision；
- 飞控接口使用原生 `pymavlink`，不使用 DroneKit；
- 程序不主动解锁、不主动切 AUTO，也不把 MANUAL/RTL/LOITER 抢回 AUTO；
- 释放功能只面向规则允许的无害软质训练载荷，并受软件、RC 和物理保险共同约束。

## 当前状态

基线核验日期：2026-08-18（Asia/Shanghai）。

| 项目 | 当前状态 |
|---|---|
| Jetson | Orin NX 16 GB，JetPack/L4T R36.4.3，CUDA 可用 |
| 摄像头 | Realtek HDR CAMERA-A，2592×1944 MJPEG 30 FPS |
| 推理格式 | PT-only，非 `.pt` 配置会被拒绝 |
| 正式模型方向 | YOLO11n OBB，输入尺寸基线 1024 |
| Ultralytics | 电脑和 Jetson 独立环境均固定为 8.3.176 |
| 相机流水线 | 2K MJPEG 留档 + 1024×768 GStreamer/NVIDIA 解码预览 |
| 飞控链路 | 尚未发现正式 USB-UART，MAVLink 真机链路未验收 |
| 系统服务 | `/opt/cuadc-mission` 尚未正式部署，服务未安装/未启用 |
| 安全状态 | dry-run、动态上传关闭、释放关闭 |

当前 Orange 单类别 YOLO11n detect-640 `.pt` 已完成 60 秒相机全链路阶段测试：
29.93 FPS、推理 P95 30.50 ms。这个结果只证明现有 detect-640 基线，不代表
未来自定义 OBB-1024 模型、两小时压力测试或完整 MAVLink 并行负载已经通过。

官方 `yolo11n-obb.pt` 已分别在电脑 RTX 5070 Ti 和 Jetson Orin 的独立环境中
完成 CUDA OBB 冒烟推理；它基于 DOTA 类别，必须使用比赛数据重新训练或微调，
不能直接作为最终靶标模型。

## 系统数据流

```text
USB UVC 摄像头
  ├─ 2592×1944 MJPEG 原始帧 → 时间戳 → 照片留档
  └─ GStreamer/NVIDIA 解码 → 1024×768 预览 → YOLO11n-OBB (.pt)
                                               ↓
                                        多帧目标融合
                                               ↓
飞控姿态/GPS ── 单调时钟对齐 ── 相机外参/内参 ── 地理位置与围栏检查
                                               ↓
                                   任务状态机 / MAVLink 管理
```

相机帧、飞控姿态和推理结果使用 Jetson 单调时钟对齐；UTC 只用于跨设备日志和
证据关联。详细误差预算见 [TIME-SYNCHRONIZATION.zh-CN.md](TIME-SYNCHRONIZATION.zh-CN.md)。

## 仓库结构

```text
config/                         示例运行配置
scripts/                        Jetson 探测、安装、时间检查和性能测试
src/cuadc_jetson/               主程序、相机、视觉、飞控、任务和安全逻辑
systemd/                        systemd 服务单元
tests/                          配置、相机时间、状态机等软件测试
udev/                           飞控串口稳定设备名规则示例
ACCEPTANCE.zh-CN.md             分级台架与飞行验收清单
HANDOFF-2026-08-15.zh-CN.md     当前实机事实和下一步顺序
YOLO11-ENVIRONMENT.zh-CN.md     电脑/Jetson 独立环境说明
YOLO-30FPS-PLAN.zh-CN.md        PT-only 相机推理性能方案与实测基线
```

模型、照片、日志、设备私有配置和工作区外备份不进入 Git。

## 硬件连接

### USB 摄像头

- 摄像头接 Jetson USB 3.x；
- 使用 `/dev/v4l/by-id/...` 稳定路径，不要把 `/dev/video0` 写入正式配置；
- 当前实测生产模式为 2592×1944 MJPEG 30 FPS；高分辨率 YUYV 只有 2–5 FPS；
- 相机线应短且固定，避免与电调动力线并行走线。

### X6 Ultra 与 Jetson

首版使用 X6 Ultra TELEM2 和 3.3 V TTL USB-UART：

| TELEM2 | USB-UART 飞控侧 | 说明 |
|---|---|---|
| TX | RX | 交叉连接 |
| RX | TX | 交叉连接 |
| GND | GND | 按隔离模块说明接地 |
| 5V/VCC | 不接 | 禁止用 TELEM 5 V 给 Jetson 供电 |

不要把 RS-232/RS-485 转换器当 TTL 串口使用。拆桨、断开执行器物理保险后，
建议从以下 ArduPilot 参数开始台架验证：

```text
SERIAL2_PROTOCOL = 2
SERIAL2_BAUD     = 921
SERIAL2_OPTIONS  = 0
```

配置中的 `flight.source_system` 应与 `SYSID_THISMAV` 一致，Jetson 的
`source_component` 使用独立组件 ID 191。

### 电源与执行器

- 飞控由合规电源模块供电；
- Jetson 使用满足载板输入范围和峰值功率的独立稳压模块；
- 舵机使用独立 BEC/电源轨，Jetson GPIO 和 USB-UART 不给舵机供电；
- RC 授权开关之外，还应保留可见、可拔除的执行器物理保险。

## 软件环境

项目运行代码要求 Python 3.10 或更高。当前已验证的 AI 组合：

| 环境 | Python | PyTorch | TorchVision | Ultralytics |
|---|---:|---|---|---:|
| Windows 训练环境 `cuadc-yolo11` | 3.9.23 | 2.7.1+cu128 | 0.22.1+cu128 | 8.3.176 |
| Jetson 推理 venv | 3.10.12 | NVIDIA 2.5.0a0 | NVIDIA 0.20.0a0 | 8.3.176 |

Windows 环境只用于 YOLO 训练/验证；主程序自身要求 Python 3.10+。完整激活和
重建约束见 [YOLO11-ENVIRONMENT.zh-CN.md](YOLO11-ENVIRONMENT.zh-CN.md)。

Jetson 不得执行会自动替换依赖的通用升级命令：

```bash
# 不要这样做
pip install -U torch torchvision ultralytics
```

Jetson venv 应使用 `--system-site-packages` 复用 JetPack 对应的 NVIDIA 构建，
再以 `--no-deps` 安装精确的 Ultralytics 版本。

## 快速开始

### 1. 克隆与只读探测

```bash
git clone https://github.com/fqsklm/cuadc-jetson-mission.git
cd cuadc-jetson-mission
chmod +x scripts/*.sh
./scripts/jetson-probe.sh | tee jetson-probe.txt
```

`jetson-probe.sh` 只读取 JetPack、内核、摄像头模式、串口、网络和 Python AI
栈，不修改硬件配置。确认摄像头和 USB-UART 后再填写稳定设备路径。

### 2. 建立飞控稳定设备名

```bash
udevadm info --query=property --name=/dev/ttyUSB0
```

把实际 VID/PID/序列号写入
`udev/99-cuadc-flight-controller.rules.example` 的副本，并安装为：

```text
/etc/udev/rules.d/99-cuadc-flight-controller.rules
```

重载规则后应得到 `/dev/cuadc-fc`。首次通信只做只读遥测记录，不上传任务，
不发送舵机命令。

### 3. 安装程序骨架

```bash
sudo ./scripts/install-jetson.sh
```

安装脚本会创建用户、目录、Python venv 和 systemd 单元，但不会启用或启动服务。
它也不会替换 NVIDIA PyTorch。随后在安装 venv 内固定 Ultralytics：

```bash
sudo /opt/cuadc-mission/.venv/bin/python -m pip install \
  --no-deps ultralytics==8.3.176
```

安装后必须确认 CUDA 和包来源：

```bash
/opt/cuadc-mission/.venv/bin/python -c \
  "import torch, ultralytics; print(torch.cuda.is_available(), torch.__version__, ultralytics.__version__, ultralytics.__file__)"
```

### 4. 安装最终模型

```bash
sudo install -m 0644 /path/to/custom-yolo11n-obb.pt \
  /opt/cuadc-mission/models/target.pt
sha256sum /opt/cuadc-mission/models/target.pt
```

每次模型更新应同时记录训练数据版本、Ultralytics/PyTorch 版本、输入尺寸、类别
映射、精度指标和 SHA-256。项目不生成、不部署也不加载 `.engine` 或 `.onnx`。

### 5. 配置和校验

```bash
sudoedit /etc/cuadc-mission/config.json
/opt/cuadc-mission/.venv/bin/cuadc-mission \
  --config /etc/cuadc-mission/config.json --validate
```

至少替换以下设备/飞机专属数据：

- 摄像头 `/dev/v4l/by-id/...` 路径和实际模式；
- 最终 `target.pt`，以及释放验收前单独验证的人员检测 `.pt`；
- `/dev/cuadc-fc` 和实际波特率；
- 当前相机焦距/分辨率对应的内参和畸变参数；
- 相机到机体系的安装旋转；
- 侦察、进场、离场和实飞验证过的降落航线；
- 只覆盖竞赛安全区的释放地理围栏；
- 飞机真实 RC 授权通道和未占用的输出通道。

必须继续保持：

```json
{
  "dry_run": true,
  "mission": {"dynamic_upload_enabled": false},
  "release": {"enabled": false}
}
```

### 6. 前台 dry-run

```bash
sudo -u cuadc /opt/cuadc-mission/.venv/bin/cuadc-mission \
  --config /etc/cuadc-mission/config.json --log-level DEBUG
```

服务只有在完整台架验收通过后才能启用：

```bash
sudo systemctl enable --now cuadc-mission.service
systemctl status cuadc-mission.service
journalctl -u cuadc-mission.service -f
```

需要停止时：

```bash
sudo systemctl disable --now cuadc-mission.service
```

## 模型与目标选择

正式外层目标定位使用 YOLO11n OBB，默认 `vision.task="obb"`、
`vision.input_size=1024`、`vision.detect_hz=10`。数字任务可使用
`vision.selection="max_numeric"`；图形任务应使用 `"priority"` 并填写
`label_priorities`。`"confidence"` 只适合调试，不代表比赛目标价值。

人员检测是独立模型和独立验收项，不应把人员类别与靶标类别混在一个未经验证
的模型里。只有释放功能获准测试时才配置 `people_model`。

## 相机与 YOLO 性能测试

先确认摄像头模式：

```bash
v4l2-ctl -d /dev/v4l/by-id/你的相机 --list-formats-ext
```

对最终 OBB 模型运行 60 秒阶段测试：

```bash
cd /home/jetson/cuadc-jetson-mission
PYTHONPATH=src /home/jetson/venvs/cuadc-yolo11/bin/python \
  scripts/benchmark-camera-yolo.py \
  --backend gstreamer \
  --device /dev/v4l/by-id/你的相机 \
  --model /path/to/target.pt --task obb \
  --width 2592 --height 1944 --camera-fps 30 --fourcc MJPG \
  --preview-width 1024 --preview-height 768 \
  --imgsz 1024 --seconds 60 --alignment-holdback-ms 80
```

当前任务检测频率是 10 Hz，单次推理预算为 100 ms。30 FPS 是相机流水线和
高频模式的阶段能力目标，不等于飞行主程序必须逐帧推理。正式验收还需要连续
两小时，并叠加照片留档、目标融合、热状态和 MAVLink 只读负载。

## 状态机与失效行为

```text
自检/未解锁 → 等待飞行 → AUTO侦察 → 多帧目标锁定 → 上传新航线
                                                ↓
                     到达目标附近 → 等待RC授权+人员净空 → 释放一次 → 航线降落
```

上图描述最终能力，不代表当前开关已经启用。程序的关键失效行为：

- 心跳、GPS、姿态、相机或授时不新鲜时保持闭锁；
- 目标未满足置信度、聚类、多帧数量或围栏要求时不上传动态任务；
- 飞手切入 MANUAL/RTL/LOITER 后进入 `pilot_override`，不抢回 AUTO；
- 空中重启不重新上传侦察任务；
- 释放需要动态任务已授权、独立人员检测净空、RC 授权和一次性状态共同满足。

侦察航线第一条必须是 `MAV_CMD_NAV_TAKEOFF (22)`，降落航线最后一条必须是
`MAV_CMD_NAV_LAND (21)`。动态目标航线还需要至少一个进场点和一个离场点。

## 开发与测试

在 Python 3.10+ 开发环境中：

```bash
python -m pip install -e ".[test]"
pytest -q
```

配置语法和纯软件状态机测试不能替代 Jetson CUDA、真实相机、真实串口和拆桨
台架测试。验收顺序见 [ACCEPTANCE.zh-CN.md](ACCEPTANCE.zh-CN.md)：

```text
纯软件 → 无飞控 dry-run → 拆桨 MAVLink → 拆桨舵机假负载
       → 低风险航线 → 只侦察不释放 → 软质训练载荷飞行
```

任何一级失败都回到上一级，不能跳级。

## 已知未完成项

- 最终比赛数据集和自定义 YOLO11n-OBB 权重尚未定版；
- 相机到机体系外参仍需通过地面控制点验证；
- X6 Ultra USB-UART、MAVLink 遥测和任务 ACK 尚未实机验收；
- 两小时相机、推理、照片留档、目标融合和 MAVLink 并行测试未完成；
- `/opt/cuadc-mission` 和 systemd 服务尚未正式安装；
- 动态航点上传和释放功能没有获得启用授权。

继续工作前请先阅读 [HANDOFF-2026-08-15.zh-CN.md](HANDOFF-2026-08-15.zh-CN.md)，
并以实机复核结果为准，不要把历史文档中的设备路径、坐标或标定数据直接用于
另一架飞机。
