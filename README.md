# PackingPlan

A Python tool for optimizing container packing plans for pipe and fitting shipments. Calculates optimal container requirements with automatic pipe nesting to minimize shipping volume.

## Features

- **Multi-file Support**: Combine multiple PI (Proforma Invoice) Excel files into a single packing plan
- **Pipe Nesting Optimization**: Automatically nests smaller pipes inside larger ones to reduce volume by ~20-25%
- **Smart Column Detection**: Automatically identifies weight, volume, and quantity columns from Excel files
- **Data Validation**: Cross-validates weight using `weight_per_piece × quantity` to catch data errors
- **Container Optimization**: Calculates optimal mix of 40ft and 20ft containers
- **Detailed Reports**: Generates Excel reports with container assignments and utilization metrics

## Supported Pipe Types

| Pipe Type | Sizes | Nesting |
|-----------|-------|---------|
| PP-R Cold Water (S5) | DN20-DN63 | ✅ Yes |
| PP-R Hot Water (S3.2) | DN20-DN40 | ✅ Yes |
| PVC-U DWV (US Standard) | 2"-6" | ✅ Yes |
| Steel-Plastic Composite | DN15-DN150 | ❌ No* |

*Steel-plastic composite pipes cannot be nested due to internal lining.

## Installation

```bash
pip install -r requirements.txt
```

Requirements:
- Python 3.8+
- pandas
- xlsxwriter (for Excel output)

## Usage

```bash
python combine_and_pack.py <input_file1.xlsx> [input_file2.xlsx ...] -o <output.xlsx>
```

### Example

```bash
python combine_and_pack.py "PI_Order_1.xlsx" "PI_Order_2.xlsx" -o packing_plan.xlsx
```

### Output

The script generates:
1. **Console output** with summary statistics
2. **Excel file** with 4 sheets:
   - `Combined_Items`: All items from input files
   - `Summary`: Weight, volume, and container requirements
   - `Container_Packing`: Detailed item-to-container assignments
   - `Container_Summary`: Utilization per container

## How It Works

### 1. Data Extraction
Reads Excel files and extracts:
- Product descriptions
- Quantities
- Total volume (from `volumn` column)
- Net weight (from `N.W` column)
- Weight per piece (for validation)

### 2. Pipe Nesting
Identifies nestable pipes and optimizes packing:

```
BEFORE NESTING:                    AFTER NESTING:
┌───┐ ┌───┐ ┌───┐                 ┌─────────────┐
│4" │ │3" │ │2" │                 │ 4" ┌─────┐  │
└───┘ └───┘ └───┘                 │    │ 3"  │  │
                                  │    │┌───┐│  │
Volume: 415 m³                    │    ││2" ││  │
                                  │    │└───┘│  │
                                  │    └─────┘  │
                                  └─────────────┘
                                  Volume: 319 m³ (-23%)
```

### 3. Container Assignment
Uses first-fit-decreasing bin packing algorithm:
- Splits oversized items into container-sized batches
- Packs heaviest items first
- Respects both weight (26,000 kg) and volume (68 m³) limits

## Container Specifications

| Container | Max Weight | Max Volume |
|-----------|------------|------------|
| 40ft HC | 26,000 kg | 68 m³ |
| 20ft | 21,700 kg | 33 m³ |

## Example Output

```
======================================================================
CONTAINER REQUIREMENTS ANALYSIS
======================================================================

BEFORE NESTING:
  Total Volume: 415.13 m³
  Total Weight: 179,555.29 kg
  Containers by volume: 6.10
  Containers by weight: 6.91

AFTER NESTING:
  Total Volume: 319.30 m³
  Total Weight: 179,555.29 kg
  Containers by volume: 4.70
  Containers by weight: 6.91

  Volume saved by nesting: 95.82 m³ (23.1%)
  Limiting factor: WEIGHT

RECOMMENDED (after nesting):
  7 × 40ft container(s)
======================================================================
```

## Configuration

Container limits can be adjusted via environment variables:

```bash
export CONTAINER_MAX_WEIGHT_KG=26000.0
export CONTAINER_MAX_VOLUME_M3=68.0
```

## License

MIT License
