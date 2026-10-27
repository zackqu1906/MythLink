# 中文文本输入与受限条件下文本输入：首轮文献调研

检索日期：2026-10-25。主体范围：2021—2026 年；另列 3 篇与中文笔画／戒指输入直接相关的早期基础工作。

面向项目：Mythlink / ProximicVoice 的戒指触摸与笔画输入、近场语音输入、候选选择和文本纠错。本文是定向调研与选读笔记，不是穷尽式系统综述。

## 1. 检索范围与阅读说明

检索围绕 Chinese text entry、Pinyin input method、stroke-based input、ring text entry、constrained text input、one-handed / eyes-free text entry、silent speech、abbreviated input、LLM-assisted text entry 等组合展开。优先采用论文原文、出版社／会议页面、ACL Anthology、作者项目页及作者代码仓库。

以下每项分别记录方法、公开证据、适用边界和项目启发。“项目启发”是本次调研的分析或待验证假设，不代表原论文已经证明。能够取得全文时核对相关实验段落；部分条目仅能核对作者摘要／公开原文片段，单独注明。

指标解释：WPM 是论文采用的词／分钟指标，英文研究通常按 5 个字符折算一个词；CPM 是字符／分钟。英文 WPM 不能直接等同于中文汉字／分钟。手势分类准确率、字母识别率、词准确率、字符错误率 CER 和词错误率 WER 也不能互换。不同训练时间、词表范围和纠错规则下的数值不宜作为统一排行榜。

## 2. 按限制条件理解研究空间

| 限制条件 | 典型路径 | 本次重点文献 | 对项目的意义 |
| --- | --- | --- | --- |
| 没有实体键盘、输入面积很小 | 戒指手写、滑动、旋转、指间微手势 | WritingRing、RingGesture、RingByte、SlideRing | 戒指输入的直接相关工作 |
| 只有少量可区分动作 | 编码压缩、歧义解码、候选选择 | 中文 GPT 拼音、CNMBERT、HeadText、SpeakFaster | 动作少与歧义增加之间的权衡 |
| 不能大声说话或环境噪声大 | 近场语音、唇读、深度感知、无声语音 | ProxiMic、Watch Your Mouth、LipType、ToneLipNet | 语音入口与辅助消歧 |
| 不方便持续看键盘 | 表面敲击、身体动作、音频反馈 | TapType、FineType、HeadText | 减少瞄准与视觉切换 |
| 用户存在运动或认知限制 | 减少动作、增加上下文、降低纠错负担 | SpeakFaster、老年中文输入研究 | 需要分别验证目标用户群 |
| 中文同音、简拼、多候选 | 约束解码、领域上下文、音形辅助 | GPT 拼音、临床输入、中文 ASR 纠错 | 中文输入的核心成本并不只有动作识别 |

## 3. 中文输入、编码与候选解码

### [01] Exploring and Adapting Chinese GPT to Pinyin Input Method

- **出处**：Minghuan Tan 等，ACL 2022。中文算法研究。
- **方法**：把中文 GPT 用于拼音转汉字；通过拼音上下文增强和区分同音字的训练目标，改善仅输入声母时的歧义。
- **证据**：构建了覆盖 15 个领域、27 万条样本的数据集。冻结 GPT 在全拼上表现较好，在简拼上明显下降；所提策略改善了各领域的简拼结果。
- **边界**：离线解码表现不能直接证明用户输入更快，也没有验证戒指动作识别噪声。
- **项目启发**：可以将“笔画／手势的物理识别”与“汉字／词语的语言解码”分开比较；尝试在候选约束内使用上下文，而非让模型自由改写。
- **来源与阅读范围**：[ACL 正式页面](https://aclanthology.org/2022.acl-long.133/)、[原文](https://aclanthology.org/2022.acl-long.133.pdf)。已核对摘要与方法开头。DOI：10.18653/v1/2022.acl-long.133。

### [02] CNMBERT: A Model for Converting Hanyu Pinyin Abbreviations to Chinese Characters

- **出处**：IJCNN 2025；预印本首次发布于 2024 年。中文算法研究。
- **方法**：将拼音缩写还原为填空任务，用 multi-mask 与 Mixture of Experts 结构进行转换。
- **证据**：预印本报告在 10,373 条测试样本上达到 51.86% accuracy、61.53% MRR。作者仓库提供代码及正式会议引用。
- **边界**：任务主要涉及文本中的缩写还原，不等同于连续交互式输入法；该准确率也说明极度压缩后仍有大量歧义。
- **项目启发**：作为简拼、短码输入的算法参考。应比较候选命中率与补充输入成本，不能只看按键减少数量。
- **来源与阅读范围**：[预印本](https://arxiv.org/abs/2411.11770)、[作者代码与正式引用](https://github.com/IgarashiAkatuki/CNMBERT)。已核对作者摘要及仓库。正式 DOI：10.1109/IJCNN64981.2025.11228899。

### [03] Using less keystrokes to achieve high top-1 accuracy in Chinese clinical text entry

- **出处**：Tao Li、Lei Yu、Liang Zhou、Panzhang Wang，Digital Health，2023。中文现场研究。
- **方法**：将电子病历中的领域上下文与输入建议连接，使用行内弹层帮助医生用更少按键命中首选词。
- **证据**：952 名医生参与现场测试。相对对照组，TLK rate（少按键下命中首选的比例）提高 40.3%，每字符按键数降低 48.3%。
- **边界**：医疗领域、结构化任务的收益不应直接外推到开放聊天；TLK rate 的相对提升也不是“输入速度提高 40.3%”。
- **项目启发**：应用场景、前文与用户常用词可能比继续扩展手势数量更有效。评价戒指输入时应记录首选命中、候选浏览和选择耗时。
- **来源与阅读范围**：[期刊原文](https://journals.sagepub.com/doi/10.1177/20552076231179027)、[PubMed](https://pubmed.ncbi.nlm.nih.gov/37256013/)。已核对摘要与公开正文相关段落。DOI：10.1177/20552076231179027。

### [04] The Influence of Input Method and Chinese Character Complexity on the Elderly Using Smartphones to Input Information – A Study from China

- **出处**：International Journal of Human–Computer Interaction；2024-01-16 在线发表，卷期为 2025，41(1):471–489。
- **方法**：访谈结合实验，分析输入方式、汉字复杂度对老年用户的输入表现、肌肉负荷、认知负荷和体验的影响。
- **证据**：汉字复杂度增加时表现下降，手写尤其受影响；作者建议让复杂汉字也可通过手写拼音输入，并减少手写功能的启动步骤。
- **边界**：本轮只能核对出版社摘要，未取得完整实验表，故不报告未核实的样本量与具体速度。
- **项目启发**：五类笔画未必天然等于低认知负担；笔顺回忆、笔画归类和动作执行应分别测量。
- **来源**：[出版社摘要](https://www.tandfonline.com/doi/abs/10.1080/10447318.2023.2301261)。DOI：10.1080/10447318.2023.2301261。

### [05] Investigating Context-Aware Collaborative Text Entry on Smartphones using Large Language Models

- **出处**：Weihao Chen 等，CHI 2025；系统名 CATIA。已验证中文使用场景。
- **方法**：利用屏幕内容、时间、位置和活动等上下文给出文本建议，用户可接受、编辑或重新生成。
- **证据**：36 名中文用户、7 天真实环境研究；超过 80% 的场合给出了合适建议。参与者为同一高校的年轻学生。
- **边界**：“合适建议”不等同于逐字准确复现预定文本；研究包含表达生成，不能与严格抄录输入混为一谈。
- **项目启发**：项目可在搜索、短回复、跨应用编辑中引入局部上下文，但应把“准确录入”与“辅助表达”设计成可辨识的任务。
- **来源与阅读范围**：[作者原文](https://pi.cs.tsinghua.edu.cn/wp-content/uploads/2025/06/3706598.3713944-2.pdf)。已核对摘要、参与者、场景与局限部分。DOI：10.1145/3706598.3713944。

### [06] Benchmarking Large Language Models for Chinese and Japanese IMEs: Phonetic-to-Character Generation and Textual Error Correction

- **出处**：Yuchun Zou、Tedd Lee、Xiaodi Fan、Jun Li，LREC 2026，4290–4311。
- **方法**：比较 LLM 与传统基线在拼音／罗马字转文字、文本纠错上的表现，同时记录准确性与运行效率。
- **证据**：公开数据、代码与结果；较强 LLM 在消歧和复杂错误纠正上有优势，但存在准确性与延迟的权衡。
- **边界**：作者明确说明该版本没有真人交互实验，不能由离线指标直接推导输入效率。语义相似也不能代替精确录入。
- **项目启发**：用于选择中文候选排序与纠错模型，以及建立 CER、首 token 延迟、完成时间等评价；仍需本项目的真实交互测试。
- **来源与阅读范围**：[ACL 正式页面](https://aclanthology.org/2026.lrec-1.337/)、[作者全文](https://www.junli.io/papers/yuchun-lrec26.pdf)、[代码与数据](https://github.com/junli-cuny/llm-ime-benchmark)。已核对摘要、指标和局限。DOI：10.63317/42jiimjriyga。

### [07] Large Language Model Should Understand Pinyin for Chinese ASR Error Correction

- **出处**：Yuang Li 等，ICASSP 2025；预印本 2024。
- **方法**：PY-GEC 使用识别文本及拼音信息，结合拼音与汉字转换的多任务训练，纠正中文 ASR 错误。
- **证据**：仅使用合成错误训练，推理使用 one-best 假设；在 AISHELL-1、Common Voice 上优于只输入文本的纠错方案。
- **边界**：本轮以预印本摘要核对机制与结论，没有将其结果外推为耳语或戒指麦克风效果。
- **项目启发**：近场语音识别后的同音纠错，可以优先保留语音／拼音约束，再结合前文和专名词表，而不是无约束润色。
- **来源**：[作者预印本](https://arxiv.org/abs/2409.13262)。正式 DOI：10.1109/ICASSP49660.2025.10887651。

### [08] A Tone-Assisted Chinese Silent Speech Input Method for Virtual Reality

- **出处**：Xinyu Zhang 等，International Journal of Human–Computer Interaction；2025-08-18 在线发表，卷期为 2026，42(6):4140–4171。
- **方法**：用额外的声调输入辅助无声唇读。先探索 5 种声调输入方式，再构建多模态模型 ToneLipNet。
- **证据**：出版社摘要报告，相比没有声调信息的基线，准确性显著提高；还进行了 5 天使用研究，考察适应与双通道协调。
- **边界**：本轮仅核对出版社摘要，不填入无法核实的 WPM、CER 或最佳声调动作细节。
- **项目启发**：很适合启发“主要通道输入 + 少量手势补充消歧”的中文方案。能否用戒指补充声调、笔画或其他辨别线索仍需实验。
- **来源**：[出版社页面](https://www.tandfonline.com/doi/abs/10.1080/10447318.2025.2537784)。DOI：10.1080/10447318.2025.2537784。

## 4. 戒指、微手势、无键盘与手写输入

### [09] WritingRing: Enabling Natural Handwriting Input with a Single IMU Ring

- **出处**：Zhe He 等，CHI 2025。项目高度相关。
- **方法**：单 IMU 戒指戴于指根，使用 TCN 与流式 LSTM 重建指尖二维手写轨迹，再接手写识别。
- **证据**：20 人采集的数据集；作者报告平均轨迹误差 1.63 mm，字母识别率 88.7%，词识别率 68.2%；将词表限制到 3,000 词时为 84.36%。
- **边界**：主要量化识别结果为英文字母／单词；论文展示中文等书写能力，不等于已经完成大词汇量中文输入效率验证。词表限制对结果影响很大。
- **项目启发**：对比“恢复完整手写轨迹”与“直接识别五类笔画”两种层级，考察需要的训练数据、错误可解释性和硬件条件。
- **来源与阅读范围**：[作者论文页](https://xiyuanshen.com/publication/2025-04-25-writingring-number-3)、[作者原文](https://pi.cs.tsinghua.edu.cn/wp-content/uploads/2025/06/3706598.3714066.pdf)。已核对作者摘要及公开原文片段；全文下载解析受大小限制。DOI：10.1145/3706598.3714066。

### [10] RingGesture: A Ring-Based Mid-Air Gesture Typing System Powered by a Deep-Learning Word Prediction Framework

- **出处**：作者预印本，2024，arXiv:2410.18100；本轮未核实正式会议归属。英文词输入。
- **方法**：戒指 IMU 控制空中滑行轨迹，用电极标记起止；Score Fusion 融合轨迹解码、空间拼写纠错和上下文语言模型。
- **证据**：报告平均 27.3 WPM、峰值 47.9 WPM；相对 Naive Correction，未纠正字符错误率改善 28.2%，速度提高 55.2%。
- **边界**：峰值不能当作普通用户平均表现；这些改善来自其特定基线与实验，不代表对任意输入法的提升。
- **项目启发**：物理轨迹与语言模型联合评分比先做硬分类、再孤立纠错更值得研究。可保留前几种笔画假设，延后到汉字／短语层消歧。
- **来源与阅读范围**：[作者预印本](https://arxiv.org/abs/2410.18100)。已核对作者摘要。

### [11] RingByte: Enhancing Text-Entry Practicality via A Singular Wearable Rotating Smart Ring

- **出处**：Rucheng Wu、Tao Ni、Zehua Sun、Jiande Sun、Weitao Xu，UIST 2025。
- **方法**：双层旋转戒指，外层 IMU 感知旋转操作；借用 T9 思路构成八个 45° 扇区，通过域适配应对不同手指、位置和用户的差异。
- **证据**：论文公开片段报告坐姿下 22.2 WPM，输入操作识别率超过 96%；设计包含候选选择与确认动作。
- **边界**：操作分类率不是整句字符准确率。其硬件具有可旋转外层，不能直接视为普通固定戒指的性能。
- **项目启发**：是紧凑戒指输入的直接相关工作，值得重点读它的分段、候选交互、跨位置适配，以及硬件改变带来的收益。
- **来源与阅读范围**：[作者原文](https://taoni.xyz/taoni_files/paper/uist25_ringbyte.pdf)。已核对原文中交互结构、对比表和用户研究公开片段；全文解析受大小限制。DOI：10.1145/3746059.3747717。

### [12] SlideRing: Robust Dual-IMU Thumb-to-Finger Text Input for Virtual Reality

- **出处**：Tao Sun、Nuo Jia、Dawei Jiao，Sensors，2026-07-03，26(13):4210。
- **方法**：双拇指 IMU 感知拇指对其他手指的滑动／触碰，用 30 种指间动作映射字母；比较人体工学布局与 QWERTY 兼容布局。
- **证据**：5 天后，人体工学布局由 7.43 提升到 15.75 WPM，QWERTY 布局由 10.55 提升到 15.25 WPM；离线目标手指准确率 96.5%、动作类型准确率 94.2%。
- **边界**：双手与双传感器方案，主要验证字母输入；离线两项分类率不能合并成输入文本准确率。
- **项目启发**：布局的初期易学性与长期动作效率可能不同，研究至少应同时报告初用表现和练习曲线。
- **来源与阅读范围**：[期刊原文](https://www.mdpi.com/1424-8220/26/13/4210)、[开放全文](https://pmc.ncbi.nlm.nih.gov/articles/PMC13364087/)。已核对摘要与公开实验／讨论段落。DOI：10.3390/s26134210。

### [13] FineType: Fine-grained Tapping Gesture Recognition for Text Entry

- **出处**：Chentao Li、Ziheng Xi、Jianjiang Feng、Jie Zhou，CHI 2025。
- **方法**：腕带 IMU 加红外相机，识别敲击手指组合与 3 种手指姿态，把单手映射到接近完整的键盘符号集合。
- **证据**：28 人手势数据；10 人输入实验在约 2 小时练习后达到平均 35.1 WPM、5.1% CER。
- **边界**：输入实验以英文及扩展字符任务为主。它依赖腕带红外相机，也需要学习动作映射，不能只比较其 WPM 与单戒指方案。
- **项目启发**：要权衡“更多可区分动作”与“更少动作加语言解码”。数字、标点、专名等不适合强语言预测的任务可能更需要明确编码。
- **来源与阅读范围**：[作者原文](https://ivg.au.tsinghua.edu.cn/~jfeng/pubs/Li_CHI25_FineType.pdf)。已核对摘要、硬件、数据与任务设置。DOI：10.1145/3706598.3714278。

### [14] TapType: Ten-finger text entry on everyday surfaces via Bayesian inference

- **出处**：Paul Streli 等，CHI 2022。arXiv 上传时间为 2024，不应误记为 2024 年新论文。
- **方法**：双腕惯性传感器识别各手指敲击概率，结合 n-gram 语言先验，在普通表面进行无实体键盘的 QWERTY 输入。
- **证据**：练习 30 分钟后平均 19 WPM、0.6% CER；熟练用户超过 25 WPM。包含仅音频反馈的应用演示。
- **边界**：使用双手、表面敲击与英文解码；不等同于无接触单手输入。
- **项目启发**：动作信号的不确定性可以以概率形式传递给语言解码器，有助于避免一次错误手势彻底排除正确候选。
- **来源与阅读范围**：[作者预印本](https://arxiv.org/abs/2410.06001)。已核对摘要与正式会议标注。DOI：10.1145/3491102.3501878。

### [15] InkFlow: Connected Handwriting Recognition for Natural Mid-Air Input in Mixed Reality

- **出处**：Xufeng Jian 等，CHI 2026。包含中文与英文研究。
- **方法**：自动判断连续手部运动中哪些是书写笔画，减少每一笔都需显式捏合／松开的负担；使用边界感知 DS-TCN、元学习及少量个性化数据。
- **证据**：30 人交互比较、12 人闭环在线研究。中文书写速度为 15.21 CPM，对照 Pinch-Release 为 10.03 CPM；系统反馈延迟约 0.45 秒。
- **边界**：这是 MR 空中笔画分割与书写研究。中文速度表衡量手写产出，标准文本识别另行离线评价，不能直接当作通用中文 IME 的最终输入速度。也没有验证毫米级戒指触摸区域。
- **项目启发**：连笔输入、笔画起止和确认动作的成本值得单独研究；可用显式分段数据帮助训练连续输入，但转移到戒指仍需重新验证。
- **来源与阅读范围**：[ACM 原文](https://doi.org/10.1145/3772318.3790596)。已核对公开全文的机制、样本、表 4、延迟及标准文本识别说明。DOI：10.1145/3772318.3790596。

## 5. 近场语音、隐私与多模态消歧

### [16] ProxiMic: Convenient Voice Activation via Close-to-Mic Speech Detected by a Single Microphone

- **出处**：Yue Qin 等，CHI 2021。当前项目近场检测的直接背景。
- **方法**：利用用户靠近麦克风说话时的气流／喷麦特征，通过阈值与 CNN 两阶段检测，实现凑近即说的免唤醒入口。
- **证据**：作者报告 5 cm 内场景的激活召回率 94.1%、每用户每周 12.3 次误触发，以及较小模型资源需求。
- **边界**：这是激活检测研究，激活召回率不能解释为中文 ASR 正确率，也不能解释为真正无声语音识别。
- **项目启发**：分别评价意图激活、音频采集、中文转写、编辑四个环节；尤其检验低声、耳语、气流罩与距离变化的影响。
- **来源与阅读范围**：[作者项目页](https://pi.cs.tsinghua.edu.cn/publication/proximic-convenient-voice-activation-via-close-to-mic-speech-detected-by-a-single-microphone/)、[原文](https://pi.cs.tsinghua.edu.cn/lab/papers/ProxiMic.pdf)。已核对作者摘要。DOI：10.1145/3411764.3445687。

### [17] Watch Your Mouth: Silent Speech Recognition with Depth Sensing

- **出处**：Xue Wang、Zixiong Su、Jun Rekimoto、Yang Zhang，CHI 2024。英文识别。
- **方法**：使用口部深度点云而非 RGB 视频识别无声语音，研究腕部、头部与环境三种传感器位置。
- **证据**：句子识别 WER 在用户内为 8.06%，跨用户为 29.14%；命令集与句子分别评价。
- **边界**：跨用户效果明显下降，不能只引用个性化结果。它仍需要面向口部的深度感知硬件。
- **项目启发**：给低声语音之外的隐私输入提供另一条路径，也提醒我们跨用户、跨佩戴位置验证的重要性。
- **来源与阅读范围**：[作者项目与数据](https://hilab-open-source.github.io/WatchYourMouth/)、[作者原文](https://xueewang.github.io/files/WatchYourMouth.pdf)。已核对方法与用户内／跨用户结果。DOI：10.1145/3613904.3642092。

### [18] Multimodal Silent Speech-based Text Entry with Word-initials Conditioned LLM

- **出处**：Zixiong Su、Shitao Fang、Jun Rekimoto，CUI 2025；系统名 LipType。
- **方法**：同时无声说话与输入每个英文单词的首字母，利用唇读和 LLM 联合解码，并提供候选纠错。
- **证据**：16 人手机实验，包含单手／双手条件；平均输入速度相对普通键入提高 33.10%，计入纠错后节省 67.59% 按键。摘要中的离线 WER 比较为 20.3% 降至 9.19%。
- **边界**：验证英文，不是中文拼音。实验使用良好光照、手机摄像头和桌面 GPU 后端；不能直接声称手机完全本地运行。正文不同离线设置还有其他 WER，需按条件引用。
- **项目启发**：重点借鉴“低带宽辅助输入用于消除另一通道的歧义”。中文可研究用少量笔画辅助语音同音词／专名，但收益仍待验证。
- **来源与阅读范围**：[作者原文](https://stfang.me/assets/pdf/LipType_CUI_25.pdf)。已核对摘要、实验硬件、参与者、输入任务及结果。DOI：10.1145/3719160.3736612。

## 6. 极少动作与辅助沟通

### [19] Using large language models to accelerate communication for eye gaze typing users with ALS

- **出处**：Shanqing Cai 等，Nature Communications，2024；系统名 SpeakFaster。
- **方法**：用户输入高度压缩的英文首字母串，LLM 结合会话上下文展开；如候选不对，用户逐步补充字母或词来消歧。
- **证据**：离线模拟节省大量动作；19 名非 AAC 用户的移动端试验之外，2 名 ALS 眼控用户的实验室／现场测试报告输入速度相对基线提高约 29%—60%。
- **边界**：目标用户样本仅 2 人，不能推导所有受限输入用户都获相同收益；英文缩写展开不等同于中文输入。
- **项目启发**：将“继续补充一笔／一个音”与“查看和选择多个候选”的成本比较，设计可渐进消歧的短码输入。
- **来源与阅读范围**：[正式论文](https://www.nature.com/articles/s41467-024-53873-3)、[作者研究页面](https://research.google/pubs/using-large-language-models-to-accelerate-communication-for-eye-gaze-typing-users-with-als/)。已核对摘要及研究类型。DOI：10.1038/s41467-024-53873-3。

### [20] HeadText: Exploring Hands-free Text Entry using Head Gestures by Motion Sensing on a Smart Earpiece

- **出处**：Songlin Xu 等，2022 作者预印本；本轮未核实正式出版信息。
- **方法**：耳戴设备感知 7 类头部动作，覆盖按键、选词、确认和取消；采用 DTW 距离及 KNN 识别。
- **证据**：作者摘要报告 10 人手势实验的识别准确率 94.29%；输入实验平均 9.84 WPM。
- **边界**：动作集较少不等于没有疲劳；还应关心头部自然运动干扰和长期社会接受度。这些不是该摘要中的速度数字能够回答的问题。
- **项目启发**：当可用动作只有几个时，输入控制状态机与确认／撤销设计本身也是重要研究对象。
- **来源与阅读范围**：[作者预印本](https://arxiv.org/abs/2205.09978)。已核对作者摘要。

## 7. 综述与研究导航

### [21] Text Entry for XR Trove (TEXT): Collecting and Analyzing Techniques for Text Input in XR

- **出处**：Arpit Bhatia 等，CHI 2025。
- **内容**：整理 176 种 XR 文本输入技术，按交互属性、评价指标与性能梳理研究空间，并提供可浏览的在线数据库。
- **用途**：适合追踪相关工作、选择基线、检查已有交互方式；尤其适合建立本项目的文献矩阵。
- **边界**：不同论文的用户、训练时长、词表与评价条件不同；数据库中的数字不能自动组成公平性能排名。
- **来源与阅读范围**：[作者预印本](https://arxiv.org/abs/2503.11357)、[交互资料库](https://xrtexttrove.github.io/)。已核对摘要与项目。DOI：10.1145/3706598.3713382。

## 8. 早期基础工作：解释笔画输入与戒指路线的来历

### [22] Stroke++: A new Chinese input method for touch screen mobile phones

- **出处**：Jianwei Niu、Yang Liu、Jialiu Lin、Like Zhu、Kongqiao Wang，International Journal of Human-Computer Studies，2014，72(4):440–450。
- **方法**：提取 42 个汉字部件，将 6,763 个汉字分解为部件，输入其中一部分来定位目标字，结合候选生成减少操作。
- **证据**：论文报告与 Stroke、Pinyin 和手写方法的比较，并在不同城市开展用户研究。
- **边界**：不是近年工作；也不是“只用横竖撇点折五个动作”的同一方案。不能把其优势直接归因于五类笔画。
- **项目启发**：是形码、部分结构输入和中文候选消歧的重要前序工作。五类笔画方案应明确相对部件输入的学习成本与输入长度差异。
- **来源与阅读范围**：[期刊原文页面](https://www.sciencedirect.com/science/article/pii/S1071581914000020)。已核对摘要与公开预览，未重算用户研究结果。DOI：10.1016/j.ijhcs.2014.01.001。

### [23] Chinese character entry for mobile phones: a longitudinal investigation

- **出处**：Interacting with Computers，2005，17(2):121–146。
- **方法**：改善笔画到按键的映射，用抽象符号结合笔形例子解释归类，再以纵向实验研究输入表现。
- **证据**：研究指出笔画归类规则的歧义会影响学习与效率，改进映射说明可以提升使用表现。
- **边界**：设备与时代较早，不能直接采用其绝对输入速度作为现代戒指性能目标。
- **项目启发**：识别器之外，还应处理人的笔画分类不一致；例如点／捺、提／横、钩／折等映射是否容易理解。
- **来源与阅读范围**：[期刊原文页面](https://www.sciencedirect.com/science/article/abs/pii/S0953543804001456)。已核对摘要与公开预览。DOI：10.1016/j.intcom.2004.11.003。

### [24] QwertyRing: Text Entry on Physical Surfaces Using a Ring

- **出处**：Yizheng Gu 等，IMWUT 2020。作者网页的 2022／2024 时间不是论文年份。
- **方法**：食指 IMU 戒指检测敲击与方向，在想象的 QWERTY 表面输入；包含长按选候选、左右移动候选和删除。
- **证据**：5 天学习研究，开始约 13.74 WPM，最后约 20.59 WPM。
- **边界**：腕部需固定在表面，以使方向与按键对应；不能任意平移整只手。英文表现不能直接等于中文拼音速度。
- **项目启发**：比较学习曲线、候选交互与姿态约束；是理解 WritingRing、RingByte 等后续工作的合适起点。
- **来源与阅读范围**：[作者介绍](https://www.zhipeng-li.com/post/qwertyring-text-entry-on-physical-surfaces-using-a-ring)、[作者原文](https://xiaoying-wei.github.io/QwertyRing.pdf)。已核对作者摘要与交互约束。DOI：10.1145/3432204。

## 9. 相邻研究：有参考价值，但不能当作完整自由文本输入结果

### [25] TraceRing: Touchpad-like Pointing with a Single IMU Ring through Personalized Learning

- **出处**：Zhe He 等，CHI 2026。
- **内容**：单 IMU 戒指实现类似触摸板的二维指针控制；使用个性化表征、多任务／对比学习和专家选择，应对用户差异。
- **证据**：作者摘要报告速度预测误差相对基线下降 33.9%，实时指向任务平均完成时间 2.26 秒，AirMouse 为 3.01 秒。
- **边界**：核心评价为指向。论文演示了滑行键入应用，但这些指向指标不是文本输入 WPM／CER。
- **项目启发**：可借鉴跨用户与个性化策略，也可用于候选选择；不能把鼠标任务结果当作中文输入验证。
- **来源与阅读范围**：[作者实验室页面](https://pi.cs.tsinghua.edu.cn/publication/page/2/)、[作者原文](https://pi.cs.tsinghua.edu.cn/wp-content/uploads/2026/04/3772318.3790463.pdf)。已核对作者摘要及应用章节公开片段。DOI：10.1145/3772318.3790463。

### [26] Whispering Wearables: Multimodal Approach to Silent Speech Recognition with Head-Worn Devices

- **出处**：Tanmay Srivastava 等，ICMI 2024；系统名 QuietSync。
- **内容**：在耳机、眼镜等头戴设备上融合 IMU 与 ExG 电极信号，感知下颌运动与肌肉活动以识别无声命令。
- **证据**：9 人、12 个命令，准确率 94.2%；论文讨论了受控环境、较小命令集以及缺少 other 类等限制。
- **边界**：这是有限命令识别，尚不能宣称可输入任意中文句子；标题中的 Whispering 也不表示只靠麦克风识别耳语。
- **项目启发**：硬件与多模态融合可以参考，研究问题应区分“识别意图命令”与“开放文本录入”。
- **来源与阅读范围**：[Microsoft Research 作者页面](https://www.microsoft.com/en-us/research/publication/quietsync-integrating-multimodal-signals-for-accurate-silent-speech-interaction-with-head-worn-devices/?locale=ja)、[原文](https://www.microsoft.com/en-us/research/uploads/prod/2024/11/3678957.3685720.pdf)。已核对作者摘要与局限相关公开片段。DOI：10.1145/3678957.3685720。

## 10. 可直接继续使用的资料与开源资源

| 资料 | 可参考内容 | 使用边界 |
| --- | --- | --- |
| [Rime 五笔画方案](https://github.com/rime/rime-stroke) | `stroke.dict.yaml` 字典、`stroke.schema.yaml` 输入规则；适合作为五类笔画方案的传统基线 | 工程方案，不是受控实验的性能证据；字表与笔顺规范仍需核对 |
| [librime](https://github.com/rime/librime) | 输入法引擎、编码与候选处理架构 | 需要按具体方案检查功能、配置与许可 |
| [CNMBERT](https://github.com/IgarashiAkatuki/CNMBERT) | 拼音缩写转换实现与论文引用 | 离线模型任务，不等同于完整交互原型 |
| [LLM IME Benchmark](https://github.com/junli-cuny/llm-ime-benchmark) | 中文／日文输入转换与纠错数据、评测代码 | 应补真人实验、专名和中英混输任务 |
| [Watch Your Mouth](https://hilab-open-source.github.io/WatchYourMouth/) | 代码、句子数据与深度唇读演示 | 传感器与语言条件需匹配 |
| [TEXT 数据库](https://xrtexttrove.github.io/) | 浏览 176 种 XR 文本输入方式及相关论文 | 用来发现与分类，避免跨论文直接排速度名次 |
| [华为手表小艺输入法官方说明](https://consumer.huawei.com/cn/support/content/zh-cn16053926/) | 小屏设备组合键盘、手写、语音，以及输入 WLAN 密码、短回复等真实任务 | 产品能力说明，不是学术性能比较 |

## 11. 对当前项目的研究判断与可检验问题

以下是基于以上文献与当前项目形态提出的研究假设，需要通过实验验证。

### 11.1 优先考虑的问题

1. **五类笔画是否能降低极小输入面上的总成本？** 优势可能来自动作易分辨、容易解释与较少映射；成本可能来自笔顺回忆、长编码、候选浏览及笔画／控制手势混淆。应报告完整输入结果，而不止单笔识别率。
2. **何时继续输入，何时选候选？** 把候选首选率、Top-3／Top-5 命中率、翻页次数、选择耗时和撤销率联系起来，研究“少写一笔”是否真正节省时间。
3. **能否把语音与笔画用于互补消歧？** 例如低声语音输入大部分内容，少量笔画约束专名或同音词。ToneLipNet 和 LipType 支持这种研究思路，但没有直接证明本项目组合有效。
4. **能否用上下文排序，同时保留输入忠实性？** 对比字频、短语模型、领域／应用上下文、LLM 重排，并单独测试人名、地名、新词、否定句、数字、中英混输，防止仅在常见句子上有效。
5. **个性化是否有可重复的收益？** 跨天、跨用户、跨佩戴位置测试；少量校准数据应独立于测试数据，不能用训练集命中率说明输入法性能。

### 11.2 建议的实验结构

| 层面 | 记录内容 | 目的 |
| --- | --- | --- |
| 动作层 | 各笔画混淆、tap／短笔画、笔画／删除／确认混淆、误触发 | 确认错误来自传感还是编码 |
| 解码层 | 首选率、Top-k、全笔顺覆盖、短码长度、专名／词外词召回 | 评价语言与字典贡献 |
| 完整输入层 | 中文 CPM、未纠正 CER、纠错次数／时间、候选选择与翻页、延迟 | 衡量实际任务成本 |
| 学习与泛化 | 首次使用、练习后、隔日保持、跨用户／佩戴位置 | 区分熟练效果与泛化能力 |
| 体验层 | NASA-TLX、疲劳、学习感受、公共场景接受度 | 识别速度之外的代价 |

任务至少区分严格抄录、自由表达、识别后纠错，以及密码／混合字符输入；这些任务不宜共用一个“准确率”。

优先可落地的内部比较：相同戒指、相同识别器下，比较“纯字频候选”“上下文候选”“保留多个笔画假设的联合解码”。若再与完整手写／拼音方案比较，应同时说明硬件、动作空间和训练时间差异。

### 11.3 优先阅读顺序

- **戒指笔画输入主线**：[09] WritingRing → [11] RingByte → [10] RingGesture → [22] Stroke++ → [23] 笔画归类纵向研究 → [15] InkFlow。
- **近场语音与多模态主线**：[16] ProxiMic → [18] LipType → [08] 中文声调辅助无声输入 → [07] 中文拼音 ASR 纠错。
- **候选与少动作主线**：[03] 中文临床输入 → [01] 中文 GPT 拼音 → [19] SpeakFaster → [06] LLM IME Benchmark。
- **写 Related Work 前**：先浏览 [21] TEXT，再按语言、硬件、动作、反馈、训练与评价条件补充检索。

## 12. 后续检索词

`Chinese text entry`、`Chinese Pinyin input method`、`stroke-based Chinese input`、`abbreviated Pinyin`、`ring-based text entry`、`thumb-to-finger text input`、`eyes-free text entry`、`situationally impaired text entry`、`silent speech text entry`、`speech text correction`、`context-aware input method`、`candidate selection`、`joint decoding text entry`。

后续值得补充的独立方向：中文传统／简体与方言用户、无障碍扫描键盘与眼控中文输入、运动中使用、长文本编辑和真实长期部署。本轮对这些方向的覆盖相对有限。
