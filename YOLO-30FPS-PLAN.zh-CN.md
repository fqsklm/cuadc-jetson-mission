# Orin NX 16GB + 2K USB 相机的 PT-only YOLO 性能方案

更新时间：2026-08-18（Asia/Shanghai）

## 固定模型格式

本项目最终只使用 Ultralytics/PyTorch `.pt` 模型：

- `.pt` 是训练、验证、部署和备份的唯一权威模型；
- 不生成、不部署、不加载其他推理模型格式；
- 配置校验和 benchmark 会拒绝非 `.pt` 模型；
- Jetson 必须保留与 JetPack 匹配的 NVIDIA CUDA 版 PyTorch 和 TorchVision，禁止用普通 PyPI Torch 覆盖。

## 推荐模型组合

当前已实测基线是 Orange 单类别 YOLO11n detect、输入 640。后续模型选择遵循：

- 外层目标定位优先使用 `n` 级模型；
- 如果比赛目标需要旋转框，先测 OBB 640/768，再决定是否升至 1024；
- 数字或图形 ROI 可使用独立 classify 模型；
- 人员安全检测必须使用独立、经过验证的 detect `.pt`，不能与靶标类别混成未经验证的单模型；
- 相机始终保留 2592×1944 原始 JPEG，只把 1024×768 GStreamer/NVIDIA 解码预览送入推理。

训练可在 RTX 工作站完成，最终 `.pt` 原样复制到 Jetson。每次模型更新必须记录 SHA-256、训练数据版本、Ultralytics/PyTorch 版本、输入尺寸和精度指标。

## 频率策略

当前任务配置 `vision.detect_hz=10`，因此每次推理只需稳定低于 100 ms。不要仅为了追求相机 30 FPS 而牺牲识别精度或运行稳定性。

如果后续确实要求逐帧 30 Hz 检测，完整帧周期只有 33.3 ms，预算还包括：

- USB 2K MJPEG 接收与硬件解码；
- 缩放/letterbox；
- PyTorch CUDA 推理与结果解析；
- ROI 分类、照片留档、MAVLink 和坐标解算。

只有端到端测试通过，才能声称满足目标频率；Ultralytics 单独模型测速不能代替任务流水线验收。

## benchmark 命令

```bash
cd /home/jetson/cuadc-jetson-mission
PYTHONPATH=src python3 scripts/benchmark-camera-yolo.py \
  --backend gstreamer \
  --device /dev/v4l/by-id/usb-BLC-260423-J_HDR_CAMERA-A_01.00.00-video-index0 \
  --model /opt/cuadc-mission/models/target.pt \
  --task obb \
  --width 2592 --height 1944 --camera-fps 30 \
  --fourcc MJPG --preview-width 1024 --preview-height 768 \
  --imgsz 640 --seconds 60 --alignment-holdback-ms 80
```

30 Hz 阶段测试的判定条件：

- `camera_fps_observed >= 29.4`；
- `processed_fps >= 29.4`；
- `inference_ms_p95 <= 33.3 ms`；
- `capture_to_result_ms_p95 <= alignment_holdback_ms + 33.3 ms`；
- `decode_sources` 只有 `gstreamer-preview`；
- 跳帧率不超过 1%；
- 无 USB、CUDA、PyTorch 或相机错误。

正式验收仍需连续运行两小时，并叠加照片留档、目标融合和 MAVLink 只读负载。动态航点上传与释放在各自台架验收和用户明确授权前保持关闭。

## 2026-08-18 PT 实测基线

Orange 单类别 `best.pt`、detect 640、2592×1944 MJPEG 30 FPS 相机、1024×768 GStreamer 预览、80 ms 姿态对齐 holdback，PT-only 清理后连续 60 秒结果：

| 指标 | 结果 |
|---|---:|
| 相机观察 FPS | 29.99911 |
| 端到端处理 FPS | 29.93098 |
| 已处理/可处理帧 | 1796/1798 |
| 跳帧率 | 0.111% |
| 解码来源 | 全部 `gstreamer-preview` |
| 推理耗时均值 | 29.929 ms |
| 推理耗时 P95 | 30.501 ms |
| 采集到结果 P95 | 110.924 ms（含 80 ms holdback） |
| 阶段结果 | 通过 |

这个结果证明当前 Orange detect-640 `.pt` 能达到阶段性 30 FPS 阈值，不代表未来 OBB、1024 输入或完整任务并行负载已经通过。
