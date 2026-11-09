"""Explicit frozen-app diagnostic; sends only the synthetic examples below."""
import argparse
import json
from pathlib import Path
import time

from ..text_processing.clef_connection import ClefServerSettings, ClefConnection, ConnectedClefRouter


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="公网服务设置 JSON：host 和 port（默认 20031）")
    args = parser.parse_args(argv)
    connection = ClefConnection(ClefServerSettings(**json.loads(Path(args.config).read_text())))
    router = ConnectedClefRouter(connection)
    try:
        router.warmup()
        for speech, source, expected in [("明天见。", "", "dictation"),
                                          ("把三点改为四点。", "我们三点开会。", "edit")]:
            start = time.perf_counter()
            decision, trace = router.classify(speech, source, {})
            if decision.task != expected:
                raise RuntimeError("测试分类与预期不一致")
            print(json.dumps(dict(expected=expected, actual=decision.task,
                tasks=decision.edit_plan.tasks if decision.edit_plan else [],
                elapsed_s=round(time.perf_counter() - start, 3),
                trace=json.loads(trace)), ensure_ascii=False))
        print("CLEF_APP_CONNECTION_OK")
        return 0
    except Exception as exc:
        print("CLEF_APP_CONNECTION_FAILED:", str(exc))
        return 1
    finally:
        router.close()


if __name__ == "__main__":
    raise SystemExit(main())
