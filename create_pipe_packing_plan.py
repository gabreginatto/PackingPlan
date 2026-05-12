"""
Create pipe-only 40ft packing plans from the BYD order workbooks.

The workbook produced by this script is separate from combine_and_pack.py:
it focuses on same-size pipe loading and pipe telescoping feasibility.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


THREAD_ID = "019e1919-b83f-7c61-97a4-734fa488d24c"
OUTPUT_DIR = Path("outputs") / THREAD_ID
OUTPUT_FILE = OUTPUT_DIR / "pipe_packing_plan_40ft.xlsx"

CONTAINER_LENGTH_M = 12.032
CONTAINER_WIDTH_M = 2.352
CONTAINER_HEIGHT_M = 2.698
CONTAINER_PAYLOAD_KG = 26000.0
HANDLING_CLEARANCE_MM = 5.0

SOURCE_FILES = [
    Path("BYD 1st ORDER/PI to BYD 宿舍采购一（加体积） (1).xlsx"),
    Path("BYD 1st ORDER/PI to BYD 宿舍采购二(加体积)(1).xlsx"),
    Path("BYD 2nd ORDER/251127- Quotation for BYD (1).xlsx"),
]

PVC_D2665 = {
    '2"': {"nominal": '2"', "dn": 50, "od_mm": 60.33, "wall_mm": 3.91},
    '3"': {"nominal": '3"', "dn": 75, "od_mm": 88.90, "wall_mm": 5.49},
    '4"': {"nominal": '4"', "dn": 100, "od_mm": 114.30, "wall_mm": 6.02},
}

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

STEEL_NPS_OD = {
    15: 21.3,
    20: 26.7,
    25: 33.4,
    32: 42.2,
    40: 48.3,
    50: 60.3,
    65: 73.0,
    80: 88.9,
    100: 114.3,
    150: 168.3,
}


@dataclass
class Pipe:
    source_file: str
    product: str
    family: str
    nominal_size: str
    dn: int
    od_mm: float
    wall_mm: float | None
    id_mm: float | None
    length_m: float
    order_qty: int
    kg_per_pipe: float
    total_weight_kg: float
    can_telescope: bool
    telescope_note: str


def find_header_row(df_raw: pd.DataFrame) -> int:
    for idx in range(min(25, len(df_raw))):
        row_values = [str(v).strip() if pd.notna(v) else "" for v in df_raw.iloc[idx]]
        if any("Description" == v or "Product Description" in v for v in row_values):
            return idx
    return 12


def read_order_rows(path: Path) -> pd.DataFrame:
    raw = pd.read_excel(path, sheet_name=0, header=None)
    header_row = find_header_row(raw)
    df = pd.read_excel(path, sheet_name=0, header=header_row)

    cols = list(df.columns)
    desc_col = next((c for c in cols if "description" in str(c).lower()), cols[1])
    qty_col = next((c for c in cols if "qty" in str(c).lower()), None)
    nw_col = next((c for c in cols if str(c).strip().lower() in {"n.w", "n.w."}), None)

    if qty_col is None or nw_col is None:
        raise ValueError(f"Could not map quantity/net-weight columns in {path}")

    rows = []
    for _, row in df.iterrows():
        desc = "" if pd.isna(row.get(desc_col)) else str(row.get(desc_col)).strip()
        if not desc or "pipe" not in desc.lower() or "fitting" in desc.lower():
            continue
        try:
            qty = int(float(row.get(qty_col)))
            nw = float(row.get(nw_col))
        except (TypeError, ValueError):
            continue
        if qty <= 0 or nw <= 0:
            continue
        rows.append(
            {
                "source_file": path.name,
                "product": desc,
                "order_qty": qty,
                "total_weight_kg": nw,
                "kg_per_pipe": nw / qty,
            }
        )
    return pd.DataFrame(rows)


def parse_pipe(row: pd.Series) -> Pipe | None:
    desc = row["product"]
    desc_lower = desc.lower()

    length_match = re.search(r"(\d+(?:\.\d+)?)\s*m(?:\b|-|$)", desc_lower)
    length_m = float(length_match.group(1)) if length_match else 5.8

    dn_match = re.search(r"\bdn\s*(\d+)", desc_lower)
    inch_match = re.search(r'(\d+)"', desc)

    family = ""
    nominal_size = ""
    dn = 0
    od_mm = None
    wall_mm = None
    can_telescope = False
    note = ""

    if "pe100" in desc_lower and "water supply straight pipe" in desc_lower:
        family = "HDPE PE100 PN10"
        dn = int(dn_match.group(1)) if dn_match else 0
        nominal_size = f"DN{dn}"
        od_mm = float(dn)
        # The order says 1.0 MPa PE100. Treat as PN10/SDR17 unless supplier
        # confirms otherwise. This is conservative enough for DN315/DN110 fit.
        wall_mm = round(od_mm / 17.0, 2)
        can_telescope = True
        note = "Plain-end HDPE assumed; verify caps/strapping before loading."
    elif "pvc-u dwv drainage pipe" in desc_lower and inch_match:
        family = "PVC-U DWV"
        nominal_size = f'{inch_match.group(1)}"'
        dim = PVC_D2665.get(nominal_size)
        if not dim:
            return None
        dn = dim["dn"]
        od_mm = dim["od_mm"]
        wall_mm = dim["wall_mm"]
        can_telescope = True
        note = "Geometrically telescopable if plain-end. Do not telescope belled/socketed pipe without supplier approval."
    elif "pp-r" in desc_lower and "pipe" in desc_lower:
        family = "PP-R"
        dn = int(dn_match.group(1)) if dn_match else 0
        nominal_size = f"DN{dn}"
        dim = PPR_S32.get(dn) if "s3.2" in desc_lower or "hot" in desc_lower else PPR_S5.get(dn)
        if not dim:
            return None
        od_mm = dim["od_mm"]
        wall_mm = dim["wall_mm"]
        can_telescope = True
        note = "Check by ID/OD; DN20 rows excluded from this workbook."
    elif "steel-plastic composite pipe" in desc_lower:
        family = "PE-lined steel-plastic composite"
        dn = int(dn_match.group(1)) if dn_match else 0
        nominal_size = f"DN{dn}"
        od_mm = STEEL_NPS_OD.get(dn, float(dn))
        wall_mm = None
        can_telescope = False
        note = "Not telescopable: source workbook note says steel pipe cannot be nested."
    else:
        return None

    if dn == 20:
        return None

    id_mm = (od_mm - 2 * wall_mm) if wall_mm is not None else None
    return Pipe(
        source_file=row["source_file"],
        product=desc,
        family=family,
        nominal_size=nominal_size,
        dn=dn,
        od_mm=od_mm,
        wall_mm=wall_mm,
        id_mm=id_mm,
        length_m=length_m,
        order_qty=int(row["order_qty"]),
        kg_per_pipe=float(row["kg_per_pipe"]),
        total_weight_kg=float(row["total_weight_kg"]),
        can_telescope=can_telescope,
        telescope_note=note,
    )


def hex_stack_count(diameter_m: float) -> dict:
    def count_for(width_m: float, height_m: float) -> tuple[int, int, int, int]:
        if diameter_m <= 0 or width_m < diameter_m or height_m < diameter_m:
            return (0, 0, 0, 0)
        row_spacing = diameter_m * math.sqrt(3) / 2
        rows = int(math.floor((height_m - diameter_m) / row_spacing)) + 1
        full = int(math.floor(width_m / diameter_m))
        offset = max(0, int(math.floor((width_m - 0.5 * diameter_m) / diameter_m)))
        count_a = sum(full if r % 2 == 0 else offset for r in range(rows))
        count_b = sum(offset if r % 2 == 0 else full for r in range(rows))
        return (max(count_a, count_b), rows, full, offset)

    normal = count_for(CONTAINER_WIDTH_M, CONTAINER_HEIGHT_M)
    rotated = count_for(CONTAINER_HEIGHT_M, CONTAINER_WIDTH_M)
    if rotated[0] > normal[0]:
        orientation = "hex staggered, rows across width"
        count, rows, full, offset = rotated
    else:
        orientation = "hex staggered, rows across height"
        count, rows, full, offset = normal
    return {
        "cross_section_pipes": count,
        "hex_rows": rows,
        "full_row_pipes": full,
        "offset_row_pipes": offset,
        "orientation": orientation,
    }


def individual_plan(pipe: Pipe) -> dict:
    length_positions = int(math.floor(CONTAINER_LENGTH_M / pipe.length_m))
    stack = hex_stack_count(pipe.od_mm / 1000)
    geometric_limit = stack["cross_section_pipes"] * length_positions
    weight_limit = int(math.floor(CONTAINER_PAYLOAD_KG / pipe.kg_per_pipe)) if pipe.kg_per_pipe > 0 else 0
    recommended = max(0, min(geometric_limit, weight_limit))
    limiting = "weight" if weight_limit < geometric_limit else "space"
    return {
        "family": pipe.family,
        "nominal_size": pipe.nominal_size,
        "product": pipe.product,
        "length_m": pipe.length_m,
        "od_mm": pipe.od_mm,
        "kg_per_pipe": pipe.kg_per_pipe,
        "order_qty": pipe.order_qty,
        "length_positions": length_positions,
        **stack,
        "geometric_limit_pipes": geometric_limit,
        "weight_limit_pipes": weight_limit,
        "recommended_pipes_per_40ft": recommended,
        "limiting_factor": limiting,
        "containers_for_order_qty": math.ceil(pipe.order_qty / recommended) if recommended else None,
        "planned_weight_kg": recommended * pipe.kg_per_pipe,
        "payload_utilization_pct": recommended * pipe.kg_per_pipe / CONTAINER_PAYLOAD_KG * 100 if recommended else 0,
        "loading_pattern": (
            f"{length_positions} length positions x {stack['hex_rows']} staggered rows "
            f"({stack['full_row_pipes']}/{stack['offset_row_pipes']} pipes per row)"
        ),
        "source_file": pipe.source_file,
    }


def min_circle_diameter_for_n(n: int, item_diameter: float) -> float:
    factors = {
        1: 1.0,
        2: 2.0,
        3: 1.0 + 2.0 / math.sqrt(3),
        4: 1.0 + math.sqrt(2),
        5: 2.701,
        6: 3.0,
        7: 3.0,
    }
    if n in factors:
        return factors[n] * item_diameter
    # Fallback for larger counts. These cases are not expected for this order.
    return item_diameter * math.sqrt(n / 0.82)


def telescope_capacity(host: Pipe, inner: Pipe) -> int:
    if host.id_mm is None or not host.can_telescope or not inner.can_telescope:
        return 0
    if host.family != inner.family:
        return 0
    available = host.id_mm - HANDLING_CLEARANCE_MM
    if available <= 0:
        return 0
    cap = 0
    for n in range(1, 8):
        if min_circle_diameter_for_n(n, inner.od_mm) <= available:
            cap = n
    return cap


def telescope_rows(pipes: list[Pipe], individual: pd.DataFrame) -> pd.DataFrame:
    rows = []
    pipe_by_key = {(p.family, p.nominal_size): p for p in pipes}
    indiv_by_key = {
        (r["family"], r["nominal_size"]): r
        for r in individual.to_dict("records")
    }

    for host in pipes:
        for inner in pipes:
            if host is inner or host.length_m != inner.length_m:
                continue
            if host.od_mm <= inner.od_mm:
                continue
            cap = telescope_capacity(host, inner)
            if cap <= 0:
                continue
            host_plan = indiv_by_key[(host.family, host.nominal_size)]
            group_weight = host.kg_per_pipe + cap * inner.kg_per_pipe
            groups_by_space = int(host_plan["geometric_limit_pipes"])
            groups_by_weight = int(math.floor(CONTAINER_PAYLOAD_KG / group_weight))
            groups = min(groups_by_space, groups_by_weight)
            rows.append(
                {
                    "family": host.family,
                    "host_size": host.nominal_size,
                    "inner_size": inner.nominal_size,
                    "inner_pipes_per_host": cap,
                    "host_id_mm": host.id_mm,
                    "inner_od_mm": inner.od_mm,
                    "groups_per_40ft": groups,
                    "host_pipes_per_40ft": groups,
                    "inner_pipes_per_40ft": groups * cap,
                    "total_pipes_per_40ft": groups * (1 + cap),
                    "group_weight_kg": group_weight,
                    "planned_weight_kg": groups * group_weight,
                    "limiting_factor": "weight" if groups_by_weight < groups_by_space else "space",
                    "loading_pattern": host_plan["loading_pattern"],
                    "condition": host.telescope_note,
                }
            )

    # Add the practical PVC chain that is better than independent pair rows.
    pvc = {p.nominal_size: p for p in pipes if p.family == "PVC-U DWV"}
    if {'2"', '3"', '4"'}.issubset(pvc):
        host = pvc['4"']
        mid = pvc['3"']
        inner = pvc['2"']
        if telescope_capacity(host, mid) >= 1 and telescope_capacity(mid, inner) >= 1:
            host_plan = indiv_by_key[(host.family, host.nominal_size)]
            group_weight = host.kg_per_pipe + mid.kg_per_pipe + inner.kg_per_pipe
            groups_by_space = int(host_plan["geometric_limit_pipes"])
            groups_by_weight = int(math.floor(CONTAINER_PAYLOAD_KG / group_weight))
            groups = min(groups_by_space, groups_by_weight)
            rows.append(
                {
                    "family": "PVC-U DWV",
                    "host_size": '4"',
                    "inner_size": '3" + 2" chain',
                    "inner_pipes_per_host": 2,
                    "host_id_mm": host.id_mm,
                    "inner_od_mm": mid.od_mm,
                    "groups_per_40ft": groups,
                    "host_pipes_per_40ft": groups,
                    "inner_pipes_per_40ft": groups * 2,
                    "total_pipes_per_40ft": groups * 3,
                    "group_weight_kg": group_weight,
                    "planned_weight_kg": groups * group_weight,
                    "limiting_factor": "weight" if groups_by_weight < groups_by_space else "space",
                    "loading_pattern": host_plan["loading_pattern"],
                    "condition": host.telescope_note,
                }
            )
    return pd.DataFrame(rows).sort_values(["family", "host_size", "inner_size"])


def order_impact_rows(pipes: list[Pipe], individual: pd.DataFrame) -> pd.DataFrame:
    indiv = {(r["family"], r["nominal_size"]): r for r in individual.to_dict("records")}
    rows = []

    def plan_without(family: str, sizes: Iterable[str]) -> int:
        total = 0
        for size in sizes:
            p = next(p for p in pipes if p.family == family and p.nominal_size == size)
            cap = int(indiv[(family, size)]["recommended_pipes_per_40ft"])
            total += math.ceil(p.order_qty / cap)
        return total

    hdpe = {p.nominal_size: p for p in pipes if p.family == "HDPE PE100 PN10"}
    if {"DN315", "DN110"}.issubset(hdpe):
        host, inner = hdpe["DN315"], hdpe["DN110"]
        cap = telescope_capacity(host, inner)
        host_cap = int(indiv[(host.family, host.nominal_size)]["recommended_pipes_per_40ft"])
        inner_cap = int(indiv[(inner.family, inner.nominal_size)]["recommended_pipes_per_40ft"])
        nested_inner = min(inner.order_qty, host.order_qty * cap)
        without = math.ceil(host.order_qty / host_cap) + math.ceil(inner.order_qty / inner_cap)
        with_tel = math.ceil(host.order_qty / host_cap) + math.ceil((inner.order_qty - nested_inner) / inner_cap)
        rows.append(
            {
                "family": "HDPE PE100 PN10",
                "scenario": "DN110 nested into DN315",
                "order_quantities": f"DN315={host.order_qty:,}; DN110={inner.order_qty:,}",
                "nested_pipe_qty": nested_inner,
                "containers_without_telescoping": without,
                "containers_with_telescoping": with_tel,
                "containers_saved": without - with_tel,
                "note": f"Uses up to {cap} DN110 inside each DN315; DN315 pipe count remains the container driver.",
            }
        )

    pvc = {p.nominal_size: p for p in pipes if p.family == "PVC-U DWV"}
    if {'2"', '3"', '4"'}.issubset(pvc):
        p2, p3, p4 = pvc['2"'], pvc['3"'], pvc['4"']
        p4_cap = int(indiv[("PVC-U DWV", '4"')]["recommended_pipes_per_40ft"])
        without = plan_without("PVC-U DWV", ['2"', '3"', '4"'])
        chain = min(p3.order_qty, p4.order_qty, p2.order_qty)
        direct_2_in_4 = min(p2.order_qty - chain, p4.order_qty - chain)
        remaining_2 = p2.order_qty - chain - direct_2_in_4
        remaining_3 = p3.order_qty - chain
        with_tel = math.ceil(p4.order_qty / p4_cap)
        if remaining_3:
            with_tel += math.ceil(remaining_3 / int(indiv[("PVC-U DWV", '3"')]["recommended_pipes_per_40ft"]))
        if remaining_2:
            with_tel += math.ceil(remaining_2 / int(indiv[("PVC-U DWV", '2"')]["recommended_pipes_per_40ft"]))
        rows.append(
            {
                "family": "PVC-U DWV",
                "scenario": '4" hosts 3" hosts 2", then remaining 2" nested directly in 4"',
                "order_quantities": f'4"={p4.order_qty:,}; 3"={p3.order_qty:,}; 2"={p2.order_qty:,}',
                "nested_pipe_qty": chain * 2 + direct_2_in_4,
                "containers_without_telescoping": without,
                "containers_with_telescoping": with_tel,
                "containers_saved": without - with_tel,
                "note": "Only valid for plain-end PVC. Treat socket/bell-end PVC as not telescopable unless supplier approves.",
            }
        )

    return pd.DataFrame(rows)


def write_workbook(
    pipes_df: pd.DataFrame,
    individual_df: pd.DataFrame,
    tel_df: pd.DataFrame,
    impact_df: pd.DataFrame,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="xlsxwriter") as writer:
        workbook = writer.book
        title_fmt = workbook.add_format({"bold": True, "font_size": 14, "font_color": "#1F4E78"})
        header_fmt = workbook.add_format({"bold": True, "bg_color": "#D9EAF7", "border": 1})
        note_fmt = workbook.add_format({"text_wrap": True, "valign": "top"})
        num_fmt = workbook.add_format({"num_format": "#,##0"})
        dec_fmt = workbook.add_format({"num_format": "#,##0.00"})
        pct_fmt = workbook.add_format({"num_format": "0.0"})

        summary_rows = [
            ["Deliverable", "Status"],
            ["Individual same-size 40ft packing plans", "Included in Individual_40ft_Plans"],
            ["Telescopable 40ft packing plans", "Included in Telescoped_40ft_Plans"],
            ["PVC telescoping check", "Geometrically possible for 2/3/4 inch plain-end PVC; not recommended for belled/socketed PVC without supplier approval"],
            ["DN20 exclusion", "DN20 pipe rows are excluded from all plan sheets"],
            ["HDPE arrangement", "DN315 hosts can carry 4 x DN110; same-size DN315 and DN110 plans also included"],
        ]
        pd.DataFrame(summary_rows[1:], columns=summary_rows[0]).to_excel(
            writer, sheet_name="Executive_Summary", index=False, startrow=2
        )
        ws = writer.sheets["Executive_Summary"]
        ws.write("A1", "40ft Pipe Packing Plan", title_fmt)
        ws.set_column("A:A", 42)
        ws.set_column("B:B", 120, note_fmt)

        assumptions = pd.DataFrame(
            [
                ["40ft HC internal dimensions", f"{CONTAINER_LENGTH_M}m L x {CONTAINER_WIDTH_M}m W x {CONTAINER_HEIGHT_M}m H"],
                ["Payload cap used", f"{CONTAINER_PAYLOAD_KG:,.0f} kg"],
                ["Pipe arrangement model", "Two 5.8m or 6.0m pipe lengths placed along the 40ft container length, with hexagonal/staggered cross-section stacking."],
                ["Telescoping clearance", f"{HANDLING_CLEARANCE_MM:.0f} mm deducted from host pipe ID."],
                ["HDPE dimensions", "PE100 1.0MPa treated as PN10/SDR17; supplier should confirm exact SDR/wall before execution."],
                ["PVC dimensions", "ASTM D2665/Schedule-40 DWV OD/wall used for 2, 3, and 4 inch PVC-U DWV pipe."],
                ["PVC telescoping decision", "Can telescope only if plain-end pipe and supplier accepts loading method; belled/socketed pipe should be treated as not telescopable."],
                ["Steel-plastic composite", "Marked non-telescopable due to source note that steel pipe cannot be nested."],
                ["Source links", "40ft HC dimensions: https://cscontainers.co.uk/guides/40ft-shipping-container-dimensions/ ; HDPE PN10 SDR17: https://www.piping-world.com/hdpe-pipe-dimensions-and-weights-pe100-pn10-sdr-17 ; PVC D2665 dimensions: https://www.engineeringtoolbox.com/ASTM-D2665-PVC-pipe-drain-waste-vent-pipe-d_2128.html"],
            ],
            columns=["Assumption", "Value"],
        )
        assumptions.to_excel(writer, sheet_name="Assumptions", index=False)

        pipes_df.to_excel(writer, sheet_name="Pipe_Input_Data", index=False)
        individual_df.to_excel(writer, sheet_name="Individual_40ft_Plans", index=False)
        tel_df.to_excel(writer, sheet_name="Telescoping_Capacity", index=False)
        tel_df.to_excel(writer, sheet_name="Telescoped_40ft_Plans", index=False)
        impact_df.to_excel(writer, sheet_name="Order_Impact", index=False)

        for sheet_name in writer.sheets:
            ws = writer.sheets[sheet_name]
            ws.freeze_panes(1, 0)
            ws.autofilter(0, 0, 0, 20)
            ws.set_row(0, None, header_fmt)

        widths = {
            "Assumptions": [28, 140],
            "Pipe_Input_Data": [26, 70, 24, 14, 8, 11, 11, 11, 10, 12, 12, 14, 14, 16, 60],
            "Individual_40ft_Plans": [22, 12, 70, 10, 10, 12, 12, 15, 16, 10, 13, 13, 15, 15, 18, 14, 16, 14, 16, 55, 30],
            "Telescoping_Capacity": [22, 12, 18, 18, 12, 12, 16, 16, 16, 16, 14, 16, 14, 55, 70],
            "Telescoped_40ft_Plans": [22, 12, 18, 18, 12, 12, 16, 16, 16, 16, 14, 16, 14, 55, 70],
            "Order_Impact": [22, 60, 44, 16, 24, 24, 16, 80],
        }
        for sheet_name, sheet_widths in widths.items():
            ws = writer.sheets[sheet_name]
            for col, width in enumerate(sheet_widths):
                ws.set_column(col, col, width, note_fmt if width >= 44 else None)

        for sheet_name in ["Pipe_Input_Data", "Individual_40ft_Plans", "Telescoping_Capacity", "Telescoped_40ft_Plans", "Order_Impact"]:
            ws = writer.sheets[sheet_name]
            max_row = len(
                {
                    "Pipe_Input_Data": pipes_df,
                    "Individual_40ft_Plans": individual_df,
                    "Telescoping_Capacity": tel_df,
                    "Telescoped_40ft_Plans": tel_df,
                    "Order_Impact": impact_df,
                }[sheet_name]
            )
            if max_row > 0:
                ws.conditional_format(1, 0, max_row, 0, {"type": "no_blanks", "format": workbook.add_format({"border": 1})})

        # Apply common numeric formats where column names match.
        for sheet_name, df in {
            "Pipe_Input_Data": pipes_df,
            "Individual_40ft_Plans": individual_df,
            "Telescoping_Capacity": tel_df,
            "Telescoped_40ft_Plans": tel_df,
            "Order_Impact": impact_df,
        }.items():
            ws = writer.sheets[sheet_name]
            for idx, col in enumerate(df.columns):
                if col.endswith("_pct"):
                    ws.set_column(idx, idx, 14, pct_fmt)
                elif "weight" in col or col.endswith("_kg") or col.endswith("_m") or col.endswith("_mm"):
                    ws.set_column(idx, idx, None, dec_fmt)
                elif "qty" in col or "pipes" in col or "containers" in col or "groups" in col:
                    ws.set_column(idx, idx, None, num_fmt)


def main() -> None:
    raw_rows = pd.concat([read_order_rows(path) for path in SOURCE_FILES], ignore_index=True)
    pipes = [p for p in (parse_pipe(row) for _, row in raw_rows.iterrows()) if p is not None]
    pipes = sorted(pipes, key=lambda p: (p.family, p.dn, p.nominal_size))

    pipes_df = pd.DataFrame([p.__dict__ for p in pipes])
    individual_df = pd.DataFrame([individual_plan(p) for p in pipes])
    tel_df = telescope_rows(pipes, individual_df)
    impact_df = order_impact_rows(pipes, individual_df)

    write_workbook(pipes_df, individual_df, tel_df, impact_df)
    print(OUTPUT_FILE)


if __name__ == "__main__":
    main()
