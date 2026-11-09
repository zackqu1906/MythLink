"""One connection presentation shared by the home card, header and tray."""


def connection_info(state, *, can_reconnect, preparing=False, error=""):
    label, action_text, action = {
        "disconnected": ("未连接", "重新连接" if can_reconnect else "连接设备", "connect"),
        "scanning": ("正在搜索设备…", "正在搜索…", ""),
        "connecting": ("正在连接…", "取消连接", "cancel"),
        "cancelling": ("正在取消连接…", "正在取消…", ""),
        "cancelled": ("已取消连接", "重新连接", "connect"),
        "connected": ("已连接", "断开设备", "disconnect"),
        "disconnecting": ("正在断开…", "正在断开…", ""),
        "failed": ("连接失败", "重试连接", "connect"),
    }[state]
    detail = "已连接 · 识别组件准备中" if state == "connected" and preparing else label
    if state == "failed":
        detail = error or "未能连接设备，请重试。"
    short_label = {"scanning": "搜索中", "connecting": "连接中", "cancelling": "取消中",
                   "cancelled": "已取消", "disconnecting": "断开中"}.get(state, label)
    return dict(state=state, label=label, shortLabel=short_label, detail=detail, actionText=action_text,
                action=action, enabled=bool(action),
                pending=state in {"scanning", "connecting", "cancelling", "disconnecting"},
                destructive=state in {"connected", "disconnecting"},
                primary=state in {"disconnected", "cancelled", "failed", "connected"})


def connection_error_message(error):
    summary = str(error).splitlines()[0].strip()
    if "timeout" in summary.lower() or "timed out" in summary.lower():
        return "连接超时，设备未响应。请确认戒指已开机并靠近电脑。"
    return summary
