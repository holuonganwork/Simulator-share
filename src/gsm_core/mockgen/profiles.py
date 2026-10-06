"""Profile universe — 100% Taxi Cơ Hữu (core_owned, ô tô điện VinFast, HĐLĐ với GSM).

Mọi tài xế (driver_id `d-*` khớp sim actors) đều được sinh bởi sim. Acceptance theo
archetype target + noise (sửa caveat R2 acceptance≈1.00).
"""

from __future__ import annotations

import random

# archetype → (target_acceptance, trips/ngày range, online giờ range). Grounded personas + realism-benchmarks.
ARCHETYPES = {
    "newbie":    {"acc": 0.74, "trips": (8, 15), "online": (6.0, 8.0), "fulfil": 0.90},
    "part_time": {"acc": 0.82, "trips": (8, 16), "online": (3.0, 5.0), "fulfil": 0.94},
    "full_time": {"acc": 0.91, "trips": (15, 25), "online": (8.0, 10.5), "fulfil": 0.95},
    "veteran":   {"acc": 0.96, "trips": (15, 22), "online": (8.0, 9.5), "fulfil": 0.98},
    "top":       {"acc": 0.96, "trips": (22, 30), "online": (10.0, 11.5), "fulfil": 0.98},
}

# loại GSM → (service_type, driver_type, track, driver_share, fare/cuốc VND range, vehicle_model, vehicle_class, seat_capacity)
KINDS = {
    "taxi_co_huu_compact": ("car", "car", "core_owned", 0.35, (45000, 75000), "VF5", "A_COMPACT", 4),
    "taxi_co_huu_sedan":   ("car", "car", "core_owned", 0.35, (50000, 80000), "VF6", "B_SEDAN", 5),
    "taxi_co_huu_suv":     ("car", "car", "core_owned", 0.35, (55000, 85000), "VF_e34", "C_SUV", 5),
    "taxi_co_huu_luxury":  ("car", "car", "core_owned", 0.40, (95000, 160000), "VF8", "D_LUXURY", 5),
}

# 3 depot THAT cua Taxi Co Huu (FRAUDS.md muc "He thong Depot quan ly phuong
# tien"): (hub_id, depot_id, depot_code, depot_name). Truoc day toan bo 1000
# tai xe bi gan cung DUY NHAT "hub-dongda"/"depot-01"/"Dong Da" - tan du tu
# thoi simulator con gioi han trong 1 quan Dong Da, chua don sau khi mo rong
# ban do ra toan Ha Noi (Res 7). Gio moi tai xe duoc gan 1 trong 3 depot that,
# on dinh theo driver_id (khong doi giua cac ngay/bang trong cung 1 lan sinh).
DEPOTS = [
    ("hub-gialam", "depot-01", "DPT01", "Gia Lam"),
    ("hub-namtuliem", "depot-02", "DPT02", "Nam Tu Liem"),
    ("hub-yennghia", "depot-03", "DPT03", "Yen Nghia"),
]


def _pick_archetype(rng: random.Random) -> str:
    # phân phối thực: đa số full_time/part_time, ít top/newbie
    return rng.choices(list(ARCHETYPES),
                       weights=[0.15, 0.30, 0.30, 0.15, 0.10])[0]


def _profile(driver_id: str, kind: str, archetype: str, simulated: bool,
             rng: random.Random) -> dict:
    service, dtype, track, share, fare_rng, vehicle, v_class, seats = KINDS[kind]
    a = ARCHETYPES[archetype]
    is_prem = kind == "taxi_co_huu_luxury"
    hub_id, depot_id, depot_code, depot_name = rng.choice(DEPOTS)
    return {
        "driver_id": driver_id, "kind": kind, "service_type": service,
        "driver_type": dtype, "track": track, "tier": "premium" if is_prem else "standard",
        "archetype": archetype, "tenure_months": rng.randint(1, 30),
        "driver_share": share, "vehicle_model": vehicle,
        "vehicle_class": v_class, "seat_capacity": seats,
        "fare_lo": fare_rng[0], "fare_hi": fare_rng[1],
        "target_acceptance": a["acc"], "target_fulfil": a["fulfil"],
        "trips_lo": a["trips"][0], "trips_hi": a["trips"][1],
        "online_lo": a["online"][0], "online_hi": a["online"][1],
        "simulated": simulated,
        "hub_id": hub_id, "depot_id": depot_id, "depot_code": depot_code, "depot_name": depot_name,
    }


_KIND_BY_VEHICLE_CLASS = {
    "A_COMPACT": "taxi_co_huu_compact",
    "B_SEDAN": "taxi_co_huu_sedan",
    "C_SUV": "taxi_co_huu_suv",
    "D_LUXURY": "taxi_co_huu_luxury",
}


def build_profile_universe(sim_profiles: list[dict], seed: int) -> dict[str, dict]:
    """Roster Taxi Cơ Hữu (core_owned, xe VinFast). Dòng xe của mỗi tài xế LẤY TỪ L0
    `driver_profile` do sim sinh (vehicle_class) — để bảng KPI L1R và L0 nói cùng một xe."""
    rng = random.Random(seed ^ 0x9E3779B1)
    uni: dict[str, dict] = {}
    for prof in sim_profiles:
        kind = _KIND_BY_VEHICLE_CLASS.get(prof.get("vehicle_class") or "A_COMPACT", "taxi_co_huu_compact")
        uni[prof["driver_id"]] = _profile(prof["driver_id"], kind, _pick_archetype(rng), True, rng)
    return uni


def kind_distribution(universe: dict[str, dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in universe.values():
        out[p["kind"]] = out.get(p["kind"], 0) + 1
    return out
