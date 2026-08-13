# CUADC Jetson-only 全流程任务程序

时间对齐方案与误差预算见 [TIME-SYNCHRONIZATION.zh-CN.md](TIME-SYNCHRONIZATION.zh-CN.md)。

摄像头内参标定使用 ROS 2 的操作步骤见 [ROS_CAMERA_CALIBRATION.zh-CN.md](ROS_CAMERA_CALIBRATION.zh-CN.md)。标定只临时使用 ROS，任务运行时仍直接读取 UVC 摄像头。

这个目录是面向 **NVIDIA Jetson + ZeroOne X6 Ultra + USB UVC 摄像头** 的新实现。它不需要树莓派、NFS、MQTT 或第二台机载计算机。

当前版本已经具备可测试的任务骨架：

1. Jetson 上电后由 systemd 启动；
2. 在未解锁状态完成相机、模型、MAVLink、GPS 自检并上传预制侦察航线；
3. 飞手用遥控器解锁并切入 AUTO，飞机超过安全高度后持续拍照和识别；
4. 目标经过置信度、位置聚类和多帧数量门槛后才会锁定；
5. 目标必须位于显式地理围栏内，程序才上传“进场点 → 目标点 → 离场点 → 预先实测的降落航线”；
6. 只针对非危险竞赛训练载荷：到达目标附近后仍需 RC 独立授权，且近期画面不得出现人员，才允许一次释放；
7. 降落由航线中的 ArduPilot LAND/固定翼降落序列完成；程序不主动解锁、不主动切 AUTO，也不把 RTL 或人工模式改回 AUTO。

默认配置是 `dry_run=true`、`mission.dynamic_upload_enabled=false`、`release.enabled=false`，因此不会改写飞控任务或输出舵机命令。

## 与 CUADC2025 参考项目的区别

- 摄像头直接插 Jetson，图像直接在内存中进入 TensorRT/Ultralytics；没有树莓派、MQTT 文件名通知和 NFS 图片目录。
- 继承 AirCam 的经验：稳定 `/dev/v4l/by-id` 设备名、持续留档、原始帧时间戳、systemd 故障重启、独立可写数据目录。
- 不继承参考项目中“任务末尾/RTL 强制释放”和“把 RTL 改回 AUTO”的行为。
- 不使用 DroneKit。飞控接口是原生 `pymavlink`，并要求任务上传 ACK、舵机命令 ACK 和遥测新鲜度检查。
- 不使用参考项目中的莫干山坐标、相机内参或 TensorRT engine；它们都必须在当前飞机上重新标定或生成。

## 硬件接线

### 1. USB 摄像头

- USB 摄像头 → Jetson USB 3.x 口。
- 使用短线、锁紧或扎带固定；不要让相机和飞控串口共享松动的 USB Hub。
- 摄像头由 Jetson USB 供电。若实测峰值电流过大，使用有独立稳压输入的工业 USB Hub。

### 2. X6 Ultra 与 Jetson 的主数据链路（首版推荐）

使用 X6 Ultra 原装/确认过线序的 **TELEM2 线束** 和 **3.3 V TTL 隔离型 USB-UART**：

| X6 Ultra TELEM2 信号 | USB-UART 飞控侧 | 说明 |
|---|---|---|
| TX | RX | 交叉连接 |
| RX | TX | 交叉连接 |
| GND | GND | 非隔离模块必须共地；隔离模块按其两侧说明分别接地 |
| 5V/VCC | 不接 | Jetson 和飞控各自供电，禁止用 TELEM 5V 给 Jetson 供电 |

USB-UART 的 USB 端插 Jetson。不要凭插头位置猜针脚；先按 X6 Ultra 随机线束标签或产品针脚图确认 TX/RX/GND，再用万用表复核 GND。不要把 RS-232/RS-485 转换器当 TTL 串口使用。

官方资料确认 TELEM2 映射到 `SERIAL2`；ArduPilot 建议台架初值：

```text
SERIAL2_PROTOCOL = 2    # MAVLink2
SERIAL2_BAUD     = 921  # 921600 baud
SERIAL2_OPTIONS  = 0    # 首版不反相、不交换 TX/RX
```

配置中的 `flight.source_system` 必须与飞控 `SYSID_THISMAV` 相同（常见默认值为 1），`source_component` 保持为独立的机载计算机组件 ID 191；不要把 Jetson 配成另一个飞行器 System ID。

如果 TELEM2 已被数传占用，可用 TELEM1 (`SERIAL1`) 或 TELEM3 (`SERIAL5`)，同时改对应 `SERIALx_*` 参数。飞控 Type-C 建议留给 Mission Planner/QGroundControl 配置和固件维护，不作为正式机内飞行线缆。

X6 Ultra 也有 100M Ethernet，可在串口版通过后作为第二阶段链路；使用前必须确认 X6 Ultra 固件、IP/UDP 参数和随机网口线束，不在首飞时临时改用。

### 3. 电源

- 飞控：由 OnePMU/合规电源模块接 X6 Ultra Power 口。
- Jetson：由独立、满足你实际载板输入范围与峰值功率的稳压模块供电；输入范围必须查载板手册，不能按 Jetson 模组名称猜。
- 舵机/执行器：由独立舵机 BEC/电源轨供电，信号接一个未占用的 X6 Ultra PWM 输出。严禁从 Jetson GPIO 或 USB-UART 给舵机供电。
- USB 摄像头与串口线应与电调动力线分开走线；机身入口做拉力释放和屏蔽/磁环验证。

### 4. 非危险训练载荷执行器

示例配置沿用未占用的 M9，但必须以你飞机的真实通道分配为准。舵机三线为：信号 → M9 信号针，正电 → 独立舵机电源正，地 → 舵机电源地/飞控伺服地。参数应将对应输出留给任务控制，并在拆桨状态确认：

```text
SERVO9_FUNCTION = 0
```

保留两个独立门控：遥控器 RC 授权通道，以及串在执行器供电或信号上的物理保险/拔插销。软件只控制比赛允许的无害轻质训练载荷，不用于人员、车辆或现实目标。

## Jetson 探测与安装

先把本目录复制到 Jetson，然后运行（仅探测，不修改设备）：

```bash
cd jetson_mission
chmod +x scripts/*.sh
./scripts/jetson-probe.sh | tee jetson-probe.txt
```

`jetson-probe.txt` 将给出 JetPack/内核、USB 摄像头模式、串口设备和网络接口。随后用下面命令取得 USB-UART 的 VID/PID/序列号：

```bash
udevadm info --query=property --name=/dev/ttyUSB0
```

把结果填入 `udev/99-cuadc-flight-controller.rules.example`，另存为 `/etc/udev/rules.d/99-cuadc-flight-controller.rules`，重载后应出现 `/dev/cuadc-fc`。摄像头配置优先使用 `/dev/v4l/by-id/...`，不要硬编码 `/dev/video0`。

安装基础服务：

```bash
sudo ./scripts/install-jetson.sh
sudoedit /etc/cuadc-mission/config.json
/opt/cuadc-mission/.venv/bin/cuadc-mission --config /etc/cuadc-mission/config.json --validate
```

Jetson 的 PyTorch、torchvision、OpenCV 和 TensorRT 应使用与当前 JetPack 匹配的 NVIDIA 版本。确认它们已可用后，在虚拟环境中安装 Ultralytics；不要让普通 PyPI wheel 覆盖 JetPack 自带 CUDA 版 PyTorch。

首次只做手动前台运行：

```bash
sudo -u cuadc /opt/cuadc-mission/.venv/bin/cuadc-mission \
  --config /etc/cuadc-mission/config.json --log-level DEBUG
```

台架验收完成后才启用开机自启：

```bash
sudo systemctl enable --now cuadc-mission.service
systemctl status cuadc-mission.service
journalctl -u cuadc-mission.service -f
```

停止服务：

```bash
sudo systemctl disable --now cuadc-mission.service
```

## 必须替换的配置

复制 `config/config.example.json` 为设备配置，至少替换：

- 相机的稳定设备路径、实际分辨率/FPS；
- 在本台 Jetson 上由 `.pt`/`.onnx` 生成的 TensorRT engine；
- 当前 USB-UART 设备名和实际波特率；
- 当前 USB 相机在当前焦距、分辨率下的 `fx/fy/cx/cy` 和畸变校正流程；
- 相机到机体系的安装旋转；
- 侦察航线、目标进场点、离场点和经过实飞验证的降落航线；
- 只覆盖竞赛安全区的释放地理围栏；
- 飞机真实 RC 授权通道和未占用的输出通道。

数字任务可用 `vision.selection="max_numeric"`；图形任务应使用 `"priority"` 并在 `label_priorities` 中写明每个类别的比赛价值。`"confidence"` 只适合调试，不等同于目标价值。

配置必须先保持：

```json
"dry_run": true,
"mission": { "dynamic_upload_enabled": false },
"release": { "enabled": false }
```

程序在启用动态航线前会硬性检查：`dry_run=false`、至少三点围栏、侦察航线和降落航线文件全部存在。启用释放时还会检查独立人员模型、RC 授权和动态航线开关。

侦察航线第一条任务必须是 `MAV_CMD_NAV_TAKEOFF (22)`；降落航线最后一条必须是 `MAV_CMD_NAV_LAND (21)`。动态目标航线还必须配置至少一个进场点和一个离场点，避免固定翼从任意方向直接切向目标。

## 状态机和失效行为

```text
自检/未解锁 → 等待飞行 → AUTO侦察 → 多帧目标锁定 → 上传新航线
                                                ↓
                     到达目标附近 → 等待RC授权+人员净空 → 释放一次 → 航线降落
```

任何阶段遇到心跳超时、GPS 不足、相机故障、目标出围栏时均保持闭锁。飞手切 MANUAL/RTL/LOITER 后程序进入 `pilot_override`，不再发任务或执行器命令，也不会抢回 AUTO。服务在空中重启时不会上传侦察任务。

## 台架到飞行的验收顺序

详见 `ACCEPTANCE.zh-CN.md`。至少依次完成：纯软件测试 → 无飞控 dry-run → 拆桨 MAVLink → 拆桨舵机假负载 → 滑跑/低风险航线 → 只侦察不释放 → 软质训练载荷飞行。任何一级失败都回到上一级，不能跳级。
