# 安装与使用

在交付 ZIP 解压目录按照 README 创建虚拟环境并安装。只使用 API/CLI 可去掉 `[web,cursor]`；只需页面观察可使用 `[web]`。依赖由 pip 安装，本包不是全依赖离线安装器。

`model-check` 只在本机认证并加载后端模型、运行一次 CPU 推理，不访问蓝牙。正常结果包含 `CPUExecutionProvider` 和 `recurrent_state_floats: 1216`。

## 升级

先停止触摸板和系统控制，断开设备，再退出旧 Python 服务。用新 wheel 执行 pip install --upgrade，然后重新启动服务。仅刷新浏览器不能更新后端。页面监听 127.0.0.1，不要同时启动多个服务争用同一戒指；需要修改端口时使用 `ui --port 8766 --open`。

## 操作

1. 打开试用台，点击扫描，选择目标戒指，再连接。
2. 确认固件版本和电量，启动触摸板，等待 RUNNING。
3. 观察轨迹、接触模式与点击。页面倍率只影响画布，不改变模型输出。
4. 使用结束先停止并断开，终端 Ctrl+C 退出。关闭拥有控制权的页面也会清理设备会话。

需要允许启动终端/Python/宿主访问蓝牙；系统鼠标输出另外需要辅助功能权限。配对错误、认证不足应按设备的配对流程处理，不通过本 SDK 清除绑定。

## CLI

```sh
.venv/bin/ring-mac scan --seconds 5
.venv/bin/ring-mac info '<扫描返回的完整 CoreBluetooth UUID>'
.venv/bin/ring-mac gestures '<UUID>' --seconds 30
.venv/bin/ring-mac touchpad '<UUID>' --seconds 30
```

Mac 设备标识是 CoreBluetooth UUID，不是蓝牙 MAC。一次只用一个宿主连接目标设备。`touchpad --seconds` 是启动成功后的观察时长；0.4.0 在缺少首 token 时继续等待并告警，Ctrl+C 可取消。

## 固件兼容边界

协议与配套模型必须匹配。Ringo 旧手势 START/STOP 没有业务 ACK，写完成不证明固件已切换；低功耗、左右手和方向设置是否可用应按 INFO 能力判断。

部分旧 Ringo 固件有“普通手势先启后触摸板无首包”的顺序问题。临时办法是先关闭手势，留出切换时间，启动触摸板并等到 RUNNING，再开启手势与麦克风。历史验证使用了操作间 0.7 秒等待，不保证零间隔可用；本次 SDK 更新没有修改设备固件。麦克风不属于本 SDK 公共接口。

电量不足、充电或模式冲突可能导致 START 拒绝。不要只凭设备错误码 -16 确定原因；查看设备状态及诊断。
