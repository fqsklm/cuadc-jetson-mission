# Orin NX 16GB + 2K USB 相机的 YOLO 30 FPS 方案

## 推荐模型组合

正式侦察基线：

- 外层目标定位：`YOLO26n-obb`，训练/导出输入 `1024`，TensorRT FP16；
- 精度对照：`YOLO26s-obb`，输入 `1024`，TensorRT FP16；
- 数字 ROI：`YOLO26n-cls` 或 `YOLO26s-cls`，输入 `224`/`320`；
- 图形 ROI：`YOLO26s-cls`，输入 `224`/`320`；
- 安全人员检测：独立 `YOLO26n` detect，输入 `640`，可降低运行频率但不能与靶标类别混成一个不经验证的模型。

不要直接用整张 2560×1440 或 2592×1944 作为网络输入。相机保持 2K 采集，用于保存原始证据和从原图裁剪高分辨率 ROI；外层检测只缩放到 1024。对于远距离小靶标，1024 通常比 640 更合理，但最终取舍由真实飞行测试集的召回率决定。

训练在带 RTX 5070 Ti 的电脑上完成。Jetson 只负责验证、导出/构建 TensorRT engine 和飞行推理。TensorRT engine 必须在目标 Jetson、当前 TensorRT/CUDA 环境中生成并保存对应版本信息。

## 为什么先选 n，不直接选 s/m

30 FPS 的帧周期只有 33.3 ms，预算还包括：

- USB 2K MJPEG 接收和解码；
- 2K → 1024 resize/letterbox；
- TensorRT OBB 推理与输出解析；
- ROI 分类、照片留档、MAVLink 和坐标解算。

`n-obb@1024 FP16` 给整个系统留出足够余量。若实机端到端 P95 明显低于 25 ms，且 `s-obb` 在独立飞行测试集上显著提高召回，才升级为 `s`。`m/l/x` 不作为 30 FPS 首选。

## 30 FPS 验收

先确认相机确实提供目标 2K MJPEG 30 FPS：

```bash
v4l2-ctl -d /dev/v4l/by-id/你的相机 --list-formats-ext
```

然后对每个 engine 运行至少 60 秒：

```bash
python3 scripts/benchmark-camera-yolo.py \
  --device /dev/v4l/by-id/你的相机 \
  --model models/target-yolo26n-obb-1024-fp16.engine \
  --width 2592 --height 1944 --camera-fps 30 \
  --fourcc MJPG --imgsz 1024 --seconds 60
```

正式验收应连续运行两小时，并满足：

- 实际相机格式和分辨率等于请求值；
- `camera_fps_observed ≥ 29.4`；
- `processed_fps ≥ 29.4`；
- `capture_to_result_ms_p95 ≤ 33.3 ms`；
- 跳帧率 ≤ 1%；
- 无 USB 读取错误、CUDA/TensorRT 错误或热降频；
- 同时运行拍照留档与 MAVLink 后仍通过。

官方模型 benchmark 只报告推理阶段，不能代替上述端到端验收。
