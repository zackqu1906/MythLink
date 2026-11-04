# IMU + 戒指语音采集 v2

更新：2026-11-02。此次实现的是可追溯的双路采集和独立标注；现有 train/evaluate 仍为 IMU 基线，尚未实现或训练融合模型。没有连接真实设备，没有执行固件烧录。

## 论文依据与采集决策

1. Zhao et al., **Raise to Speak: An Accurate, Low-power Detector for Activating Voice Assistants on Smartwatches**, KDD 2019。
   [Apple 官方页面](https://machinelearning.apple.com/research/raise-to-speak-an-accurate-low-power-detector-for-activating-voice-assistants-on-smartwatches)。动作 CNN、近场语音 CNN 和决策策略分工处理自然抬手说话。本次据此采集动作与语音的交叉对照，同时记录声源、距离及日常活动。没有把手表性能或能耗外推到戒指。
2. Buddi et al., **Efficient Multimodal Neural Networks for Trigger-less Voice Assistants**, Interspeech 2023。
   [Apple 官方页面](https://machinelearning.apple.com/research/efficient-multimodal-neural-networks) · [原文 §3.1–3.3、§4](https://www.isca-archive.org/interspeech_2023/buddi23_interspeech.pdf)。分别训练动作四分类和语音二分类，将 10 Hz 输出对齐后训练 GRU 意图二分类；比较概率与 logits 融合。动作、语音、交互意图分别标注。这里采用这个数据组织思路；戒指采六轴，论文为手表三轴加速度，特征与性能并不相同。
3. Krishna et al., **Modality Dropout for Multimodal Device Directed Speech Detection using Verbal and Non-Verbal Features**，Apple 2023 年发布，ICASSP。
   [Apple 官方页面](https://machinelearning.apple.com/research/modality-dropout)。研究模态缺失时的融合鲁棒性，使用声学、文本、ASR 与韵律等信息，**不是 IMU 论文**。本次仅借鉴保留缺失状态、不要把传输丢失等同于无语音；尚未实现 modality dropout。

上述文献不确定本戒指的音频时间戳参考点，也不能证明两路硬件同步。没有加入 ASR、语言模型、云端上传或语音助手调用。

## 连接与采集生命周期

扫描 → 选择设备 → **连接戒指** → 读取 INFO / 显示实际固件 → 待机 → **开始采集** → **停止并保存** → 保持已连接，可继续录制。

- 连接时停止残留的麦克风/IMU 流，建立明确待机状态；待机不录音。
- 一个持久 asyncio/BLE 连接接收两种包，不为麦克风再建第二条连接。
- 正式录制恢复 NORMAL，先启动麦克风，再启动 raw IMU；录制包含 5 秒准备引导。两路持续保存，不由预测触发来截取正例。
- **断开连接**会停止两路并释放 BLE。若正在采集，保存部分文件、标记不完整；不会悄悄重连后接到同一时间轴。
- **停止并保存**是正常提前结束；没有完成的动作尾段仍须标 unknown。录制是否正常结束与信号是否适合训练是不同检查。
- 5 秒没有请求的数据流即失败。连续全零音频显示警告并保留原包；真实安静与坏信号都可能接近零，不能由 RMS 自动生成无语音标签。
- 单独麦克风、单独 IMU 只用于 sanity 检查。正式场景强制双路。

## 采集矩阵

每个 participant/visit 都包含以下场景，整批划入同一 train/val/test。更换佩戴或跨天使用新 visit。

| 场景 | 动作 | 声音 / 意图 | 用途 |
| --- | --- | --- | --- |
| raise_speak | 抬到嘴边 | 佩戴者向戒指发起交互 | 正例 |
| raise_silent | 相同抬手与保持 | 不说话 | 运动正例、意图负例 |
| raise_conversation | 相同抬手与保持 | 对别人说话 | 两路均活跃的难负例 |
| speak_no_raise | 不抬手 | 对别人说话 | 音频单独活跃的负例 |
| background_speech | 自然姿态 | 旁人 / 播放器语音 | 声源混淆负例 |
| daily | 打字、喝水、摸脸等 | 各种自然环境声音 | 连续负例 |
| sanity | 静止 / 转动 / 发声 | 只做传感器检查 | 不提供正式监督 |

先单路、再双路，各做几段短检查，重启/反复启停后复测，检查非零声音是否随实际说话变化。检查后再采小批次。自然改变语句、音量、手部起点、动作速度；包含抬起中开口、到嘴边即开口和稍后开口。不要让模型只学会固定倒计时、提示音或固定句子；页面只用视觉提示，不自动发出提示音。

界面引导生成的动作阶段只是草稿。语音、意图区间初始为空，不能自动视为静默/无意图。音频播放与标注均在本地。

## 数据与时间

- `wire.jsonl`：完整 RX/TX，主机 monotonic ns / Unix ns。
- `imu.csv`：原始 int16 与 SI 六轴、设备秒、包序号、分段；200 Hz 包内回填的限制不变。
- `audio.wav`：由收到的完整 ADPCM 块解码得到的 16 kHz / mono / int16；不归一化、不降噪、不截静默。
- `audio_blocks.jsonl`：每块序号、uint32 uptime、解绕后的包时间、主机首末分片时间、WAV 起始样本、样本数、分段、RMS/峰值/全零状态。
- `audio_events.jsonl`：缺块、缺片、时间异常等。缺失部分不在 WAV 中补零；WAV 是有效块串联，不能仅用 WAV 秒与 IMU 秒对齐。
- `metadata.json`：版本、环境、场景、随机提示种子、结束状态、计数及所有原始文件哈希。
- `annotations.json`：动作阶段，IMU 相对秒。
- `multimodal_annotations.json`：声音来源、交互意图、对齐复核记录、来源哈希；每次保存旧版归档。

音频包只有一个 uptime_ms，附件没有明确其对应采样块首、块尾还是发送时刻。原值必须保留。对齐未确认时仍可采集和分别标注，**不能直接生成可信融合训练窗口**。之后需核对固件或重复测量共同事件，检查开头/结尾偏差和漂移；若时间戳实际为发送时刻且延迟不稳定，简单常数偏移不够，需要补充固件采样时间。

当参考点经确认后，块首时间候选为：包时间（若是块尾则减该块时长）+ 测得 offset_s − IMU origin。跨 uint32 回绕需统一 epoch。此公式只定义记录中的 offset，当前采集器不据此声称已实现精确对齐。未来窗口还要剔除缺口、限制跨模态延迟并验证因果可用性，不能使用尚未到达的整块音频作弊。

## 三种标签

1. **动作**：background / raising / near_mouth / lowering / unknown。v2 将口旁抬手的运动与意图分开：抬手不说话、抬手但侧谈也标相应运动阶段。喝水/摸脸等其他运动仍标 background，保留具体活动。新旧标签语义不同，构建器禁止混用 v1 与 v2；旧记录需人工统一复核后迁移。
2. **声音来源**：wearer_speech / other_speech / mixed_speech / non_speech / unknown，使用 WAV 时间。训练一般 speech/no-speech 分支时可将前三类映射为 speech；声源标签为误触分析保留。咳嗽/风噪不是缺包，也不自动算语音。
3. **意图**：directed / not_directed / unknown，使用 IMU 时间，来自参与者/观察者确认。它不由音量、场景名或动作分类推断。区间表示实际交互意图，未来事件评价还应记录一次交互的起点与结束。

语音复核需要实际试听；意图与动作需要观察/录像。对齐复核是单独状态，保存语音标签不会自动使对齐变成已确认。真实零信号、缺片、未知标签都必须保留区别。

## 后续模型实验边界

现有 CLI 的 prepare/train/evaluate 仍只做 IMU 运动基线，manifest 明确 objective。后续按同一冻结划分比较 IMU-only、audio-only、规则融合、时序融合。拟采用：IMU 动作分支 4 维 + 音频语音分支 2 维 → 对齐到 10 Hz → 概率/logits 融合 → GRU 意图分支 2 维；这是实验设计，不是本次已训练模型。

先分别训练分支，再训练融合层；验证集选择结构与阈值。融合训练使用训练批次输出，不能用验证/测试标签反向训练。必要时训练集内部按批次做交叉拟合，评估分支对训练数据过拟合带来的融合偏差。保留语音起点、抬手起点、到嘴边时刻以及真实主机接收时间，评价漏检、误触/小时、重复触发、端到端延迟及缺失模态失效，不仅看窗口准确率。

## 用户确认的语音分支：原始 decoupled CNN

采用 `/Users/admin/Downloads/ProximicAsr_decoupled/src/proximic_ring/model.py` 中的原始 `CnnNet8(20, 2)`。没有采用 100 ms 的 dense_stats 实验结构。原始网络、重采样、特征代码和两份系数已逐字冻结到 `vendor/decoupled_original/`，每个文件的来源路径与哈希在该目录 PROVENANCE.json。`ring_raise/audio_model.py` 提供未训练模型与前端；旧 checkpoint 未复制、未加载。

数据路径：16 kHz PCM16 的最近 1 秒（16000 点）→ /32768 转浮点 → 原始 16→8 kHz 下采样 → 原始 20×201 特征 → Conv1d(20→50,k3) / BN / ReLU / MaxPool(k3) → Conv1d(50→100,k3) / BN / ReLU → 时间最大池化 → FC(100→20→2)。未来每 100 ms 更新一次，与 IMU 分支同为 10 Hz 输出；两支统一使用截止预测时刻的最近 1 秒，IMU 每窗为 6×200 个值。统一窗口长度不等于两路已经同步，仍须复核音频和 IMU 时间映射。

原 decoupled 训练任务为 near_speech / realistic_non_target_audio（far 与 artifact 属非目标），并非一般有语音/无语音。新语音分支使用独立复核的 speech / non_speech 标签重新训练，不能仅修改旧权重类别名称。旁人、远处和播放语音也有语音，应在一般 VAD 分支中为 speech，同时在意图分支中通常为 not_directed。保留声音来源标签以便做这个区分。

前端的居中 STFT/反射填充只允许作用于截止当前的已知 1 秒窗，不允许从全录音提取时偷看窗口之后的数据。端到端部署前须核对重采样边界、音频块到达时间以及处理耗时。此次只验证前端形状与原始 CNN 兼容，还没有训练真实 VAD 或融合模型。
