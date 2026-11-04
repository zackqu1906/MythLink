# Ringo API

| 接口 | 能力 |
| --- | --- |
| 本机 TCP | 设备状态、实时 IMU；不提供 PPG/音频/按钮订阅或灯控 |
| Python / BLE NUS | IMU、PPG、麦克风、按钮通知、LED 控制，见下文 |
| 录制文件 | IMU HDF5、音频 WAV、全量 JSONL |

BLE 直连示例与桌面应用二选一使用，同一设备不要同时连接。

## 实时 IMU

启动应用并在界面连接 Ringo。接口为本机 **TCP `127.0.0.1:17888`**，UTF-8 JSON，每条消息以换行 `\n` 结束；不是 HTTP。

连接后先收到 `hello`（`version: 1`）和 `status`。默认接收所有已连接戒指的 IMU。

| 请求示例 | 作用 / 应答 |
| --- | --- |
| `{"cmd":"status"}` | 返回 `status`，`rings` 含 `address`、`name`、`connected` |
| `{"cmd":"subscribe","rings":[],"streams":["imu"]}` | 接收所有连接设备；返回 `subscribed` |
| `{"cmd":"subscribe","rings":["设备地址"],"streams":["imu"]}` | 只接收指定设备 |
| `{"cmd":"subscribe","rings":[],"all":false}` | 停止接收 IMU，仍接收设备状态 |

IMU 消息示例：

```json
{"type":"imu","address":"设备地址","name":"Ringo1234","timestamp":12.5,"local_timestamp":12.5,"host_timestamp":0.0,"sequence":2500,"acc":[0.0,0.0,9.80665],"gyr":[0.0,0.0,0.0]}
```

| 字段 | 含义 |
| --- | --- |
| `timestamp` / `local_timestamp` | 相同的设备时间，单位秒 |
| `host_timestamp` | 校准后的主机时间；未校准为 `0` |
| `sequence` | 应用接收帧计数；断连或重启后应重新建立连续性判断 |
| `acc` | X/Y/Z，加速度 m/s² |
| `gyr` | X/Y/Z，角速度 rad/s；界面显示时转换为 °/s |

轴顺序保持设备线上坐标，默认六轴采样率 200 Hz。

## Python 示例

仅使用标准库；连接设备后运行：

```python
import json
import socket

with socket.create_connection(("127.0.0.1", 17888)) as sock:
    sock.sendall(b'{"cmd":"subscribe","rings":[],"streams":["imu"]}\n')
    with sock.makefile("r", encoding="utf-8") as stream:
        for line in stream:
            message = json.loads(line)
            if message["type"] == "imu":
                print(message["timestamp"], message["acc"], message["gyr"])
```

此接口只提供状态查询和 IMU 订阅；连接、采集控制及录制通过界面操作。PPG、音频和按钮数据可从全量录制 JSONL 读取，音频也可直接读取 WAV。
错误返回 `{"type":"error","message":"..."}`：`invalid_command`、`unsupported_command` 或 `unsupported_stream`。读取过慢会断开连接，客户端需重连并重新订阅。

## 读取 IMU 文件

在本项目的 Python 环境运行：

```python
from ring.runtime.recording_io import read_recording

for frame in read_recording("data/ringo-imu-示例.h5"):
    print(frame["timestamp"], frame["acc"], frame["gyr"])
```

单位同上；配套 `.meta.json` 的 `complete`、`samples` 和 `error` 表示录制完整性、帧数和错误。

## BLE 控制接口

本项目通过 `await ring.write(bytes)` 发命令，`ring.packet_callback(raw_bytes)` 收通知；
`ring.drivers.ringo_protocol.decode_packet(raw_bytes)` 将通知解码为字典。
直接使用 Bleak 时：写入 RX（`response=False`），订阅 TX。

| NUS UUID | 值 |
| --- | --- |
| Service | `6e400001-b5a3-f393-e0a9-e50e24dcca9e` |
| RX：主机 → 戒指 | `6e400002-b5a3-f393-e0a9-e50e24dcca9e` |
| TX：戒指 → 主机 | `6e400003-b5a3-f393-e0a9-e50e24dcca9e` |

下面字节均为十六进制；多字节字段均为小端。采集前发送 `2c 00 00`，恢复正常通知模式。

| 功能 | 命令 | 实验室 action |
| --- | --- | --- |
| PPG 绿光 | `24 00 00 01` | `ppg_green` |
| PPG 红光 + 红外 | `24 00 01 01` | `ppg_red` |
| PPG 红外 / 佩戴检测 | `24 00 02 01` | `ppg_ir` |
| 停止 PPG | `24 01` | `ppg_stop` |
| 开启麦克风，使用固件默认编码 | `20 00` | `mic` |
| 关闭麦克风 | `20 01` | `mic_stop` |
| 开灯 / 关灯 | `28 00 01` / `28 00 00` | `led_on` / `led_off` |
| 按钮 | 无需启动命令，订阅 TX 后接收事件 | — |

`action` 是进程内 `await lab.control(address, ring, action)` 的参数，不是 TCP 请求。
`audio_start` / `audio_stop` 在控制麦克风的同时开始 / 结束 WAV 录音；
`imu_record_start` / `imu_record_stop` 控制 HDF5 录制。
这些录制 action 需要桌面运行时已接好数据回调；单独发送 BLE 命令不会创建文件。
PPG、麦克风、LED 命令没有固件 ACK，写入成功不代表已采集或已亮灯；以通知或实际灯态确认。

## PPG 数据

每个解码事件都有 `kind`、`cmd`、`subcmd`；以下字段在此基础上增加。
`seq` 是 uint16 包序号（65535 后回到 0），`uptime_ms` 是 uint32 设备运行时间，单位毫秒。

| 通知头 / kind | 解码字段 |
| --- | --- |
| `24 08` / `ppg_raw` | `seq, mode, uptime_ms, channels` |
| `24 04` / `ppg_raw` | `seq, wear, flags, sar_lp, sar_bl, sar_diff, uptime_ms, channels`（仅 `ir`） |
| `24 02` / `ppg` | `seq, hr, spo2, wear, uptime_ms`；扩展包另有 `rri_ms, stress, rri_count` |

`channels` 示例：`{"green":[123,125],"red":[90,92],"ir":[180,182]}`。
仅包含固件实际发送的通道，值为 int32 原始光学计数，无归一化、无物理单位。
`hr` 为 bpm，`spo2` 为百分比，`rri_ms` 为毫秒；`wear`、`flags`、`stress` 透传固件数值。
数据是否有效应由接收方结合佩戴状态及固件判断；原始波形和生理指标是不同通知。

二进制布局（不含两字节通知头，`u8/u16/u32` 为无符号整数，`i16/i32` 为有符号整数）：

- `24 08`：`seq:u16, mode:u8, reserved:u8, count:u8, mask:u8, uptime_ms:u32, samples:i32[]`。
  `mask` 位 0/1/2 对应绿/红/红外；每组按启用通道的绿→红→红外顺序交错排列，共 `count` 组。
- `24 04`：`seq:u16, wear:u8, flags:u8, count:u8, reserved:u8, sar_lp:i16, sar_bl:i16, sar_diff:i32, uptime_ms:u32, ir:i32[count]`。
- `24 02`：`seq:u16, hr:u16, spo2:u8, wear:u16, uptime_ms:u32`；扩展尾部为 `rri_ms:u16, stress:u8, rri_count:u8`。

## 音频数据

当前 dev 固件使用 **ADPCM**；其他固件可能使用 Opus。按通知头识别编码，不要强制发送 Opus 启动参数。

| 通知头 | 编码 | `decode_packet` 输出 |
| --- | --- | --- |
| `20 03` | IMA ADPCM | `kind:"mic", encoding:"adpcm", seq, frag_idx, frag_count, uptime_ms, payload` |
| `20 04` | Opus | 同上，`encoding:"opus"` |

两种通知布局相同：通知头后为 `seq:u16, frag_idx:u16, frag_count:u16, uptime_ms:u32, payload:bytes`。
`payload` 在解码字典中是十六进制字符串，**不是 PCM**。
按设备、`seq` 和 `uptime_ms` 归组，收齐 `frag_count` 片后按从 0 开始的 `frag_idx` 拼接。
缺片不能直接解码；实验室会丢弃超时分片并记录缺口。

- ADPCM 完整块：`predictor:i16, index:u8, reserved:u8, sample_count:u16, codes:bytes`。
  predictor 是首个 PCM 样本，后续 IMA ADPCM 码低半字节在前；每块独立解码。
  可调用 `ring.apps.ringo_lab.adpcm.AdpcmDecoder().decode(block)`，返回 int16 范围的整数列表，无额外音频库依赖。
- Opus 完整块：`sample_count:u16=1600, frame_count:u8=5`，随后 5 组 `length:u16, opus_frame:bytes[length]`。
  可复用 `ring.apps.ringo_lab.audio.AudioDecoder`；依赖系统 libopus，需按序使用同一个解码器。
- PCM / WAV：**16000 Hz、单声道、有符号 16-bit、小端**。JSON 中 PCM 为整数数组，范围 `-32768…32767`。
  界面波形是幅度预览；读取音频请用 PCM 或 WAV。

## 按钮与灯

按钮通知头 `27 02`，随后 `seq:u16, event:u8, uptime_ms:u32`。
解码结果为 `kind:"button", seq, event, label, uptime_ms`（另含公共 `cmd/subcmd`）。

| event | label |
| --- | --- |
| 0 | 按下 |
| 1 | 松开 |
| 2 | 单击 |
| 3 | 长按 |
| 4 | 双击 |

LED 使用上面的 `28 00 01` / `28 00 00`，当前实验室只开放开关。
没有 LED 数据流或状态回执；全量 JSONL 中的 `type:"tx", hex:"280001"` 仅表示已发送开灯命令。

## Python：接收 PPG / 音频 / 按钮，控制灯

在解压项目环境运行，先关闭桌面应用。将名称改为目标戒指的实际名称：

```python
import asyncio
from bleak import BleakClient, BleakScanner
from ring.drivers.nus_protocol import RX, TX
from ring.drivers.ringo_protocol import decode_packet

async def main():
    device = await BleakScanner.find_device_by_name("Ringo1234", timeout=10)
    if device is None:
        raise RuntimeError("未找到目标 Ringo")

    def receive(_, raw):
        try:
            event = decode_packet(raw)
            if event["kind"] in ("ppg_raw", "ppg", "mic", "button"):
                print(event)  # mic 是压缩分片，按上文重组后解码
        except ValueError as exc:
            print("无效通知:", exc)

    async with BleakClient(device) as client:
        async def send(hex_bytes):
            await client.write_gatt_char(RX, bytes.fromhex(hex_bytes), response=False)

        await client.start_notify(TX, receive)
        try:
            for command in ("2c 00 00", "24 00 00 01", "20 00", "28 00 01"):
                await send(command)
            await asyncio.sleep(10)  # 此时可按戒指按钮
        finally:
            for command in ("24 01", "20 01", "28 00 00"):
                await send(command)
            await client.stop_notify(TX)

asyncio.run(main())
```

## 全量 JSONL 文件

在实验室开启“全量录制”后，每行一个 JSON 对象；此格式不走 TCP。
首行为 `type:"session", version:1, devices, audio, imu, clock`。
事件行包含 `address`、`received_ns`（主机单调时钟纳秒）、`unix_ns`（Unix 时间纳秒）和：

| type | 内容 |
| --- | --- |
| `rx` / `tx` | `hex`：完整 BLE 通知 / 命令的十六进制字符串 |
| `decoded` | 上文 `decode_packet` 字段，如 `kind:"ppg_raw"`、`kind:"button"`、`kind:"mic"` |
| `adpcm` / `opus` | `seq, uptime_ms, hex`：重组后的完整压缩音频块 |
| `pcm` | `seq, uptime_ms, sample_rate:16000, samples:[整数…]` |
| `connection` | `connected`：连接状态 |
| `sequence_gap` | `stream, previous, current`：序号缺口 |
| `audio_gap` / `decode_error` | `error`；`audio_gap` 另有 `seq` |

正常收尾有 `type:"end", records, complete, error`。`complete` 表示写文件是否成功，
不保证蓝牙无丢包；还需检查 gap 事件。没有 `end` 表示文件未正常收尾。

```python
import json

with open("data/ringo-示例.jsonl", encoding="utf-8") as file:
    for line in file:
        event = json.loads(line)
        if event.get("type") == "decoded" and event.get("kind") == "ppg_raw":
            print(event["channels"])
        elif event.get("type") == "decoded" and event.get("kind") == "button":
            print(event["event"], event["label"])
        elif event.get("type") == "pcm":
            print(event["sample_rate"], len(event["samples"]))
```
