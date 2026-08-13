# 照片、UTC与飞控姿态时间对齐

## 目标

每张照片必须绑定其采集时刻，而不是保存完成、MQTT到达或YOLO结束时刻。程序同时保留：

- Jetson `CLOCK_MONOTONIC`：用于帧、飞控姿态和任务事件排序，不受NTP校时跳变影响；
- UTC纳秒时间：用于文件命名、跨设备对时和赛后检索；
- MAVLink `time_boot_ms`：用于消除串口接收延迟并插值飞控位置/姿态；
- 授时状态和每层不确定度：用于判断照片能否用于高精度定位。

## 对参考方案的吸收与修正

Raspberry Pi参考程序使用Chrony、10Hz硬件PWM和V4L2 MMAP直取MJPEG，避免存储阻塞，这些经验值得保留。但它以GPIO回调时的`time.time()`四舍五入到100ms作为照片名，没有使用V4L2帧自身的内核时间戳；线程和MQTT日志中还会出现相邻照片乱序。因此，触发时刻、真正曝光帧和收到文件不能严格一一对应。

Jetson单机方案不再依赖树莓派、NFS或MQTT传照片，并做以下修正：

1. 默认使用`v4l2src → tee`：原始MJPEG分支保留照片与PTS，`nvv4l2decoder → nvvidconv`分支生成YOLO预览图；
2. 使用GStreamer buffer PTS与pipeline base time恢复V4L2内核帧时刻；
3. 用采样中点把单调时钟映射到UTC，并记录采样不确定度；
4. 按MAVLink消息的`time_boot_ms`建立飞控时钟映射，而不是按串口收到消息的时刻；
5. 位置消息前后插值到照片时刻，偏航采用最短圆弧插值；
6. 图片旁写入同名JSON侧车，保存和YOLO延迟不改变拍摄时刻；
7. `SYSTEM_TIME`只用于交叉核验Jetson UTC与飞控GPS时间，不反向修改系统时钟。

## Jetson实测

在2592×1944、MJPEG、30 FPS下读取300帧：

- GStreamer时钟减Jetson单调时钟中位差约4.3微秒；
- 探针观测最大绝对差约0.70毫秒；
- 帧时间戳到应用拿到硬解码图像平均约72毫秒、P95约99毫秒；
- 处理延迟不会进入照片采集时间，因为照片使用更早的buffer PTS；
- 首帧存在约248毫秒启动间隔，正式任务应在相机预热稳定后开始。

上述数字是一次实测，不是所有温度、USB负载下的固定保证。验收时重新运行：

```bash
python3 scripts/probe-gstreamer-timestamps.py \
  --device /dev/video0 --width 2592 --height 1944 --fps 30 --frames 300
```

## UTC质量

程序优先解析Chrony，要求：`Leap status = Normal`、Stratum不高于门槛、`abs(Last offset)`不高于10ms、`Root dispersion`不高于20ms。全部满足才设置`qualified=true`。参考程序中“offset恰好为0反而失败”的判断已修正。

若没有`chronyc`，程序退回检查`timedatectl NTPSynchronized`。这只能设置`synchronized=true`，不能设置`qualified=true`，因为没有偏差和根离散度数据。诊断命令：

```bash
/opt/cuadc-mission/.venv/bin/python /opt/cuadc-mission/scripts/check-time-sync.py
```

需要阻止未认证UTC的任务时，将配置设为：

```json
"time_sync": {
  "require_qualified_utc": true,
  "max_offset_ms": 10.0,
  "max_root_dispersion_ms": 20.0,
  "max_stratum": 4
}
```

## 照片JSON侧车

每张`photo_<UTC纳秒>_<序号>.jpg`旁生成同名JSON，记录采集UTC/单调时钟、时间戳来源、相机不确定度、Chrony质量、插值Pose、飞控boot time、插值所用遥测跨度、Pose对齐误差、飞控`SYSTEM_TIME`交叉差值和保存完成时间。

复盘程序应按`capture_monotonic_ns`或`capture_unix_ns`排序，不能按文件mtime、MQTT到达顺序或YOLO完成顺序排序。

## 精度限制

普通USB UVC相机没有共享飞控PPS/硬件快门线时，软件只能使用驱动报告的SOE/PTS。实测可把Jetson时间域转换误差压到约1ms量级，但绝对曝光时刻仍受相机固件、曝光定义、USB驱动和Rolling Shutter影响，不能仅凭软件宣称亚毫秒级“光学曝光—飞控姿态”绝对同步。

若以后需要继续提高，应使用带硬件trigger和曝光输出的global-shutter相机，并把飞控GPS PPS接入Jetson PPS/硬件时间戳输入，采用Chrony+PPS或PTP硬件授时。
