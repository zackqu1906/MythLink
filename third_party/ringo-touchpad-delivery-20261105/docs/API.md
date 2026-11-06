# SDK 与触控板接口

## Python SDK

所有异步对象在同一个 asyncio 事件循环中使用。回调是同步回调，必须快速返回；耗时任务放入自己的有界队列。不要在回调中阻塞、同步等待网络或运行模型。

| 接口 | 含义 |
|---|---|
| `await scan(timeout=5.0)` | 返回设备列表：identifier、name、rssi |
| `Ring(device, on_touchpad=..., on_touchpad_status=..., on_connection=..., on_gesture=...)` | 创建对象并注册回调 |
| `await ring.connect()` | 连接并返回 DeviceInfo，含firmware_version、hardware_revision、components |
| `await ring.battery_level()` | 电量百分比 |
| `await ring.touchpad.start()` | 加载匹配后端并完成设备START握手 |
| `await ring.touchpad.stop()` | 停止触控板 |
| `await ring.disconnect()` | 释放本应用连接，不解绑或恢复出厂 |

`async with Ring(...) as ring:` 自动连接，退出时断开。可运行 `python tools/touchpad_events.py <扫描到的identifier>` 查看事件。CLI也可使用 `ring-mac touchpad <identifier> --seconds 120`。两者都不注入系统鼠标。

### 触控板事件

| 类型 | 字段 | 含义 |
|---|---|---|
| `Motion` | frame_id, dx, dy, mode, episode_id | 相对位移；mode为单指/双指/未确定，不是原始IMU或原始模型速度 |
| `ContactUpdate` | episode_id, phase, mode, range | 接触阶段和对应帧/时间范围，END/CANCEL应清理应用接触状态 |
| `Click` | click_id, button, count, is_preview | LEFT/RIGHT；1/2次；预告不是最终点击 |
| `TouchpadStatus` | stage, message, session_id, generation, backend | STARTING、STARTING_STREAM、RUNNING、STOPPING、STOPPED、FAILED等生命周期状态 |

前三类事件均有 `meta.session_id / generation / sequence / source_time_ns`。session/generation变化后清空上一段交互状态；时间值用于流内对齐和延迟处理，不当作UTC时间。sequence为SDK事件序号，不是BLE token序号。

仅在RUNNING及当前session/generation匹配时使用事件。Click去重使用click_id；不要把预告和正式Click各点一次。当前系统适配只派发正式点击，不支持鼠标按住/拖拽接口。

异常类型 `RingError` 的 `reason / device_code / phase / response` 可用于诊断。`-16`具有多种原因；不要仅凭错误码推断充电状态。

### 可选系统输出层

`CursorConfig(gain=1.0, scroll_gain=1.0, finger_mapping=True, clicks=False)` 控制映射；X/Y相同倍率，已做过的位移映射不重复。NativeOutput接口为 `move(dx,dy)`、`scroll(dy)`、`click(button,count)`。单指/UNKNOWN移动；开启双指映射时TWO_FINGER将dy转换为纵向像素滚动，不输出横向滚动。

实际应用使用 `SystemCursor` 管理权限、队列和生命周期：`await check_permission()`、`await enable((session_id,generation), config, still_current=...)`、`submit(event)`、`disable(reason)`、`await close()`。enable要提供会话仍有效的检查，stop/故障/代次变化时disable。推荐参考包内已接好的网页Controller，不直接在BLE回调调用原生鼠标函数。

系统输出队列最多128条。Motion来源或应用排队超过200ms丢弃；正式Click入应用队列超过250ms丢弃。队列溢出和原生异常停用输出。已经交给操作系统的单次调用不能撤销。

## 本地页面接口

`http://127.0.0.1:8765/` 使用同源 `ws://127.0.0.1:8765/ws`；仅允许一个控制页面，不是远程服务或完整事件广播API。每约100ms发送页面状态快照；系统光标直接消费每条SDK事件，不受快照刷新频率限制。

命令为JSON对象：

```json
{"action":"scan"}
{"action":"connect","identifier":"实际扫描到的UUID"}
{"action":"start"}
{"action":"cursor_permission"}
{"action":"cursor_enable","config":{"gain":1.0,"scroll_gain":1.0,"finger_mapping":true,"clicks":false}}
{"action":"cursor_stop"}
{"action":"stop"}
{"action":"disconnect"}
```

每次发送一个对象；另有battery、clear。页面WebSocket有Host/Origin检查，不作为任意跨站客户端接口。自己的Python程序优先使用上面的公开SDK接口。

## BLE触控板协议摘要

NUS服务 `6e400001-b5a3-f393-e0a9-e50e24dcca9e`，写入特征为对应0002，通知为0003。MTU至少67，推荐247或更大。Ringo可能广播SMP服务而非NUS，发现设备不能只靠NUS广播过滤。

连接/订阅后等待Ringo加密握手，初始STOP得到回复后再START。串行执行控制命令，因为协议没有请求事务号。SDK已封装此流程。

| 类型 | 格式 |
|---|---|
| START | `3c 00 02 01 00 00`：v2、允许批包、持续运行 |
| STOP | `3c 01` |
| 状态查询 | `3c 03` |
| 诊断查询 | `3c 04` |
| STATUS | `3c 11 02`，64字节；state位于3、int32 result位于4、session/epoch位于8/12、model_id位于44 |
| 单token | `3c 02 02`，28字节；byte3 bit0=reset，session/epoch/seq位于4/8/12，第二帧uptime_ms位于16，8字节特征位于20 |
| 批包 | `3c 13 02 N`后接N个完整28字节token，N=2..15 |

多字节字段小端。固件在200Hz采样下每两帧计算一个100Hz token；批包按现成队列与MTU发送，不等待凑满。特征8维int8，逐通道fraction bits为 `[5,6,5,5,5,6,6,5]`。model_id为`0x77762a23`，SDK校验匹配后端。

Ringo使用自身既有的 `(Y,-X,Z)` 输入轴；收到的是特征，接收端不再转轴、拼IMU帧或执行戒指前端。model_id标识参数，不编码安装坐标；不能据此认为不同设备输入轴相同。

epoch/reset变化要求后端清状态、重新预热；序号缺口不能静默跳过继续使用旧状态。STOP后丢弃在途尾包。故障后STOP可返回state=2/result=0，表示停止完成但保留历史故障。完整状态解析已经封装在SDK中。
