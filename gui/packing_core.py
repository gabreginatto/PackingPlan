"""Shared pipe geometry + packing math for the GUI.

Lifted from create_pipe_packing_plan.py so the Flask backend doesn't depend
on Excel I/O for catalog-based manual entry.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable

CONTAINER_LENGTH_M = 12.032
CONTAINER_WIDTH_M = 2.352
CONTAINER_HEIGHT_M = 2.698
CONTAINER_PAYLOAD_KG = 26000.0
HANDLING_CLEARANCE_MM = 5.0
EXCLUDED_DN = {20}

# --- pipe dimension catalog -------------------------------------------------

PPR_S5 = {
    20: {"od_mm": 20.0, "wall_mm": 1.9},
    25: {"od_mm": 25.0, "wall_mm": 2.3},
    32: {"od_mm": 32.0, "wall_mm": 2.9},
    40: {"od_mm": 40.0, "wall_mm": 3.7},
    50: {"od_mm": 50.0, "wall_mm": 4.6},
    63: {"od_mm": 63.0, "wall_mm": 5.8},
}

PPR_S32 = {
    20: {"od_mm": 20.0, "wall_mm": 3.4},
    25: {"od_mm": 25.0, "wall_mm": 4.2},
    32: {"od_mm": 32.0, "wall_mm": 5.4},
    40: {"od_mm": 40.0, "wall_mm": 6.7},
}

PVC_D2665 = {
    '2"': {"dn": 50, "od_mm": 60.33, "wall_mm": 3.91},
    '3"': {"dn": 75, "od_mm": 88.90, "wall_mm": 5.49},
    '4"': {"dn": 100, "od_mm": 114.30, "wall_mm": 6.02},
    '6"': {"dn": 150, "od_mm": 168.28, "wall_mm": 7.11},
}

# HDPE PE100 PN10/SDR17 — wall = od / 17
HDPE_SIZES = [63, 75, 90, 110, 125, 140, 160, 180, 200, 225, 250, 280, 315, 355, 400]

STEEL_NPS_OD = {
    15: 21.3, 20: 26.7, 25: 33.4, 32: 42.2, 40: 48.3, 50: 60.3,
    65: 73.0, 80: 88.9, 100: 114.3, 150: 168.3,
}

# Approximate weight per metre (kg/m) for catalog entries — used when the
# user enters a quantity without weighing data. Numbers are calculated from
# pipe geometry assuming PVC=1.42, PP-R=0.91, HDPE=0.96, steel=7.85 g/cc.
DENSITY = {
    "PP-R": 0.91,
    "PVC-U DWV": 1.42,
    "HDPE PE100 PN10": 0.96,
    "Steel-Plastic Composite": 2.5,  # composite, conservative
}


def kg_per_metre(family: str, od_mm: float, wall_mm: float | None) -> float:
    if wall_mm is None:
        # crude estimate for steel-composite using OD only
        return DENSITY[family] * math.pi * (od_mm / 1000.0) * 0.005 * 1000
    rho = DENSITY.get(family, 1.0)
    # cross-section area in m^2
    od_m = od_mm / 1000.0
    id_m = max(0.0, (od_mm - 2 * wall_mm) / 1000.0)
    area = math.pi / 4 * (od_m ** 2 - id_m ** 2)
    # density above is g/cc = 1000 kg/m³
    return area * rho * 1000.0


# --- catalog for the dropdowns ----------------------------------------------

def build_catalog() -> list[dict]:
    cat = []
    for size, dim in sorted(PPR_S5.items()):
        if size in EXCLUDED_DN:
            continue
        cat.append({
            "family": "PP-R (S5 cold)",
            "size": f"DN{size}",
            "dn": size,
            "od_mm": dim["od_mm"],
            "wall_mm": dim["wall_mm"],
            "id_mm": dim["od_mm"] - 2 * dim["wall_mm"],
            "can_telescope": True,
        })
    for size, dim in sorted(PPR_S32.items()):
        if size in EXCLUDED_DN:
            continue
        cat.append({
            "family": "PP-R (S3.2 hot)",
            "size": f"DN{size}",
            "dn": size,
            "od_mm": dim["od_mm"],
            "wall_mm": dim["wall_mm"],
            "id_mm": dim["od_mm"] - 2 * dim["wall_mm"],
            "can_telescope": True,
        })
    for size, dim in PVC_D2665.items():
        cat.append({
            "family": "PVC-U DWV",
            "size": size,
            "dn": dim["dn"],
            "od_mm": dim["od_mm"],
            "wall_mm": dim["wall_mm"],
            "id_mm": dim["od_mm"] - 2 * dim["wall_mm"],
            "can_telescope": True,
        })
    for dn in HDPE_SIZES:
        if dn in EXCLUDED_DN:
            continue
        od = float(dn)
        wall = round(od / 17.0, 2)
        cat.append({
            "family": "HDPE PE100 PN10",
            "size": f"DN{dn}",
            "dn": dn,
            "od_mm": od,
            "wall_mm": wall,
            "id_mm": od - 2 * wall,
            "can_telescope": True,
        })
    for dn, od in STEEL_NPS_OD.items():
        if dn in EXCLUDED_DN:
            continue
        cat.append({
            "family": "Steel-Plastic Composite",
            "size": f"DN{dn}",
            "dn": dn,
            "od_mm": od,
            "wall_mm": None,
            "id_mm": None,
            "can_telescope": False,
        })
    return cat


def lookup(family: str, size: str) -> dict | None:
    for row in build_catalog():
        if row["family"] == family and row["size"] == size:
            return row
    return None


# --- geometry ---------------------------------------------------------------

@dataclass
class Pipe:
    family: str
    size: str
    length_m: float
    qty: int
    od_mm: float
    wall_mm: float | None
    id_mm: float | None
    can_telescope: bool
    kg_per_pipe: float

    @property
    def od_m(self) -> float:
        return self.od_mm / 1000.0


def hex_stack_count(diameter_m: float) -> dict:
    """Return how many pipes of a given OD fit in the container cross-section
    using hexagonal close packing, plus the row geometry used by the SVG view.
    """

    def count_for(width_m: float, height_m: float):
        if diameter_m <= 0 or width_m < diameter_m or height_m < diameter_m:
            return 0, 0, 0, 0
        row_spacing = diameter_m * math.sqrt(3) / 2
        rows = int(math.floor((height_m - diameter_m) / row_spacing)) + 1
        full = int(math.floor(width_m / diameter_m))
        offset = max(0, int(math.floor((width_m - 0.5 * diameter_m) / diameter_m)))
        count_a = sum(full if r % 2 == 0 else offset for r in range(rows))
        count_b = sum(offset if r % 2 == 0 else full for r in range(rows))
        if count_b > count_a:
            return count_b, rows, offset, full
        return count_a, rows, full, offset

    normal = count_for(CONTAINER_WIDTH_M, CONTAINER_HEIGHT_M)
    rotated = count_for(CONTAINER_HEIGHT_M, CONTAINER_WIDTH_M)
    if rotated[0] > normal[0]:
        return {
            "cross_section_pipes": rotated[0],
            "rows": rotated[1],
            "first_row": rotated[2],
            "offset_row": rotated[3],
            "orientation": "rows-across-width",  # width is the longer dim
            "frame_w_m": CONTAINER_HEIGHT_M,
            "frame_h_m": CONTAINER_WIDTH_M,
        }
    return {
        "cross_section_pipes": normal[0],
        "rows": normal[1],
        "first_row": normal[2],
        "offset_row": normal[3],
        "orientation": "rows-across-height",
        "frame_w_m": CONTAINER_WIDTH_M,
        "frame_h_m": CONTAINER_HEIGHT_M,
    }


def hex_positions(diameter_m: float) -> tuple[list[tuple[float, float]], dict]:
    """Return (x, y) centres for each pipe in the hex stack along with stack info.
    Origin is the lower-left corner of the container cross-section frame.
    """
    stack = hex_stack_count(diameter_m)
    frame_w = stack["frame_w_m"]
    frame_h = stack["frame_h_m"]
    if stack["cross_section_pipes"] == 0:
        return [], stack
    r = diameter_m / 2.0
    row_spacing = diameter_m * math.sqrt(3) / 2.0
    positions: list[tuple[float, float]] = []
    full_row = stack["first_row"]
    offset_row = stack["offset_row"]
    for row in range(stack["rows"]):
        y = r + row * row_spacing
        if row % 2 == 0:
            count = full_row
            x0 = r
        else:
            count = offset_row
            x0 = r + r  # 0.5 diameter shift
        for i in range(count):
            positions.append((x0 + i * diameter_m, y))
    return positions, stack


# --- telescoping ------------------------------------------------------------

def min_circle_factor(n: int) -> float:
    factors = {1: 1.0, 2: 2.0, 3: 1.0 + 2.0 / math.sqrt(3),
               4: 1.0 + math.sqrt(2), 5: 2.701, 6: 3.0, 7: 3.0}
    return factors.get(n, math.sqrt(n / 0.82))


def telescope_capacity(host: Pipe, inner: Pipe) -> int:
    if host.id_mm is None or not host.can_telescope or not inner.can_telescope:
        return 0
    if host.family != inner.family:
        return 0
    if host.length_m != inner.length_m:
        return 0
    if host.od_mm <= inner.od_mm:
        return 0
    available = host.id_mm - HANDLING_CLEARANCE_MM
    if available <= 0:
        return 0
    cap = 0
    for n in range(1, 8):
        if min_circle_factor(n) * inner.od_mm <= available:
            cap = n
    return cap


def telescope_chain(pipes: list[Pipe]) -> list[tuple[int, int, int]]:
    """Greedy: for each (host, inner) pair within the same family/length, return
    (host_idx, inner_idx, cap) sorted by host OD descending then inner OD desc.
    """
    indexed = list(enumerate(pipes))
    indexed.sort(key=lambda x: (-x[1].od_mm))
    out = []
    for i, h in indexed:
        for j, k in indexed:
            if i == j:
                continue
            cap = telescope_capacity(h, k)
            if cap > 0:
                out.append((i, j, cap))
    return out


# --- packing ---------------------------------------------------------------

def items_to_pipes(items: list[dict]) -> list[Pipe]:
    pipes: list[Pipe] = []
    for it in items:
        cat = lookup(it["family"], it["size"])
        if cat is None:
            continue
        try:
            raw_length = it.get("length_m", 5.8)
            length_m = 5.8 if raw_length in (None, "") else float(raw_length)
            qty = int(it["qty"])
        except (TypeError, ValueError):
            continue
        if length_m <= 0 or qty <= 0 or int(cat["dn"]) in EXCLUDED_DN:
            continue
        # PP-R family normalisation for density lookup
        family_norm = "PP-R" if "PP-R" in it["family"] else it["family"]
        kg_per_m = kg_per_metre(family_norm, cat["od_mm"], cat["wall_mm"])
        pipes.append(Pipe(
            family=it["family"],
            size=it["size"],
            length_m=length_m,
            qty=qty,
            od_mm=cat["od_mm"],
            wall_mm=cat["wall_mm"],
            id_mm=cat["id_mm"],
            can_telescope=cat["can_telescope"],
            kg_per_pipe=kg_per_m * length_m,
        ))
    return pipes


def build_plan(items: list[dict]) -> dict:
    """Group pipes by (family-base, length), telescope where possible, then
    compute container count per group. Return JSON-friendly structure.
    """
    pipes = items_to_pipes(items)

    # Telescope within (family, length)
    # Strategy: sort group by od desc, greedy: each host pipe absorbs as many
    # smaller pipes as physically and quantity-wise possible.
    groups: dict[tuple, list[Pipe]] = {}
    for p in pipes:
        # PP-R hot and cold treated as separate families for nesting since
        # walls differ. Use the full family name as the key.
        groups.setdefault((p.family, p.length_m), []).append(p)

    containers = []
    total_pipes_loose = 0
    total_pipes_nested = 0

    for (family, length_m), members in groups.items():
        members = sorted(members, key=lambda p: -p.od_mm)
        # Track remaining qty per pipe size
        remaining = {(p.size): p.qty for p in members}
        # Build nesting assignments: each unit of host can hold cap of inner.
        # Greedy: for each host (largest first), pair with the largest inner
        # that fits and has remaining qty.
        host_units: list[dict] = []  # one entry per host-pipe unit produced
        for h in members:
            while remaining[h.size] > 0:
                # New host unit
                unit = {
                    "host_size": h.size,
                    "host_od_mm": h.od_mm,
                    "host_id_mm": h.id_mm,
                    "host_kg": h.kg_per_pipe,
                    "inners": [],  # nested unit dicts: size, od_mm, kg, inners
                }
                remaining[h.size] -= 1
                # Try to fill with smaller pipes; recursively nest
                available = (h.id_mm - HANDLING_CLEARANCE_MM) if h.id_mm else 0
                if h.can_telescope and available > 0:
                    self_fill_inner(unit, h, members, remaining, available)
                host_units.append(unit)

        # Now pack host units into containers, weight-limited.
        # Geometric limit: container_length / pipe_length * hex_count
        stack = hex_stack_count(h.od_m if False else members[0].od_m)  # placeholder
        # For mixed sizes per container, we keep it simple: same family/length
        # means same hex stack only if all units share the same OD; but units
        # may differ by host size. We compute capacity per host size and pack
        # by host-size groups, but for visual clarity here we segment per
        # host size.

        by_host_size: dict[str, list[dict]] = {}
        for u in host_units:
            by_host_size.setdefault(u["host_size"], []).append(u)

        for size, units in by_host_size.items():
            sample = next(m for m in members if m.size == size)
            length_positions = max(1, int(math.floor(CONTAINER_LENGTH_M / sample.length_m)))
            stack = hex_stack_count(sample.od_m)
            geom_per_container = stack["cross_section_pipes"] * length_positions
            if geom_per_container <= 0:
                continue
            # group units into containers
            i = 0
            while i < len(units):
                chunk = []
                weight = 0.0
                while i < len(units) and len(chunk) < geom_per_container:
                    u = units[i]
                    unit_w = unit_weight(u)
                    if weight + unit_w > CONTAINER_PAYLOAD_KG and chunk:
                        break
                    chunk.append(u)
                    weight += unit_w
                    i += 1
                if not chunk:
                    break
                containers.append(make_container(
                    family=family, length_m=sample.length_m,
                    host_size=size, host_od_mm=sample.od_mm,
                    stack=stack, length_positions=length_positions,
                    units=chunk, weight=weight,
                ))
                total_pipes_loose += sum(unit_pipe_count(u) for u in chunk)
                total_pipes_nested += sum(unit_nested_count(u) for u in chunk)

    # Summary
    total_volume_pre = sum(p.qty * math.pi / 4 * p.od_m ** 2 * p.length_m for p in pipes)
    total_weight = sum(p.qty * p.kg_per_pipe for p in pipes)
    summary = {
        "total_containers": len(containers),
        "total_weight_kg": total_weight,
        "total_volume_m3": total_volume_pre,
        "nested_pipes": total_pipes_nested,
    }
    return {"containers": containers, "summary": summary}


def unit_weight(unit: dict) -> float:
    return unit["host_kg"] + sum(unit_weight(inner) for inner in unit["inners"])


def unit_pipe_count(unit: dict) -> int:
    return 1 + sum(unit_pipe_count(inner) for inner in unit["inners"])


def unit_nested_count(unit: dict) -> int:
    return sum(unit_pipe_count(inner) for inner in unit["inners"])


def self_fill_inner(unit: dict, host_pipe: Pipe, members: list[Pipe],
                    remaining: dict, available_id_mm: float) -> None:
    """Fill `unit` (a host pipe) with as many smaller pipes as it can hold,
    decrementing the `remaining` map. Uses min-circle-of-n packing.
    """
    # Try inners largest-first.
    for inner in sorted(members, key=lambda p: -p.od_mm):
        if inner.od_mm >= host_pipe.od_mm:
            continue
        if remaining.get(inner.size, 0) <= 0:
            continue
        # how many of this inner size physically fit
        cap = 0
        for n in range(1, 8):
            if min_circle_factor(n) * inner.od_mm <= available_id_mm:
                cap = n
        if cap <= 0:
            continue
        take = min(cap, remaining[inner.size])
        for _ in range(take):
            inner_unit = {
                "host_size": inner.size,
                "host_od_mm": inner.od_mm,
                "host_id_mm": inner.id_mm,
                "host_kg": inner.kg_per_pipe,
                "size": inner.size,
                "od_mm": inner.od_mm,
                "id_mm": inner.id_mm,
                "kg": inner.kg_per_pipe,
                "inners": [],
            }
            remaining[inner.size] -= 1
            nested_available = (inner.id_mm - HANDLING_CLEARANCE_MM) if inner.id_mm else 0
            if inner.can_telescope and nested_available > 0:
                self_fill_inner(inner_unit, inner, members, remaining, nested_available)
            unit["inners"].append(inner_unit)
        break


def make_container(family: str, length_m: float, host_size: str,
                   host_od_mm: float, stack: dict, length_positions: int,
                   units: list[dict], weight: float) -> dict:
    positions, _ = hex_positions(host_od_mm / 1000.0)
    # truncate positions to the number of units
    used_positions = positions[:len(units)]
    return {
        "type": "40ft",
        "family": family,
        "length_m": length_m,
        "host_size": host_size,
        "host_od_mm": host_od_mm,
        "units_in_container": len(units),
        "length_positions": length_positions,
        "weight_kg": weight,
        "weight_pct": round(weight / CONTAINER_PAYLOAD_KG * 100, 1),
        "loading_pattern": (
            f"{length_positions} length positions × {stack['rows']} staggered rows "
            f"({stack['first_row']}/{stack['offset_row']} pipes per row)"
        ),
        "cross_section": {
            "frame_w_m": stack["frame_w_m"],
            "frame_h_m": stack["frame_h_m"],
            "circles": [
                {
                    "cx_m": cx, "cy_m": cy,
                    "od_mm": host_od_mm,
                    "host_size": host_size,
                    "inners": flatten_inners(units[idx]["inners"]),
                }
                for idx, (cx, cy) in enumerate(used_positions)
            ],
        },
    }


def flatten_inners(inners: list[dict]) -> list[dict]:
    out = []
    for inner in inners:
        out.append({
            "size": inner["host_size"],
            "od_mm": inner["host_od_mm"],
            "id_mm": inner["host_id_mm"],
            "kg": inner["host_kg"],
            "inners": flatten_inners(inner["inners"]),
        })
    return out


# --- Excel ingestion (delegates to the existing parser) --------------------

def parse_excel(path: str) -> list[dict]:
    """Best-effort: read an Excel file in the format of the BYD PI workbooks
    and return a list of {family, size, length_m, qty} that the GUI can show.
    """
    import pandas as pd
    raw = pd.read_excel(path, sheet_name=0, header=None)
    # find header row
    header_row = 12
    for idx in range(min(25, len(raw))):
        row_values = [str(v).strip() if pd_notna(v) else "" for v in raw.iloc[idx]]
        if any("Description" == v or "Product Description" in v for v in row_values):
            header_row = idx
            break
    df = pd.read_excel(path, sheet_name=0, header=header_row)
    cols = list(df.columns)
    desc_col = next((c for c in cols if "description" in str(c).lower()), cols[1])
    qty_col = next((c for c in cols if "qty" in str(c).lower()), None)
    if qty_col is None:
        return []
    items = []
    for _, row in df.iterrows():
        desc = "" if not pd_notna(row.get(desc_col)) else str(row.get(desc_col)).strip()
        if not desc or "pipe" not in desc.lower() or "fitting" in desc.lower():
            continue
        try:
            qty = int(float(row.get(qty_col)))
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        info = classify_description(desc)
        if info is None:
            continue
        items.append({**info, "qty": qty})
    return items


def pd_notna(v) -> bool:
    import pandas as pd
    return pd.notna(v)


def classify_description(desc: str) -> dict | None:
    desc_lower = desc.lower()
    length_match = re.search(r"(\d+(?:\.\d+)?)\s*m(?:\b|-|$)", desc_lower)
    length_m = float(length_match.group(1)) if length_match else 5.8
    dn_match = re.search(r"\bdn\s*(\d+)", desc_lower)
    inch_match = re.search(r'(\d+)"', desc)

    if "pe100" in desc_lower:
        dn = int(dn_match.group(1)) if dn_match else 0
        if dn in EXCLUDED_DN:
            return None
        if dn in HDPE_SIZES:
            return {"family": "HDPE PE100 PN10", "size": f"DN{dn}", "length_m": length_m}
    if "pvc-u dwv" in desc_lower and inch_match:
        size = f'{inch_match.group(1)}"'
        if size in PVC_D2665:
            return {"family": "PVC-U DWV", "size": size, "length_m": length_m}
    if "pp-r" in desc_lower:
        dn = int(dn_match.group(1)) if dn_match else 0
        if dn in EXCLUDED_DN:
            return None
        hot = "s3.2" in desc_lower or "hot" in desc_lower
        family = "PP-R (S3.2 hot)" if hot else "PP-R (S5 cold)"
        if hot and dn in PPR_S32:
            return {"family": family, "size": f"DN{dn}", "length_m": length_m}
        if not hot and dn in PPR_S5:
            return {"family": family, "size": f"DN{dn}", "length_m": length_m}
    if "steel-plastic composite" in desc_lower:
        dn = int(dn_match.group(1)) if dn_match else 0
        if dn in EXCLUDED_DN:
            return None
        if dn in STEEL_NPS_OD:
            return {"family": "Steel-Plastic Composite", "size": f"DN{dn}", "length_m": length_m}
    return None
