"""Release composition. Driver modules are imported only when connecting."""
from dataclasses import dataclass
import json
from pathlib import Path

DRIVERS = {
    "r1": ("ring.drivers.r1", ("drivers/r1.py", "drivers/nus.py", "drivers/nus_protocol.py")),
    "bcl": ("ring.drivers.bcl", ("drivers/bcl.py", "adapters/ble_protocol.py")),
    "ringo": ("ring.drivers.ringo", ("drivers/ringo.py", "drivers/ringo_protocol.py", "drivers/nus.py", "drivers/nus_protocol.py")),
}
APPS = {
    "firmware": ("固件烧录", "↑", "FirmwarePage.qml", ("apps/firmware", "ui/qml/FirmwarePage.qml")),
    "density": ("Density 识别", "∿", "DensityPage.qml", ("apps/density", "ui/qml/DensityPage.qml")),
    "ringo_lab": ("Ringo 实验室", "◈", "RingoLabPage.qml", ("apps/ringo_lab", "ui/qml/RingoLabPage.qml", "ui/qml/LabPlot.qml")),
    "handwriting": ("手写识别", "✎", "HandwritingPage.qml", ("apps/handwriting", "ui/qml/HandwritingPage.qml")),
    "click": ("点击控制", "⌁", "ClickPage.qml", ("apps/click", "ui/qml/ClickPage.qml")),
    "gestures": ("手势识别", "✦", "GesturesPage.qml", ("apps/gestures.py", "ui/qml/GesturesPage.qml")),
    "recording": ("录制", "▤", "RecordingPage.qml", ("runtime/recorder.py", "runtime/recording_io.py", "ui/qml/RecordingPage.qml")),
}


@dataclass(frozen=True)
class Product:
    drivers: tuple[str, ...] = ("bcl", "ringo", "r1")
    apps: tuple[str, ...] = ("recording", "gestures", "density", "click", "handwriting", "ringo_lab", "firmware")

    max_devices: int | None = None

    def __post_init__(self):
        if self.max_devices is not None and (type(self.max_devices) is not int or self.max_devices < 1):
            raise ValueError("max_devices 必须为正整数")
        if "ringo_lab" in self.apps and "ringo" not in self.drivers:
            raise ValueError("Ringo 实验室需要 ringo 驱动")
        for values, registry in ((self.drivers, DRIVERS), (self.apps, APPS)):
            if len(set(values)) != len(values) or set(values) - registry.keys():
                raise ValueError(f"无效的发布模块：{values}")

    @property
    def pages(self):
        return ([dict(name="设备", symbol="◉", source="")]
                + [dict(name=APPS[a][0], symbol=APPS[a][1], source=APPS[a][2]) for a in self.apps]
                + [dict(name="设置", symbol="⚙", source="SettingsPage.qml")])

    def match(self, device, advertisement):
        names = (device.name or "", advertisement.local_name or "")
        import re
        if "r1" in self.drivers and any(re.fullmatch(r"R1[0-9A-Fa-f]{4}", n) or n.endswith("的Ringo") for n in names):
            return "r1"
        if "bcl" in self.drivers and any(n.startswith("BCL") for n in names):
            return "bcl"
        if "ringo" in self.drivers and any("ringo" in n.lower() for n in names):
            return "ringo"
        return None


def load_product(path=None):
    path = Path(path) if path else Path(__file__).with_name("product.json")
    if not path.exists():
        if path.name != "product.json" or path.parent != Path(__file__).parent:
            raise FileNotFoundError(path)
        return Product()
    data = json.loads(path.read_text())
    if not {"drivers", "apps"} <= set(data) or set(data) - {"drivers", "apps", "max_devices"}:
        raise ValueError("发布配置必须包含 drivers 和 apps")
    return Product(tuple(data["drivers"]), tuple(data["apps"]), data.get("max_devices"))
