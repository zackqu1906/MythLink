# JEV / Nimble 意图分类测试

同一个 `jev_intent_tester.py` 支持 `--backend jev`（默认）和 `--backend nimble`。
两者使用相同的听写/编辑规则、已有文本和当前语句。`-c` 是旧参数，当前真实模型 prompt 不使用它。

## Mac 首次准备

需要 Apple Silicon、原生 Python 3.12。官方 MLX scorer 当前不支持量化权重，9B 权重约 18 GB，运行和 CPU 合并需要额外内存；下载缓存与合并模型都会占用磁盘。不要把 165 MiB 的适配器大小当作整个模型的大小。
下列步骤会联网下载源码、依赖、模型。分开使用准备环境和 MLX 环境，避免修改项目现有 `.venv`。

```bash
cd /Users/admin/Downloads/ProximicVoice-main
git clone https://github.com/bespokelabsai/nimble.git pretrained_models/nimble
python3.12 -m venv pretrained_models/nimble/.venv-prepare
pretrained_models/nimble/.venv-prepare/bin/python -m pip install torch==2.8.0 -r pretrained_models/nimble/requirements/training.txt
pretrained_models/nimble/.venv-prepare/bin/python tools/prepare_nimble.py

python3.12 -m venv pretrained_models/nimble/.venv-mlx
pretrained_models/nimble/.venv-mlx/bin/python -m pip install -r pretrained_models/nimble/requirements/mlx.txt
```

准备脚本会下载官方适配器及其指定版本的 Qwen3.5-9B，检查 prompt 合约，合并后写入 `.cache/nimble-model.json`。已有此配置时不会重复准备。
来源：[官方安装说明](https://github.com/bespokelabsai/nimble#quickstart)、[官方模型](https://huggingface.co/bespokelabs/Bespoke-Nimble-9B)。

## 交互测试（推荐，模型只加载一次）

```bash
cd /Users/admin/Downloads/ProximicVoice-main
pretrained_models/nimble/.venv-mlx/bin/python tools/jev_intent_tester.py --backend nimble --interactive
```

进入后逐行输入：

```text
:doc 我在咖啡店和牛奶
喝牛奶
:doc 我在咖啡店
喝牛奶
:quit
```

`:doc` 不会因一次判断而自动改变。模型只分类，不实际修改已有文本。

## 单次、批量与其他选项

```bash
pretrained_models/nimble/.venv-mlx/bin/python tools/jev_intent_tester.py --backend nimble --once -d '我在咖啡店和牛奶' -u '喝牛奶'
pretrained_models/nimble/.venv-mlx/bin/python tools/jev_intent_tester.py --backend nimble --id edit_01 --repeat 5
```

- `--with-uncertainty`：额外判断信息充足度。
- `--no-save`：不保存结果；否则在 `tools/results` 写 JSON 和 CSV。
- `--nimble-repo /绝对路径/nimble`：自定义源码目录。
- `--nimble-config /绝对路径/nimble-model.json`：自定义模型配置。
- `--nimble-device cuda`：使用官方 CUDA scorer，需另行准备 NVIDIA 环境。
- 不下载模型只检查旧离线流程，可使用 `--mock`；mock 不验证 Nimble 模型或安装是否成功。

Nimble 输出两个类别的概率。它没有 JEV 同义的 confidence，所以该栏显示 `–`，不会拿最大概率或自制公式冒充；概率也未经校准。
总耗时是本地 score 调用耗时；额外的“本地计算”来自官方 scorer 的 metrics，包含其评估流程，不是纯 GPU 内核时间。
模型加载在计时前完成，首次评分仍可能含编译/预热开销。Nimble 批量测试强制串行，避免同一 GPU 模型并发竞争。

2,048-token 限制包含规则、示例、schema 和已有文本；超限会报错，不会静默截断。长文本需要缩短后再测试。
