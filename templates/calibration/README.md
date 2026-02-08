# Calibration Templates

ZPL templates for calibrating label positioning on Zebra printers.

## Usage

Send directly to your printer via TCP:

```bash
cat circle-50mm-300dpi.zpl | nc <printer-ip> 9100
```

## Available Templates

### circle-50mm-300dpi.zpl

Calibration label for 50mm circular labels at 300 DPI.

Features:
- Circle outline (45mm with margin)
- Center crosshairs and dot
- TOP/BOTTOM/L/R orientation markers
- Current offset values displayed

## Adjusting Label Offset

The `^LH{x},{y}` command sets the label home position (origin offset):
- `x` = horizontal offset in dots (positive = right)
- `y` = vertical offset in dots (positive = down)

### Calculating Offset

For centered labels on a 4-inch (104mm) printhead:

```
offset_mm = (printhead_width_mm - label_width_mm) / 2
offset_dots = offset_mm × (dpi / 25.4)
```

Example for 50mm label at 300 DPI:
```
offset_mm = (104 - 50) / 2 = 27mm
offset_dots = 27 × 11.8 = 319 dots
```

### DPI Conversion

| DPI | Dots per mm |
|-----|-------------|
| 203 | 8.0 |
| 300 | 11.8 |
| 600 | 23.6 |

## Saving Offset to Printer

Once you find the correct offset, save it permanently:

```zpl
^XA^LH{x},{y}^JUS^XZ
```

This stores the offset in the printer's flash memory.

## Other Useful Commands

```zpl
~JC              ; Calibrate media sensor
~SD{n}           ; Set darkness (0-30)
^MNA             ; Continuous media mode
^MNY             ; Web sensing (gap between labels)
^MNM             ; Mark sensing (black mark)
```
