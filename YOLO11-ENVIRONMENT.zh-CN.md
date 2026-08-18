# CUADC YOLO11 独立环境

本项目固定使用 `ultralytics==8.3.176` 和 PyTorch `.pt` 格式的
`yolo11n-obb.pt`。不要在 Jetson 上执行会替换 NVIDIA 定制版 PyTorch 的
全局升级命令。

## 电脑训练环境

- Conda 环境名：`cuadc-yolo11`
- Python：3.9.23
- PyTorch：2.7.1+cu128
- TorchVision：0.22.1+cu128
- Ultralytics：8.3.176

激活：

```powershell
conda activate cuadc-yolo11
python -c "import torch, ultralytics; print(torch.cuda.is_available(), ultralytics.__version__)"
```

不激活环境也可以显式运行：

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run -n cuadc-yolo11 python your_script.py
```

## Jetson 部署环境

- venv：`/home/jetson/venvs/cuadc-yolo11`
- Python：3.10.12
- NVIDIA PyTorch：2.5.0a0+872d972e41.nv24.08
- TorchVision：0.20.0a0+afc54f7
- Ultralytics：8.3.176

激活：

```bash
source /home/jetson/venvs/cuadc-yolo11/bin/activate
python -c "import torch, ultralytics; print(torch.cuda.is_available(), ultralytics.__version__)"
```

服务或脚本也可以直接使用解释器绝对路径：

```bash
/home/jetson/venvs/cuadc-yolo11/bin/python your_script.py
```

该 venv 使用 `--system-site-packages` 复用 Jetson 已验证的 NVIDIA PyTorch。
在其中更新 Ultralytics 时必须使用精确版本和 `--no-deps`，不得让 pip 自动
替换 `torch` 或 `torchvision`。

## 安全基线

建立环境不改变任务安全状态。台架验收和明确授权前继续保持：

- `dry_run=true`
- 动态航点上传关闭
- 释放关闭
