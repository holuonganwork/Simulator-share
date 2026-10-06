"""CLI: gen mock dataset nhiều ngày + chạy verify vòng 1–3, xuất manifest + reports.

  uv run python -m gsm_core.mockgen.generate --days 30 --seed-base 100 --out data/mock/v1

Mỗi ngày = 1 sim run (seed = seed_base + day_index). Output parquet per entity
(gitignored — tái gen được từ seed) + manifest.json + verify reports (commit git).
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date as _date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl

from gsm_core.mockgen.adapter_sim import entity_tables, generate_day
from gsm_core.schema_registry import SchemaRegistry

ROOT = Path(__file__).resolve().parents[3]
TZ = timezone(timedelta(hours=7))


def generate_dataset(days: int, seed_base: int, out_dir: Path,
                     config_path: Path | None = None, start_date: str = "2026-07-01") -> dict:
    cfg_path = config_path or (ROOT / "configs" / "pilot_hanoi.yaml")
    out_dir.mkdir(parents=True, exist_ok=True)
    all_records: dict[str, list[dict]] = defaultdict(list)
    seeds = []
    d0 = _date.fromisoformat(start_date)
    for i in range(days):
        seed = seed_base + i
        seeds.append(seed)
        day = entity_tables(generate_day(cfg_path, seed=seed, date=(d0 + timedelta(days=i)).isoformat()))
        for entity, records in day.items():
            if entity.startswith("_") or not isinstance(records, list):
                continue
            if entity in ("policy_bundle", "station_registry") and i > 0:
                continue  # L0 slowly-changing: chỉ giữ 1 bản
            if entity == "driver_profile" and i > 0:
                continue
            all_records[entity].extend(records)

    # ghi parquet (nested field -> json string cho polars đơn giản)
    counts = {}
    for entity, records in all_records.items():
        dict_records = [r for r in records if isinstance(r, dict)]
        if not dict_records:
            continue
        # infer_schema_length=None: quét toàn bộ — cột thưa (nullable) không bị suy Null
        # rồi crash khi gặp giá trị ở dòng >100 (BUG-PI2b-05)
        flat = [{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                 for k, v in r.items()} for r in dict_records]
        pl.DataFrame(flat, infer_schema_length=None).write_parquet(out_dir / f"{entity}.parquet")
        counts[entity] = len(dict_records)

    manifest = {
        "label": "MOCK", "generator": "gsm_core.mockgen v1",
        "generated_at": datetime.now(TZ).isoformat(),
        "days": days, "seed_base": seed_base, "seeds": seeds,
        "start_date": start_date, "config": str(cfg_path.name),
        # Cycle V: version đọc từ REGISTRY, không hardcode — từ khi validate route theo
        # record["schema_version"] (đa phiên bản), manifest nói dối version là bug thật chứ
        # không còn là chi tiết trang trí (review đối kháng confirmed).
        "schema_versions": {e: SchemaRegistry(ROOT / "schemas").schema_version(e)
                            for e in sorted(all_records)},
        "record_counts": counts,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"manifest": manifest, "records": all_records}


# ---------- Verify vòng 1: schema + FK ----------

def verify_round1(records: dict[str, list[dict]], report_path: Path) -> bool:
    reg = SchemaRegistry(ROOT / "schemas")
    lines = ["# ROUND 1 — Schema validation + FK integrity\n"]
    ok = True
    for entity in sorted(records):
        bad = reg.validate_many(entity, records[entity])
        lines.append(f"- `{entity}`: {len(records[entity])} records, {len(bad)} FAIL")
        if bad:
            ok = False
            lines.append(f"  - ví dụ: {list(bad.items())[:2]}")
    # FK checks
    drivers = {r["driver_id"] for r in records["driver_profile"]}
    stations = {r["station_id"] for r in records["station_registry"]}
    trips = {r["order_id"] for r in records["trip_record"]}
    orphans = defaultdict(int)
    for e in records["app_event"]:
        if e["driver_id"] not in drivers:
            orphans["app_event.driver_id"] += 1
    for t in records["swap_transaction"]:
        if t["station_id"] not in stations:
            orphans["swap.station_id"] += 1
        if t["driver_id"] not in drivers:
            orphans["swap.driver_id"] += 1
    for p in records["payout_ledger"]:
        if p["kind"] == "trip_payout" and p["basis"]["trip_id"] not in trips:
            orphans["ledger.trip_id"] += 1
    lines.append(f"\n**Orphan FK:** {dict(orphans) or 'KHÔNG'}")
    if orphans:
        ok = False
    lines.append(f"\n**KẾT LUẬN: {'PASS' if ok else 'FAIL'}**")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return ok


# ---------- Verify vòng 3: cross-entity consistency ----------

def verify_round3(records: dict[str, list[dict]], report_path: Path) -> bool:
    lines = ["# ROUND 3 — Cross-entity consistency\n"]
    ok = True
    pb = records["policy_bundle"][0]
    share = pb["driver_share"]
    trips = {t["order_id"]: t for t in records["trip_record"]}
    mismatch = 0
    n_payout = 0
    for e in records["payout_ledger"]:
        if e["kind"] != "trip_payout":
            continue
        n_payout += 1
        t = trips.get(e["basis"]["trip_id"])
        if t is None or e["amount_vnd"] != int(round(t["gross_vnd"] * share)):
            mismatch += 1
    lines.append(f"- Ledger tái tính từ policy: {n_payout} trip_payout, {mismatch} lệch")
    if mismatch:
        ok = False
    # event ordering per driver
    from collections import defaultdict as dd
    by_d = dd(list)
    for e in records["app_event"]:
        by_d[e["driver_id"]].append(e["t"])
    bad_order = sum(1 for ts in by_d.values() if ts != sorted(ts))
    lines.append(f"- Event ordering per driver: {bad_order} driver lỗi")
    if bad_order:
        ok = False
    # GPS endpoints vs trip (sample 200 trips)
    from gsm_sim.geo import haversine_km
    pings = dd(list)
    for p in records["gps_ping"]:
        pings[p["driver_id"]].append(p)
    for d in pings:
        pings[d].sort(key=lambda x: x["t"])
    checked, far = 0, 0
    for t in records["trip_record"][:200]:
        dp = [p for p in pings[t["driver_id"]] if t["t_pickup"] <= p["t"] <= t["t_complete"]]
        if len(dp) < 2:
            continue
        checked += 1
        d1 = haversine_km(dp[-1]["location"]["lat"], dp[-1]["location"]["lon"],
                          t["drop"]["lat"], t["drop"]["lon"])
        if d1 > 0.05:  # 50m
            far += 1
    lines.append(f"- GPS↔trip endpoint (≤50m): {checked} kiểm, {far} lệch")
    if far:
        ok = False
    lines.append(f"\n**KẾT LUẬN: {'PASS' if ok else 'FAIL'}**")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--seed-base", type=int, default=100)
    ap.add_argument("--out", type=str, default="data/mock/v1")
    ap.add_argument("--start-date", type=str, default="2026-07-01")
    args = ap.parse_args()

    out_dir = ROOT / args.out
    res = generate_dataset(args.days, args.seed_base, out_dir, start_date=args.start_date)
    reports = ROOT / "research" / "experiments" / "mockgen"
    reports.mkdir(parents=True, exist_ok=True)
    r1 = verify_round1(res["records"], reports / "ROUND-1-schema-report.md")
    r3 = verify_round3(res["records"], reports / "ROUND-3-consistency-report.md")
    print(json.dumps({"counts": res["manifest"]["record_counts"],
                       "round1": "PASS" if r1 else "FAIL",
                       "round3": "PASS" if r3 else "FAIL"}, indent=1))


if __name__ == "__main__":
    main()
