"""
Combined Packing Plan Script
Combines multiple PI Excel files and calculates container requirements.
Includes pipe nesting optimization to reduce volume.

Based on the actual column structure from the Excel files:
- Column N: Total Volume (m³) - THIS IS THE CORRECT VOLUME
- Column P: Net Total Weight (kg)
- Column R: Total Weight including carton weight (Gross Weight, kg)
- Column H: QTY(PC) - Quantity
"""

import argparse
import logging
import math
import os
import re
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional

import pandas as pd

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# --------- CONFIGURABLE CONSTANTS ---------

# 40' container constraints
CONTAINER_40FT_MAX_WEIGHT_KG = float(os.getenv("CONTAINER_MAX_WEIGHT_KG", "26000.0"))
CONTAINER_40FT_MAX_VOLUME_M3 = float(os.getenv("CONTAINER_MAX_VOLUME_M3", "68.0"))

# 20' container constraints
CONTAINER_20FT_MAX_WEIGHT_KG = 21700.0
CONTAINER_20FT_MAX_VOLUME_M3 = 33.0

# Nesting clearance (mm) - minimum gap between nested pipes
NESTING_CLEARANCE_MM = 2.0

# Circle packing efficiency - how much of the inner area can be filled
# Hexagonal close packing theoretical max is ~0.9069
CIRCLE_PACKING_EFFICIENCY = 0.85

# --------- PIPE DIMENSION DATABASE ---------

# PP-R pipes (OD in mm, wall thickness in mm)
# For S5 (1.25MPa cold water) - SDR 11
PPR_S5_PIPES = {
    20: (20, 1.9),   # DN20: OD=20mm, wall=1.9mm, ID=16.2mm
    25: (25, 2.3),   # DN25
    32: (32, 2.9),   # DN32
    40: (40, 3.7),   # DN40
    50: (50, 4.6),   # DN50
    63: (63, 5.8),   # DN63
}

# PP-R pipes for S3.2 (2.0MPa hot water) - SDR 6, thicker walls
PPR_S32_PIPES = {
    20: (20, 3.4),   # DN20: OD=20mm, wall=3.4mm, ID=13.2mm
    25: (25, 4.2),
    32: (32, 5.4),
    40: (40, 6.7),
}

# PVC-U DWV pipes (US Standard) - OD in mm, wall thickness in mm
PVC_DWV_PIPES = {
    50: (60.3, 3.0),    # 2" - DN50
    75: (88.9, 4.0),    # 3" - DN75
    100: (114.3, 5.0),  # 4" - DN100
    150: (168.3, 6.0),  # 6" - DN150
}


def get_pipe_dimensions(description: str, size_col: str) -> Optional[Tuple[float, float, float, bool]]:
    """
    Extract pipe dimensions from description and size column.

    Returns: (outer_diameter_mm, inner_diameter_mm, length_m, can_nest)
    Returns None if not a pipe or dimensions cannot be determined.
    """
    desc_lower = description.lower()

    # Skip if not a pipe
    if 'pipe' not in desc_lower or 'fitting' in desc_lower:
        return None

    # Check for non-nestable pipes (steel-plastic composite)
    # The Excel has a note "钢管不能套管" = "Steel pipes cannot be nested"
    if 'steel' in desc_lower or 'composite' in desc_lower or '钢' in description:
        return None  # Cannot nest

    # Extract DN size
    dn_match = re.search(r'[Dd][Nn](\d+)', description)
    inch_match = re.search(r'(\d+)"', description)

    # Extract length
    length_match = re.search(r'(\d+\.?\d*)\s*[Mm]', description)
    length_m = float(length_match.group(1)) if length_match else 5.8  # Default 5.8m

    od_mm = None
    id_mm = None

    # Determine pipe type and get dimensions
    if 'pp-r' in desc_lower or 'ppr' in desc_lower:
        if dn_match:
            dn = int(dn_match.group(1))
            # Check if hot water (S3.2) or cold water (S5)
            if 's3.2' in desc_lower or 'hot' in desc_lower or '热' in description:
                if dn in PPR_S32_PIPES:
                    od_mm, wall = PPR_S32_PIPES[dn]
                    id_mm = od_mm - 2 * wall
            else:
                if dn in PPR_S5_PIPES:
                    od_mm, wall = PPR_S5_PIPES[dn]
                    id_mm = od_mm - 2 * wall

    elif 'pvc' in desc_lower and 'dwv' in desc_lower:
        if inch_match:
            inch_size = int(inch_match.group(1))
            # Map inch to DN
            inch_to_dn = {2: 50, 3: 75, 4: 100, 6: 150}
            dn = inch_to_dn.get(inch_size)
            if dn and dn in PVC_DWV_PIPES:
                od_mm, wall = PVC_DWV_PIPES[dn]
                id_mm = od_mm - 2 * wall
        elif dn_match:
            dn = int(dn_match.group(1))
            if dn in PVC_DWV_PIPES:
                od_mm, wall = PVC_DWV_PIPES[dn]
                id_mm = od_mm - 2 * wall

    if od_mm and id_mm:
        return (od_mm, id_mm, length_m, True)

    return None


def calculate_nesting_capacity(outer_id_mm: float, inner_od_mm: float) -> int:
    """
    Calculate how many inner pipes can fit inside an outer pipe.
    Uses circle packing in a circle formula.

    Returns the number of inner pipes that can fit.
    """
    if inner_od_mm >= outer_id_mm - NESTING_CLEARANCE_MM:
        return 0

    # Available inner radius
    R = (outer_id_mm - NESTING_CLEARANCE_MM) / 2
    # Inner pipe radius
    r = inner_od_mm / 2

    if r >= R:
        return 0

    # For small ratios, use geometric calculation
    # Area-based estimate with packing efficiency
    outer_area = math.pi * R * R
    inner_area = math.pi * r * r

    # Maximum based on area
    max_by_area = int((outer_area / inner_area) * CIRCLE_PACKING_EFFICIENCY)

    # For very small inner pipes, area method works
    # For similar sizes, we need to check geometric fit
    if r > R * 0.4:
        # Check if at least one fits centered
        if inner_od_mm < outer_id_mm - NESTING_CLEARANCE_MM:
            return 1
        return 0

    return max(1, max_by_area)


def find_header_row(df_raw: pd.DataFrame) -> int:
    """Find the row index containing 'Part Number' or 'SAP Number' header."""
    for idx in range(min(20, len(df_raw))):
        row_values = [str(v).strip() if pd.notna(v) else '' for v in df_raw.iloc[idx]]
        if 'Part Number' in row_values or 'SAP Number' in row_values:
            return idx
    return 14  # Default fallback


def load_and_normalize_pi(filepath: str) -> pd.DataFrame:
    """
    Load a PI Excel file and normalize it to a standard format.

    Returns DataFrame with columns:
    - description: Product description
    - quantity: Number of pieces
    - total_volume_m3: Total volume in cubic meters
    - net_weight_kg: Net weight in kg
    - gross_weight_kg: Gross weight (including carton) in kg
    - source_file: Which file this came from
    """
    logger.info(f"Loading file: {filepath}")

    # First read raw to find header row
    df_raw = pd.read_excel(filepath, header=None)
    header_row = find_header_row(df_raw)
    logger.info(f"Found header at row {header_row}")

    # Read with proper header
    df = pd.read_excel(filepath, header=header_row)

    # Debug: show all columns
    logger.debug(f"Columns found: {list(df.columns)}")

    # Find data rows (those with valid SAP numbers)
    data_rows = []
    sap_col = None
    for col in df.columns:
        if 'SAP' in str(col):
            sap_col = col
            break

    if sap_col is None:
        # Try second column (index 1)
        sap_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]

    for idx in df.index:
        sap = df.loc[idx, sap_col]
        try:
            if pd.notna(sap) and (isinstance(sap, (int, float)) or str(sap).replace('.', '').isdigit()):
                data_rows.append(idx)
        except:
            pass

    df_data = df.loc[data_rows].copy()
    logger.info(f"Found {len(df_data)} data rows")

    # Map columns based on the actual Excel structure
    # The columns are positional - we need to identify them by position or partial name match

    # Build column mapping
    col_mapping = {}
    cols = list(df.columns)

    # Find description column (contains 'Product Description' or '产品描述')
    for i, col in enumerate(cols):
        col_str = str(col).lower()
        if 'product description' in col_str:
            col_mapping['description'] = col
        elif 'qty' in col_str and 'pc' in col_str:
            col_mapping['quantity'] = col

    # For the numeric columns, we need to look at the actual column names from the screenshots
    # N: Total Volume, O: Weight per piece, P: Net total Weight, R: Total Weight including carton

    # Find columns by looking for specific patterns
    for i, col in enumerate(cols):
        col_str = str(col).lower()

        # Volume columns
        if 'total volume' in col_str or col_str == 'total volume':
            col_mapping['total_volume'] = col
        elif col_str == 'volumn' or col_str == 'volume':
            # This might be the total volume column
            if 'total_volume' not in col_mapping:
                col_mapping['total_volume'] = col

        # Weight per piece column (for validation)
        if 'weight per piece' in col_str or 'weight/piece' in col_str:
            col_mapping['weight_per_piece'] = col

        # Weight columns
        if 'net' in col_str and 'weight' in col_str and 'per' not in col_str:
            col_mapping['net_weight'] = col
        elif col_str == 'n.w' or col_str == 'n.w.':
            col_mapping['net_weight'] = col

        if ('gross' in col_str or 'g.w' in col_str or 'including carton' in col_str) and 'weight' in col_str:
            col_mapping['gross_weight'] = col
        elif col_str == 'g.w' or col_str == 'g.w.':
            col_mapping['gross_weight'] = col

    # Fallback: use positional mapping based on screenshots
    # The key insight is that 'volumn' column IS the total volume (not unit volume)
    # and N.W and G.W are in the last columns

    if 'total_volume' not in col_mapping:
        # Look for 'volumn' column
        for col in cols:
            if str(col).lower() == 'volumn':
                col_mapping['total_volume'] = col
                break

    if 'net_weight' not in col_mapping:
        for col in cols:
            if str(col) == 'N.W':
                col_mapping['net_weight'] = col
                break

    if 'gross_weight' not in col_mapping:
        for col in cols:
            if str(col) == 'G.W':
                col_mapping['gross_weight'] = col
                break

    # Weight per piece is in 'Unnamed: 15' based on Excel structure analysis
    if 'weight_per_piece' not in col_mapping:
        for col in cols:
            if str(col) == 'Unnamed: 15':
                col_mapping['weight_per_piece'] = col
                break

    if 'quantity' not in col_mapping:
        for col in cols:
            if 'QTY' in str(col).upper():
                col_mapping['quantity'] = col
                break

    if 'description' not in col_mapping:
        for col in cols:
            if 'Description' in str(col) or 'description' in str(col):
                col_mapping['description'] = col
                break

    logger.info(f"Column mapping: {col_mapping}")

    # Create normalized dataframe
    normalized_rows = []

    for idx in data_rows:
        row = df.loc[idx]

        # Get description
        desc = ''
        if 'description' in col_mapping:
            desc = str(row[col_mapping['description']]) if pd.notna(row[col_mapping['description']]) else ''

        # Get quantity
        qty = 0
        if 'quantity' in col_mapping:
            qty_val = row[col_mapping['quantity']]
            if pd.notna(qty_val):
                try:
                    qty = int(float(qty_val))
                except:
                    qty = 0

        # Get total volume (this is the KEY fix - use the correct column!)
        total_vol = 0.0
        if 'total_volume' in col_mapping:
            vol_val = row[col_mapping['total_volume']]
            if pd.notna(vol_val):
                try:
                    total_vol = float(vol_val)
                except:
                    total_vol = 0.0

        # Get weight per piece (for validation)
        weight_per_piece = 0.0
        if 'weight_per_piece' in col_mapping:
            wpp_val = row[col_mapping['weight_per_piece']]
            if pd.notna(wpp_val):
                try:
                    weight_per_piece = float(wpp_val)
                except:
                    weight_per_piece = 0.0

        # Get net weight
        net_wt = 0.0
        if 'net_weight' in col_mapping:
            wt_val = row[col_mapping['net_weight']]
            if pd.notna(wt_val):
                try:
                    net_wt = float(wt_val)
                except:
                    net_wt = 0.0

        # Get gross weight
        gross_wt = 0.0
        if 'gross_weight' in col_mapping:
            wt_val = row[col_mapping['gross_weight']]
            if pd.notna(wt_val):
                try:
                    gross_wt = float(wt_val)
                except:
                    gross_wt = 0.0

        # SANITY CHECK: Validate net weight using weight_per_piece × quantity
        if weight_per_piece > 0 and qty > 0:
            calculated_weight = weight_per_piece * qty
            # If net weight differs significantly from calculated weight, use calculated
            if net_wt > 0:
                ratio = net_wt / calculated_weight
                if ratio > 2.0 or ratio < 0.5:
                    logger.warning(f"Net weight mismatch for: {desc[:50]}")
                    logger.warning(f"  N.W = {net_wt:,.2f} kg, but weight_per_piece × qty = {weight_per_piece} × {qty} = {calculated_weight:,.2f} kg")
                    logger.warning(f"  Using calculated weight instead")
                    net_wt = calculated_weight
            elif net_wt == 0:
                # Net weight missing, use calculated
                net_wt = calculated_weight
                logger.info(f"Using calculated weight for: {desc[:50]} ({calculated_weight:,.2f} kg)")

        # Sanity check for gross weight - it should be close to net weight (typically 1.0x-1.5x)
        # If G.W is suspiciously large, use N.W instead
        if gross_wt > net_wt * 3 and net_wt > 0:
            logger.warning(f"Suspicious G.W ({gross_wt:,.0f} kg) >> N.W ({net_wt:,.0f} kg) for: {desc[:50]}")
            logger.warning(f"  Using N.W instead of G.W for this item")
            gross_wt = net_wt * 1.05  # Assume 5% packaging overhead

        # Use net weight (with small overhead for packaging) as the primary weight
        # Since we want to use N.W as the basis
        if net_wt > 0:
            gross_wt = net_wt  # Use net weight directly as per user request

        # Skip rows with no meaningful data
        if qty == 0 and total_vol == 0 and net_wt == 0:
            continue

        # Sanity check: volume should typically be much smaller than weight for these products
        # Typical density for packed fittings/pipes: 100-500 kg/m³
        if total_vol > 0 and net_wt > 0:
            density = net_wt / total_vol
            if density < 50:  # Less than 50 kg/m³ is suspiciously light
                logger.warning(f"Low density ({density:.1f} kg/m³) for: {desc[:50]}")

        normalized_rows.append({
            'description': desc,
            'quantity': qty,
            'total_volume_m3': total_vol,
            'net_weight_kg': net_wt,
            'gross_weight_kg': gross_wt,
            'source_file': os.path.basename(filepath)
        })

    result_df = pd.DataFrame(normalized_rows)

    # Log summary
    logger.info(f"File summary:")
    logger.info(f"  Items: {len(result_df)}")
    logger.info(f"  Total Quantity: {result_df['quantity'].sum():,}")
    logger.info(f"  Total Volume: {result_df['total_volume_m3'].sum():.2f} m³")
    logger.info(f"  Total Net Weight: {result_df['net_weight_kg'].sum():,.2f} kg")
    logger.info(f"  Total Gross Weight: {result_df['gross_weight_kg'].sum():,.2f} kg")

    return result_df


def combine_pi_files(file_paths: List[str]) -> pd.DataFrame:
    """Combine multiple PI files into a single normalized DataFrame."""
    all_dfs = []

    for filepath in file_paths:
        df = load_and_normalize_pi(filepath)
        all_dfs.append(df)

    combined = pd.concat(all_dfs, ignore_index=True)

    logger.info(f"\n{'='*60}")
    logger.info(f"COMBINED TOTALS")
    logger.info(f"{'='*60}")
    logger.info(f"Total Items: {len(combined)}")
    logger.info(f"Total Quantity: {combined['quantity'].sum():,}")
    logger.info(f"Total Volume: {combined['total_volume_m3'].sum():.2f} m³")
    logger.info(f"Total Net Weight: {combined['net_weight_kg'].sum():,.2f} kg")
    logger.info(f"Total Gross Weight: {combined['gross_weight_kg'].sum():,.2f} kg")

    return combined


def calculate_container_requirements(
    df: pd.DataFrame,
    use_gross_weight: bool = True
) -> Dict[str, Any]:
    """
    Calculate how many containers are needed based on weight and volume constraints.

    Returns dict with container requirements analysis.
    """
    total_volume = df['total_volume_m3'].sum()
    total_weight = df['gross_weight_kg'].sum() if use_gross_weight else df['net_weight_kg'].sum()

    # Calculate containers needed by each constraint
    containers_by_volume = total_volume / CONTAINER_40FT_MAX_VOLUME_M3
    containers_by_weight = total_weight / CONTAINER_40FT_MAX_WEIGHT_KG

    # The limiting factor determines actual container count
    limiting_factor = 'volume' if containers_by_volume > containers_by_weight else 'weight'
    containers_needed = max(containers_by_volume, containers_by_weight)

    # Calculate optimal mix of 40ft and 20ft containers
    full_40ft = int(containers_needed)
    remainder_volume = total_volume - (full_40ft * CONTAINER_40FT_MAX_VOLUME_M3)
    remainder_weight = total_weight - (full_40ft * CONTAINER_40FT_MAX_WEIGHT_KG)

    # Check if remainder fits in a 20ft container
    needs_20ft = False
    if remainder_volume > 0 or remainder_weight > 0:
        if remainder_volume <= CONTAINER_20FT_MAX_VOLUME_M3 and remainder_weight <= CONTAINER_20FT_MAX_WEIGHT_KG:
            needs_20ft = True
        else:
            full_40ft += 1  # Need another 40ft instead

    return {
        'total_volume_m3': total_volume,
        'total_weight_kg': total_weight,
        'containers_by_volume': containers_by_volume,
        'containers_by_weight': containers_by_weight,
        'limiting_factor': limiting_factor,
        'recommended_40ft': full_40ft,
        'recommended_20ft': 1 if needs_20ft else 0,
        'volume_utilization': total_volume / ((full_40ft * CONTAINER_40FT_MAX_VOLUME_M3) +
                                               (CONTAINER_20FT_MAX_VOLUME_M3 if needs_20ft else 0)) * 100,
        'weight_utilization': total_weight / ((full_40ft * CONTAINER_40FT_MAX_WEIGHT_KG) +
                                               (CONTAINER_20FT_MAX_WEIGHT_KG if needs_20ft else 0)) * 100,
    }


def apply_pipe_nesting(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply pipe nesting optimization to reduce total volume.

    Smaller pipes are nested inside larger pipes where possible.
    This reduces the effective volume while keeping the same weight.

    Returns a new DataFrame with adjusted volumes for nested pipes.
    """
    logger.info("\n" + "="*60)
    logger.info("APPLYING PIPE NESTING OPTIMIZATION")
    logger.info("="*60)

    # First, identify all nestable pipes and their dimensions
    pipes = []
    for idx, row in df.iterrows():
        desc = row['description']
        dims = get_pipe_dimensions(desc, '')

        if dims:
            od_mm, id_mm, length_m, can_nest = dims
            pipes.append({
                'idx': idx,
                'description': desc,
                'od_mm': od_mm,
                'id_mm': id_mm,
                'length_m': length_m,
                'quantity': row['quantity'],
                'weight_per_piece': row['gross_weight_kg'] / row['quantity'] if row['quantity'] > 0 else 0,
                'volume_per_piece': row['total_volume_m3'] / row['quantity'] if row['quantity'] > 0 else 0,
                'remaining_qty': row['quantity'],
                'nested_inside': None,
            })

    if not pipes:
        logger.info("No nestable pipes found")
        return df

    logger.info(f"Found {len(pipes)} nestable pipe types:")
    for p in pipes:
        logger.info(f"  {p['description'][:50]}: OD={p['od_mm']}mm, ID={p['id_mm']:.1f}mm, qty={p['quantity']}")

    # Sort pipes by OD (largest first) for outer pipes
    pipes_sorted = sorted(pipes, key=lambda x: x['od_mm'], reverse=True)

    # Track nesting assignments
    nesting_groups = []  # List of {outer_pipe, inner_pipes: [{pipe, qty}], volume_saved}

    # For each potential outer pipe, try to nest smaller pipes inside
    for i, outer in enumerate(pipes_sorted):
        if outer['remaining_qty'] <= 0:
            continue

        outer_id = outer['id_mm']
        outer_length = outer['length_m']

        # Find pipes that can fit inside
        nestable_inners = []
        for inner in pipes_sorted[i+1:]:
            if inner['remaining_qty'] <= 0:
                continue
            # Check if inner pipe fits inside outer
            if inner['od_mm'] < outer_id - NESTING_CLEARANCE_MM:
                # Check length compatibility (within 0.5m)
                if abs(inner['length_m'] - outer_length) <= 0.5:
                    capacity = calculate_nesting_capacity(outer_id, inner['od_mm'])
                    if capacity > 0:
                        nestable_inners.append({
                            'pipe': inner,
                            'capacity': capacity
                        })

        if not nestable_inners:
            continue

        # For each outer pipe, nest as many inner pipes as possible
        for _ in range(outer['remaining_qty']):
            if outer['remaining_qty'] <= 0:
                break

            nested_in_this = []
            remaining_area = math.pi * (outer_id / 2) ** 2 * CIRCLE_PACKING_EFFICIENCY

            # Try to fill this outer pipe with smaller pipes (largest inner first)
            for inner_info in nestable_inners:
                inner = inner_info['pipe']
                if inner['remaining_qty'] <= 0:
                    continue

                inner_area = math.pi * (inner['od_mm'] / 2) ** 2
                can_fit = int(remaining_area / inner_area)
                actual_fit = min(can_fit, inner['remaining_qty'])

                if actual_fit > 0:
                    nested_in_this.append({
                        'pipe': inner,
                        'qty': actual_fit
                    })
                    inner['remaining_qty'] -= actual_fit
                    remaining_area -= actual_fit * inner_area

            if nested_in_this:
                # Calculate volume saved
                outer_cylinder_vol = math.pi * (outer['od_mm'] / 1000 / 2) ** 2 * outer_length
                inner_vol_saved = sum(
                    n['qty'] * n['pipe']['volume_per_piece']
                    for n in nested_in_this
                )

                nesting_groups.append({
                    'outer': outer,
                    'outer_qty': 1,
                    'nested': nested_in_this,
                    'volume_saved': inner_vol_saved
                })
                outer['remaining_qty'] -= 1

    # Calculate total volume savings
    total_volume_saved = sum(g['volume_saved'] for g in nesting_groups)

    if total_volume_saved > 0:
        logger.info(f"\nNesting Results:")
        logger.info(f"  Total nesting groups: {len(nesting_groups)}")
        logger.info(f"  Total volume saved: {total_volume_saved:.2f} m³")

        for g in nesting_groups[:5]:  # Show first 5 groups
            outer_desc = g['outer']['description'][:40]
            nested_desc = ", ".join(f"{n['qty']}x {n['pipe']['description'][:20]}" for n in g['nested'])
            logger.info(f"  {outer_desc} <- [{nested_desc}]")
        if len(nesting_groups) > 5:
            logger.info(f"  ... and {len(nesting_groups) - 5} more groups")

    # Create new DataFrame with adjusted volumes
    # The nested pipes' volume is "absorbed" by the outer pipe
    new_rows = []
    nested_pipe_indices = set()

    for g in nesting_groups:
        for n in g['nested']:
            # Track which pipes have been fully or partially nested
            pass  # We'll handle this by adjusting volumes

    # Rebuild the DataFrame with volume adjustments
    for idx, row in df.iterrows():
        new_row = row.to_dict()

        # Find if this pipe was involved in nesting
        for pipe in pipes:
            if pipe['idx'] == idx:
                # Calculate how many were nested away
                nested_away = pipe['quantity'] - pipe['remaining_qty']
                if nested_away > 0:
                    # Reduce volume for nested pipes (their volume is inside outer pipes)
                    original_vol_per_piece = row['total_volume_m3'] / row['quantity'] if row['quantity'] > 0 else 0
                    new_row['total_volume_m3'] = pipe['remaining_qty'] * original_vol_per_piece
                    new_row['_nested_qty'] = nested_away
                    new_row['_original_volume'] = row['total_volume_m3']
                    logger.debug(f"  {row['description'][:40]}: {nested_away} nested, vol {row['total_volume_m3']:.2f} -> {new_row['total_volume_m3']:.2f}")
                break

        new_rows.append(new_row)

    result_df = pd.DataFrame(new_rows)

    # Summary
    original_vol = df['total_volume_m3'].sum()
    new_vol = result_df['total_volume_m3'].sum()
    logger.info(f"\nVolume Summary:")
    logger.info(f"  Original total volume: {original_vol:.2f} m³")
    logger.info(f"  After nesting: {new_vol:.2f} m³")
    logger.info(f"  Volume saved: {original_vol - new_vol:.2f} m³ ({(original_vol - new_vol) / original_vol * 100:.1f}%)")

    return result_df


def split_large_items(df: pd.DataFrame) -> pd.DataFrame:
    """
    Split items that exceed container limits into smaller batches.

    Returns a new DataFrame where large items are split into container-sized chunks.
    """
    new_rows = []

    for idx, row in df.iterrows():
        qty = row['quantity']
        total_weight = row['gross_weight_kg']
        total_volume = row['total_volume_m3']

        if qty == 0:
            new_rows.append(row.to_dict())
            continue

        # Calculate per-piece values
        weight_per_piece = total_weight / qty
        volume_per_piece = total_volume / qty if total_volume > 0 else 0

        # Check if item needs splitting
        if total_weight <= CONTAINER_40FT_MAX_WEIGHT_KG and total_volume <= CONTAINER_40FT_MAX_VOLUME_M3:
            new_rows.append(row.to_dict())
            continue

        # Calculate max pieces per container
        max_by_weight = int(CONTAINER_40FT_MAX_WEIGHT_KG / weight_per_piece) if weight_per_piece > 0 else qty
        max_by_volume = int(CONTAINER_40FT_MAX_VOLUME_M3 / volume_per_piece) if volume_per_piece > 0 else qty
        max_per_container = max(1, min(max_by_weight, max_by_volume))

        # Split into batches
        remaining = qty
        batch_num = 1
        while remaining > 0:
            batch_qty = min(remaining, max_per_container)
            batch_weight = weight_per_piece * batch_qty
            batch_volume = volume_per_piece * batch_qty

            new_row = row.to_dict()
            new_row['quantity'] = batch_qty
            new_row['gross_weight_kg'] = batch_weight
            new_row['net_weight_kg'] = batch_weight
            new_row['total_volume_m3'] = batch_volume
            new_row['description'] = f"{row['description']} (batch {batch_num})"
            new_rows.append(new_row)

            remaining -= batch_qty
            batch_num += 1

        logger.info(f"Split '{row['description'][:50]}' into {batch_num - 1} batches")

    return pd.DataFrame(new_rows)


def pack_items_into_containers(df: pd.DataFrame, enable_nesting: bool = True) -> List[Dict[str, Any]]:
    """
    Pack items into containers using a greedy bin-packing algorithm.

    First applies pipe nesting (if enabled), then splits large items,
    then packs using first-fit-decreasing.

    Returns list of containers with their contents.
    """
    # First, apply pipe nesting optimization
    if enable_nesting:
        df_nested = apply_pipe_nesting(df)
    else:
        df_nested = df

    # Then, split any items that are too large for a single container
    df_split = split_large_items(df_nested)

    containers = []

    # Sort items by weight (heaviest first) for better packing
    df_sorted = df_split.sort_values('gross_weight_kg', ascending=False).reset_index(drop=True)

    # Track which items have been packed
    packed = [False] * len(df_sorted)

    while not all(packed):
        # Start a new container
        container = {
            'container_id': len(containers) + 1,
            'items': [],
            'total_weight_kg': 0.0,
            'total_volume_m3': 0.0,
        }

        # Try to add items to this container
        for idx in range(len(df_sorted)):
            if packed[idx]:
                continue

            row = df_sorted.iloc[idx]
            item_weight = row['gross_weight_kg']
            item_volume = row['total_volume_m3']

            # Check if item fits
            if (container['total_weight_kg'] + item_weight <= CONTAINER_40FT_MAX_WEIGHT_KG and
                container['total_volume_m3'] + item_volume <= CONTAINER_40FT_MAX_VOLUME_M3):

                container['items'].append({
                    'description': row['description'],
                    'quantity': row['quantity'],
                    'weight_kg': item_weight,
                    'volume_m3': item_volume,
                    'source_file': row['source_file'],
                })
                container['total_weight_kg'] += item_weight
                container['total_volume_m3'] += item_volume
                packed[idx] = True

        # If container is empty but there are unpacked items, force-add the next one
        # (this should rarely happen after splitting, but keep as safety)
        if len(container['items']) == 0:
            for idx in range(len(df_sorted)):
                if not packed[idx]:
                    row = df_sorted.iloc[idx]
                    container['items'].append({
                        'description': row['description'],
                        'quantity': row['quantity'],
                        'weight_kg': row['gross_weight_kg'],
                        'volume_m3': row['total_volume_m3'],
                        'source_file': row['source_file'],
                    })
                    container['total_weight_kg'] = row['gross_weight_kg']
                    container['total_volume_m3'] = row['total_volume_m3']
                    packed[idx] = True
                    logger.warning(f"Item still exceeds limits after split: {row['description'][:50]}")
                    break

        containers.append(container)

    return containers


def write_results_to_excel(
    df: pd.DataFrame,
    containers: List[Dict[str, Any]],
    requirements: Dict[str, Any],
    output_path: str
):
    """Write the analysis results to an Excel file."""

    with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
        # Sheet 1: Combined items
        df.to_excel(writer, sheet_name='Combined_Items', index=False)

        # Sheet 2: Summary
        summary_data = {
            'Metric': [
                'Total Items',
                'Total Quantity (pieces)',
                'Total Volume (m³)',
                'Total Gross Weight (kg)',
                'Containers by Volume',
                'Containers by Weight',
                'Limiting Factor',
                'Recommended 40ft Containers',
                'Recommended 20ft Containers',
                'Volume Utilization (%)',
                'Weight Utilization (%)',
            ],
            'Value': [
                len(df),
                df['quantity'].sum(),
                f"{requirements['total_volume_m3']:.2f}",
                f"{requirements['total_weight_kg']:,.2f}",
                f"{requirements['containers_by_volume']:.2f}",
                f"{requirements['containers_by_weight']:.2f}",
                requirements['limiting_factor'].upper(),
                requirements['recommended_40ft'],
                requirements['recommended_20ft'],
                f"{requirements['volume_utilization']:.1f}%",
                f"{requirements['weight_utilization']:.1f}%",
            ]
        }
        pd.DataFrame(summary_data).to_excel(writer, sheet_name='Summary', index=False)

        # Sheet 3: Container packing details
        packing_rows = []
        for container in containers:
            for item in container['items']:
                packing_rows.append({
                    'Container': container['container_id'],
                    'Container_Weight_kg': container['total_weight_kg'],
                    'Container_Volume_m3': container['total_volume_m3'],
                    'Weight_Util_%': container['total_weight_kg'] / CONTAINER_40FT_MAX_WEIGHT_KG * 100,
                    'Volume_Util_%': container['total_volume_m3'] / CONTAINER_40FT_MAX_VOLUME_M3 * 100,
                    'Item_Description': item['description'][:80],
                    'Item_Quantity': item['quantity'],
                    'Item_Weight_kg': item['weight_kg'],
                    'Item_Volume_m3': item['volume_m3'],
                    'Source_File': item['source_file'],
                })
        pd.DataFrame(packing_rows).to_excel(writer, sheet_name='Container_Packing', index=False)

        # Sheet 4: Container summary
        container_summary = []
        for container in containers:
            container_summary.append({
                'Container': container['container_id'],
                'Items_Count': len(container['items']),
                'Total_Weight_kg': container['total_weight_kg'],
                'Total_Volume_m3': container['total_volume_m3'],
                'Weight_Utilization_%': container['total_weight_kg'] / CONTAINER_40FT_MAX_WEIGHT_KG * 100,
                'Volume_Utilization_%': container['total_volume_m3'] / CONTAINER_40FT_MAX_VOLUME_M3 * 100,
            })
        pd.DataFrame(container_summary).to_excel(writer, sheet_name='Container_Summary', index=False)

    logger.info(f"Results written to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Combine PI Excel files and calculate container packing plan."
    )
    parser.add_argument(
        "input_files",
        nargs='+',
        help="Path(s) to PI Excel file(s)",
    )
    parser.add_argument(
        "-o", "--output",
        default="combined_packing_plan.xlsx",
        help="Output Excel file (default: combined_packing_plan.xlsx)",
    )
    args = parser.parse_args()

    # Set up logging
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"packing_log_{timestamp}.txt"

    file_handler = logging.FileHandler(log_filename, mode='w', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
    logger.addHandler(console_handler)

    logger.info(f"Processing {len(args.input_files)} file(s)")

    # Combine all input files
    combined_df = combine_pi_files(args.input_files)

    # Apply nesting optimization
    nested_df = apply_pipe_nesting(combined_df)

    # Calculate container requirements (before and after nesting)
    requirements_original = calculate_container_requirements(combined_df)
    requirements_nested = calculate_container_requirements(nested_df)

    # Print summary
    print("\n" + "=" * 70)
    print("CONTAINER REQUIREMENTS ANALYSIS")
    print("=" * 70)

    print("\nBEFORE NESTING:")
    print(f"  Total Volume: {requirements_original['total_volume_m3']:.2f} m³")
    print(f"  Total Weight: {requirements_original['total_weight_kg']:,.2f} kg")
    print(f"  Containers by volume: {requirements_original['containers_by_volume']:.2f}")
    print(f"  Containers by weight: {requirements_original['containers_by_weight']:.2f}")

    print("\nAFTER NESTING:")
    print(f"  Total Volume: {requirements_nested['total_volume_m3']:.2f} m³")
    print(f"  Total Weight: {requirements_nested['total_weight_kg']:,.2f} kg")
    print(f"  Containers by volume: {requirements_nested['containers_by_volume']:.2f}")
    print(f"  Containers by weight: {requirements_nested['containers_by_weight']:.2f}")

    vol_saved = requirements_original['total_volume_m3'] - requirements_nested['total_volume_m3']
    print(f"\n  Volume saved by nesting: {vol_saved:.2f} m³ ({vol_saved/requirements_original['total_volume_m3']*100:.1f}%)")
    print(f"  Limiting factor: {requirements_nested['limiting_factor'].upper()}")

    print(f"\nRECOMMENDED (after nesting):")
    print(f"  {requirements_nested['recommended_40ft']} × 40ft container(s)")
    if requirements_nested['recommended_20ft'] > 0:
        print(f"  {requirements_nested['recommended_20ft']} × 20ft container(s)")
    print(f"\nExpected Utilization:")
    print(f"  Volume: {requirements_nested['volume_utilization']:.1f}%")
    print(f"  Weight: {requirements_nested['weight_utilization']:.1f}%")
    print("=" * 70)

    # Pack items into containers (nesting already applied to nested_df)
    containers = pack_items_into_containers(nested_df, enable_nesting=False)  # Already nested

    print(f"\nActual packing result: {len(containers)} containers")
    for c in containers:
        print(f"  Container {c['container_id']}: {c['total_weight_kg']:,.1f} kg ({c['total_weight_kg']/CONTAINER_40FT_MAX_WEIGHT_KG*100:.1f}%), "
              f"{c['total_volume_m3']:.2f} m³ ({c['total_volume_m3']/CONTAINER_40FT_MAX_VOLUME_M3*100:.1f}%)")

    # Write results
    write_results_to_excel(nested_df, containers, requirements_nested, args.output)

    print(f"\nResults saved to: {args.output}")
    print(f"Log file: {log_filename}")


if __name__ == "__main__":
    main()
