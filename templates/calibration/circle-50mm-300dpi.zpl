; Calibration label for 50mm circular labels at 300 DPI
; Adjust ^LH values to center on your media
; ^LH{x},{y} - x=horizontal offset, y=vertical offset (in dots)
; At 300 DPI: 1mm = 11.8 dots
;
; Example for 4-inch printhead with centered 50mm label:
; Offset = (104mm - 50mm) / 2 = 27mm = 319 dots
;
; ~SD{n} sets darkness (0-30)

^XA

~SD20
^PR2,2,2

; Adjust this offset for your printer/media setup
^LH319,0

; Circle outline (45mm diameter = 531 dots, with 2.5mm margin)
^FO24,24^GC543,3,B^FS

; Crosshairs
^FO295,24^GB0,543,2,B^FS
^FO24,295^GB543,0,2,B^FS

; Center dot
^FO290,290^GB10,10,10,B^FS

; Orientation markers
^FO265,60^A0N,30,30^FDTOP^FS
^FO245,520^A0N,30,30^FDBOTTOM^FS
^FO45,285^A0N,30,30^FDL^FS
^FO520,285^A0N,30,30^FDR^FS

; Label info
^FO200,150^A0N,40,40^FD50mm DIA^FS
^FO225,200^A0N,32,32^FD300 DPI^FS

; Current offset (update when you find correct values)
^FO150,350^A0N,26,26^FDOffset Test^FS
^FO160,390^A0N,22,22^FD^LH=319,0^FS

^XZ
