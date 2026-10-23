# Workflow 与原任务对比：唯一的两组方案

在 Downloads 项目终端运行：

```sh
cd /Users/admin/Downloads/ProximicVoice-main
```

| 目的 | 入口 | 结果 |
|---|---|---|
| 生成 Workflow | `bash scripts/run-workflows.sh --latest --run` | `agent-experience/workflows/library.html`，正文为自然语言 |
| 只刷新 / 查看 Workflow | `bash scripts/run-workflows.sh` | 每份 `<job>.flow.txt` 可复制；旧草稿需手动重新提取才升级 |
| 对比执行 | `bash scripts/run-workflow-trials.sh run --task notes --mode baseline` 或 `--mode steps` | 每轮保存实际输入、时长、工具调用次数和验证状态 |
| 查看对比 | `bash scripts/run-workflow-trials.sh report` | `agent-experience/workflow-tests/comparison.html` |

应用内入口：Agent 窗口「记录」→ 对应任务「提取 / 重新提取」→「查看 Workflow 草稿」；Agent 窗口「对比测试」可选流程、填写参数与起点、分别运行两组。需要重启源码运行的应用才能加载新代码。已经安装的 `.app` 需要重新构建才会更新。

这些脚本默认读写**当前项目**的 `agent-experience/`。两者都可用 `--root '/其他目录/agent-experience'` 指定同一个记录目录；安装版应用的数据可能位于 `~/Library/Application Support/ProxiMic Voice/agent-experience/`。不要混用两组的目录。

## 1. 生成与回查

```sh
bash scripts/run-workflows.sh --list
bash scripts/run-workflows.sh --latest --run
open agent-experience/workflows/library.html
```

也可从 `--list` 复制 `--session ... --episode ...` 指定任务，替代 `--latest`。不加 `--run` 只入队，由已运行的应用后台处理；加 `--run` 使用已保存的文本模型配置处理队列，开启思考。应用已有提取进程时不会同时启动第二个消费者，已入队任务仍保留。

步骤内容由模型提炼：动作、具体执行方法、完成条件；程序只负责排版，不额外调用模型做文字转换。JSON 只用于结构校验、来源回查、参数和内部存档，不发送给执行 Agent。模型必须引用真实记录；缺失观察保持缺失。

## 2. 把生成的 Workflow 准备为可编辑测试任务

```sh
bash scripts/run-workflow-trials.sh workflows
# 将下面引号内的标识换成上一条命令显示的 job:index
bash scripts/run-workflow-trials.sh prepare --workflow '流程标识' --task my-task
```

新建 `tasks/my-task/`，不会覆盖已有任务。编辑其中：

- `baseline.txt`：用户原任务描述，只说明目标。
- `steps.txt`：从所选 Workflow 导入的自然语言步骤，可直接修改。
- `task.json`：名称、参数的本次值、共同起点和复位方法，以及生成来源。参数和起点初始留空，必须填写后才能运行。

`{{note_title}}` 这类占位符在发送前替换一次；参数值里的花括号不会再次解释。配置中的 `initial_state` 只给操作者复位，不送给 Agent。

预置 `notes` 和 `music` 保留上次测试使用的自然语言步骤、任务与参数。新增的提取不会偷偷改写这些输入；要测试新草稿，请 `prepare` 为新目录或明确编辑 `steps.txt`。

## 3. 预览，然后分别运行

```sh
bash scripts/run-workflow-trials.sh list
bash scripts/run-workflow-trials.sh preview --task notes --mode baseline
bash scripts/run-workflow-trials.sh preview --task notes --mode steps

bash scripts/run-workflow-trials.sh run --task notes --mode baseline
# 手动恢复 task.json 中同一起点，再运行另一组
bash scripts/run-workflow-trials.sh run --task notes --mode steps
```

把 `notes` 换为 `music` 或自己的目录名即可测试其他任务。每条 run 只执行一组，真实操作电脑；按终端提示恢复起点，结束后核验目标是否完成。Ctrl+C 取消。没有暂停 / resume 功能。

两组通过同一 Flow → 新临时 thread → turn/start 入口：

- `baseline`：只发送替换参数后的 `baseline.txt`（去掉首尾空白）。
- `steps`：只发送替换参数后的 `steps.txt`，不再附加 baseline、guidance、JSON 或第二份参数说明。

相同系统规则、工具与权限机制仍然生效。新会话不继承上一轮对话；应用界面、缓存、播放状态等仍需人工复位。预置 notes/music 只预授予各自应用的普通操作；额外授权仍按 Agent 正常流程处理。不要同时运行其他操控桌面的任务。

## 4. 结果与历史记录

```sh
bash scripts/run-workflow-trials.sh report
open agent-experience/workflow-tests/comparison.html
```

报告展示总时长、工具调用次数、目标核验、实际发送文本、原始运行记录。只有同一不可变任务版本、同一模型与设置、确认相同起点、正常结束并核验成功、无中途干预的样本才比较。计时含新会话准备与人工等待，不含手动复位；少量样本不能证明稳定提速。

每次修改输入会产生新版本；不会覆盖旧运行。JSON 组已从 CLI、界面和底层执行入口移除；`--suite` / `--mode json` 已停用。历史 JSON / guided / direct 测试快照与报告保留，只读回查，不计入当前两组结论。

- `cases/`：过去实验的原始冻结输入，保留。
- `legacy-inputs/`：旧 `workflow.json`、`guided-workflow.txt`，不再被新测试读取。
- `agent-experience/workflow-tests/*.case.json`：内部固定任务与来源。
- `*.workflow.txt`：固定后的步骤文本。
- `*.prompt.txt`：该轮实际发给 Agent 的文本。
- `*.run.json`：内部时长、调用次数、会话标识和验证信息。
- 旧 HTML / JSON 报告原样保留，新报告固定使用 `comparison.html`。
