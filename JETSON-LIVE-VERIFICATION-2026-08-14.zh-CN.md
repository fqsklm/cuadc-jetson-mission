# Jetson 实机版本核验与性能测试报告

日期：2026-08-14（Asia/Shanghai）
目标主机：`jetson@192.168.1.20`（hostname: `yahboom`）

## 结论

这台设备确认是 NVIDIA Jetson Orin NX Engineering Reference Developer Kit Super，具有约 16 GB 内存，适合运行 Jetson 单机版飞行任务程序。USB 摄像头的 2592×1944 MJPEG 原始视频流可以稳定输出 30 FPS，但当前 OpenCV CPU 解码与现有 YOLO11 TensorRT 引擎组成的 Python 流水线不能达到端到端 30 FPS。

当前最优先问题不是更换 Jetson，而是：

1. 清理或扩容几乎已满的 NVMe；
2. 将摄像头采集改为 GStreamer + `nvv4l2decoder` 硬件 MJPEG 解码；
3. 在本机重新生成 TensorRT FP16 引擎，不能继续依赖带有跨设备警告的旧引擎；
4. 将前处理、推理和后处理流水化，并重新进行端到端 30 FPS 验收；
5. 接入 ZeroOne X6 Ultra 后再进行 MAVLink 串口核验。

## 硬件与系统

| 项目 | 实测结果 | 状态 |
|---|---|---|
| Jetson 型号 | NVIDIA Jetson Orin NX Engineering Reference Developer Kit Super | 通过 |
| 架构 | aarch64，8 CPU 核，GPU Compute Capability 8.7，8 SM | 通过 |
| 内存 | 15 GiB 可见，约等于 16 GB SKU；测试时可用约 10 GiB | 通过 |
| 操作系统 | Ubuntu 22.04.5 LTS | 通过 |
| 内核 | 5.15.148-tegra | 通过 |
| L4T | R36.4.3，包版本 `36.4.3-20250107174145` | 通过 |
| JetPack 元包 | `nvidia-jetpack` 未安装；根据 L4T 推断为 JetPack 6.2 系列 BSP | 注意 |
| 功耗模式 | `MAXN_SUPER`，模式 ID 0 | 通过 |
| 空闲温度 | 约 49–56°C | 通过 |
| 根分区 | 157 GB，总使用 146 GB，只余 4.2 GB，使用率 98% | 严重告警 |

主要用户目录占用：`~/workspaces` 约 15 GB、`~/.cache` 约 7.7 GB、`~/fastsdcpu` 约 2.2 GB、`~/.vscode-server` 约 1.8 GB、`~/JEP` 约 1.4 GB。删除前必须逐项确认内容，不能直接批量清理。

## AI 软件栈

| 组件 | 实测版本/结果 |
|---|---|
| NVIDIA 驱动接口 | 540.4.0 |
| CUDA 运行时 | 12.6 |
| `nvcc` | 未找到；完整 CUDA 开发工具链未安装或不在 PATH |
| TensorRT | 10.7.0 |
| cuDNN | 9.6.0 |
| Python | 3.10.12 |
| PyTorch | 2.5.0a0 NVIDIA 24.08 构建 |
| Torch CUDA | 可用，设备名 `Orin`，CUDA 12.6 |
| TorchVision | 0.20.0a0 NVIDIA 构建 |
| Ultralytics | 8.3.65 |
| OpenCV | 4.11.0；FFmpeg、GStreamer 1.20.3、CUDA 均启用 |
| NumPy | 1.26.4（测试中曾被旧引擎元数据触发自动降级，已恢复） |

`nvcc` 缺失不会阻止现有 TensorRT 引擎推理，但会影响 CUDA 扩展编译和部分模型导出工作。是否安装完整 CUDA Toolkit 应在释放磁盘空间后决定。

## USB 摄像头测试

设备：Realtek `HDR CAMERA-A`，USB ID `0bda:5180`
有效节点：`/dev/video0`；`/dev/video1` 无可枚举采集格式。

相机声明支持：

- MJPEG：2592×1944、2048×1536、1920×1080 等，均为 30 FPS；
- YUYV：2592×1944 和 2048×1536 仅 2 FPS，1920×1080 仅 5 FPS；
- 因此生产程序必须显式使用 MJPEG，不能接受 OpenCV 默认 YUYV。

实测结果：

| 测试 | 结果 | 判定 |
|---|---|---|
| V4L2 原始流，2592×1944 MJPEG，300 帧 | 稳态显示 30.00 FPS，无采集错误 | 通过 |
| OpenCV `VideoCapture` CPU 解码，2592×1944，10 秒 | 相机线程约 9.59 FPS，程序约 10.10 FPS | 未通过 30 FPS |
| NVIDIA `nvv4l2decoder`，2592×1944，300 帧 | 10.775 秒完成，约 27.8 FPS（含启动开销），无解码失败 | 路径可用，需应用级优化复测 |

## YOLO/TensorRT 实测

Jetson 上已有 YOLO11n detect/OBB/classify/pose/segment 的 `.pt`、`.onnx` 和 `.engine`。现有 TensorRT 引擎加载时均警告其可能由不同设备型号生成，因此不能作为最终部署引擎。

| 模型与输入 | 采集输入 | 端到端处理 FPS | 平均调用 | P95 | 判定 |
|---|---|---:|---:|---:|---|
| YOLO11n-OBB TensorRT，1024 | 2592×1944 MJPEG，经 OpenCV CPU 解码 | 10.10 | 89.0 ms | 99.7 ms | 未通过 |
| YOLO11n detect TensorRT，640 | 1920×1080 MJPEG，经 OpenCV CPU 解码 | 15.05 | 41.2 ms | 41.9 ms | 未通过 |

OBB 引擎固定输入为 1024×1024，不能以 640 输入运行。`trtexec` 对旧 OBB 引擎做裸引擎测试时在加载阶段出现 `LLVM ERROR: out of memory`；同一引擎可由 Ultralytics 加载运行，但存在跨设备引擎警告。这进一步说明最终模型必须在当前 Jetson、当前 TensorRT 10.7 环境中重新导出。

建议模型路线：

- 第一阶段实时侦察：YOLO11n/YOLO26n detect，640，TensorRT FP16；
- 任务目标为旋转框时：YOLO11n/YOLO26n OBB，先以 640 或 768 验证 30 FPS，再评估 1024；
- 保留 2592×1944 原始图，只将硬件解码后的图缩放用于检测；检测框映射回原图后裁剪高分辨率 ROI；
- 30 FPS 验收必须包含采集、硬件解码、缩放、TensorRT、NMS/OBB 后处理和结果发布，不能只引用裸模型 FPS。

## 飞控连接状态

测试时不存在以下设备：

- `/dev/ttyACM0`
- `/dev/ttyUSB0`
- `/dev/serial/by-id`

因此 ZeroOne X6 Ultra 当前没有作为 USB CDC 或 USB 转串口设备连接到 Jetson，MAVLink 心跳、参数读取、航点上传和指令 ACK 尚未测试。

接线后优先使用飞控提供的数据/USB 接口连接 Jetson USB；若使用飞控 TELEM UART，则必须使用 3.3 V TTL，交叉连接 TX/RX 并共地，禁止把 5 V 电源脚接入 Jetson UART。具体 X6 Ultra 端口针脚必须依据厂商针脚定义核对后才能上电。

## 项目回归测试

本地 `jetson_mission` 项目：`25 passed`。

基准脚本已支持两种模式：

- 不传 `--model`：只测相机与解码；
- 传入 `.engine`：测摄像头到 YOLO 结果的端到端性能。

## 下一轮验收门槛

1. NVMe 使用率降至 85% 以下，并至少保留 20–30 GB；
2. 2592×1944 MJPEG 硬件解码后的应用采集率不低于 29.4 FPS；
3. 在本 Jetson 重新生成的 FP16 引擎无跨设备警告；
4. 端到端处理率不低于 29.4 FPS；
5. 捕获到结果 P95 不高于 33.3 ms；
6. 丢帧率不高于 1%；
7. 飞控出现稳定的 `/dev/serial/by-id/...`，MAVLink 心跳连续运行至少 30 分钟无断连。
