"""阅读器性能基线：翻页页文本、文献详情、PDF 首屏。

用法（对着已部署实例）：

    set FS_PERF_BASE=http://127.0.0.1:18080
    set FS_PERF_EMAIL=admin@example.com
    set FS_PERF_PASSWORD=...
    python scripts/perf_reader.py --rounds 12

脚本只读：登录、列实验室、挑一篇有 PDF 的文献、反复测量。不写任何业务数据。
输出为可复算的 JSON（写到 stdout，可选 --out 落盘），字段固定，便于前后对比。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - 运行环境缺依赖时给出人读提示
    print("需要 httpx：pip install httpx", file=sys.stderr)
    raise SystemExit(2) from None


def pct(values: list[float], q: float) -> float:
    """线性插值分位数。values 非空。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return round(ordered[low] * (1 - frac) + ordered[high] * frac, 1)


def summarize(name: str, values: list[float], extra: dict | None = None) -> dict:
    if not values:
        return {"name": name, "samples": 0, "note": "没有样本", **(extra or {})}
    return {
        "name": name,
        "samples": len(values),
        "min_ms": round(min(values), 1),
        "p50_ms": pct(values, 0.50),
        "p95_ms": pct(values, 0.95),
        "max_ms": round(max(values), 1),
        **(extra or {}),
    }


class Probe:
    def __init__(self, base: str, email: str, password: str, timeout: float) -> None:
        self.api = base.rstrip("/") + "/api/v1"
        self.client = httpx.Client(timeout=timeout, follow_redirects=True)
        self.email = email
        self.password = password
        self.token = ""

    def close(self) -> None:
        self.client.close()

    def login(self) -> None:
        r = self.client.post(f"{self.api}/auth/login", json={"email": self.email, "password": self.password})
        r.raise_for_status()
        self.token = r.json().get("access") or ""
        if not self.token:
            raise RuntimeError("登录成功但没有拿到 access token")

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.client.get(f"{self.api}{path}", headers=self.headers, **kw)

    def as_items(self, payload: Any) -> list[dict]:
        """列表接口在真空库时会包一层 {items: []}（G1/G16 口径）。"""
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            got = payload.get("items")
            if isinstance(got, list):
                return got
        return []

    def pick_item(self, lab_id: str | None) -> tuple[str, str, int]:
        """返回 (lab_id, item_id, page_count)。优先挑有 PDF 的文献。"""
        labs = self.as_items(self.get("/labs").json())
        if lab_id:
            labs = [x for x in labs if x.get("id") == lab_id]
        if not labs:
            raise RuntimeError("这个账号看不到任何实验室")
        for lab in labs:
            lid = lab.get("id") or ""
            payload = self.get(f"/labs/{lid}/literature", params={"page": 1, "page_size": 50}).json()
            rows = self.as_items(payload if isinstance(payload, list) else payload.get("items", payload))
            with_pdf = [r for r in rows if r.get("has_pdf")]
            for row in with_pdf or rows:
                iid = row.get("id") or ""
                if not iid:
                    continue
                detail = self.get(f"/literature/{iid}/detail").json()
                pages = 0
                for f in detail.get("files") or []:
                    pages = max(pages, int(f.get("page_count") or 0))
                return lid, iid, pages or 1
        raise RuntimeError("这些实验室里没有可用文献；请先入库一篇带 PDF 的文献")

    def time_call(self, fn) -> float:
        started = time.perf_counter()
        fn()
        return (time.perf_counter() - started) * 1000.0


def run(args: argparse.Namespace) -> dict:
    probe = Probe(args.base, args.email, args.password, args.timeout)
    try:
        probe.login()
        lab_id, item_id, page_count = probe.pick_item(args.lab)

        page_text_ms: list[float] = []
        detail_full_ms: list[float] = []
        detail_slim_ms: list[float] = []
        pdf_first_byte_ms: list[float] = []
        pdf_total_ms: list[float] = []
        pdf_bytes = 0
        etag = ""
        revalidate_ms: list[float] = []
        revalidate_304 = 0

        span = max(1, min(page_count, args.rounds))
        for i in range(args.rounds):
            page = (i % span) + 1

            # 翻页：前端实际调用的 slim 路径。
            page_text_ms.append(
                probe.time_call(
                    lambda p=page: probe.get(
                        f"/literature/{item_id}/detail", params={"page": p, "include": "page_text"}
                    ).raise_for_status()
                )
            )
            # 打开文献：完整详情。
            detail_full_ms.append(
                probe.time_call(
                    lambda p=page: probe.get(f"/literature/{item_id}/detail", params={"page": p}).raise_for_status()
                )
            )
            # 瘦身后的默认详情（A3 之后才与上面不同）。
            detail_slim_ms.append(
                probe.time_call(
                    lambda p=page: probe.get(
                        f"/literature/{item_id}/detail", params={"page": p, "include": "core"}
                    ).raise_for_status()
                )
            )

        for _ in range(max(1, args.pdf_rounds)):
            started = time.perf_counter()
            first: float | None = None
            total = 0
            with probe.client.stream("GET", f"{probe.api}/literature/{item_id}/pdf", headers=probe.headers) as r:
                r.raise_for_status()
                etag = r.headers.get("etag") or etag
                for chunk in r.iter_bytes():
                    if first is None:
                        first = (time.perf_counter() - started) * 1000.0
                    total += len(chunk)
            pdf_first_byte_ms.append(first or 0.0)
            pdf_total_ms.append((time.perf_counter() - started) * 1000.0)
            pdf_bytes = total

        if etag:
            for _ in range(max(1, args.pdf_rounds)):
                started = time.perf_counter()
                r = probe.get(f"/literature/{item_id}/pdf", headers={**probe.headers, "If-None-Match": etag})
                revalidate_ms.append((time.perf_counter() - started) * 1000.0)
                if r.status_code == 304:
                    revalidate_304 += 1

        return {
            "base": args.base,
            "lab_id": lab_id,
            "item_id": item_id,
            "page_count": page_count,
            "rounds": args.rounds,
            "metrics": [
                summarize("翻页 page_text", page_text_ms),
                summarize("文献详情 完整", detail_full_ms),
                summarize("文献详情 核心", detail_slim_ms),
                summarize("PDF 首字节", pdf_first_byte_ms, {"bytes": pdf_bytes}),
                summarize("PDF 整份下载", pdf_total_ms, {"bytes": pdf_bytes}),
                summarize(
                    "PDF 重校验",
                    revalidate_ms,
                    {"etag": etag or "无", "hit_304": revalidate_304},
                ),
            ],
        }
    finally:
        probe.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="蜂算阅读器性能基线")
    ap.add_argument("--base", default=os.environ.get("FS_PERF_BASE") or "http://127.0.0.1:8000")
    ap.add_argument("--email", default=os.environ.get("FS_PERF_EMAIL") or "")
    ap.add_argument("--password", default=os.environ.get("FS_PERF_PASSWORD") or "")
    ap.add_argument("--lab", default=os.environ.get("FS_PERF_LAB") or "")
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--pdf-rounds", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if not args.email or not args.password:
        print("需要 --email / --password（或 FS_PERF_EMAIL / FS_PERF_PASSWORD）", file=sys.stderr)
        return 2

    try:
        report = run(args)
    except Exception as exc:  # 基线脚本失败要人读，不要堆栈
        print(f"基线测量失败：{exc}", file=sys.stderr)
        return 1

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
