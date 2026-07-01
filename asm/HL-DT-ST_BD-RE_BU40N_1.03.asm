.arm.little
.thumb

.Open "../firmware/HL-DT-ST_BD-RE_BU40N_1.03.bin","../patched_firmware/HL-DT-ST_BD-RE_BU40N_1.03_OmniDrive.bin",0

; Free Space
.definedatalabel FreeSpaceStart,0x1A6AE0
.definedatalabel FreeSpaceEnd,0x1B001F

; Command Table
.definedatalabel CommandTableDVDRead1,0x150648
.definedatalabel CommandTableDVDRead2,0x150650
.definedatalabel CommandTableDVDRead3,0x150658
.definedataLabel CommandTableBDRead1,0x1508C0
.definedataLabel CommandTableBDRead2,0x1508C8
.definedatalabel CommandTableEnd,0x15051C

; Data
.definedatalabel mediaType,0x01FF9E04
.definedatalabel BDDIOffset,0x01FFA38C
.definedatalabel forceUnitAccess,0x01FFBE0D
.definedatalabel seekLayer,0x01FFBE1D
.definedatalabel isDiscPTP,0x01FFBE23
.definedatalabel memoryStart,0x02000C78 // 0x1C08000
.definedatalabel discStructOffset,0x02000C7C // 0x327C80
.definedatalabel lastSector,0x02000CA0
.definedatalabel startAddress,0x02000CA4
.definedatalabel transferLength,0x02000CB0
.definedatalabel readTimeCounter,0x02000CC4
.definedatalabel cdb,0x02000D40
.definedatalabel layer0End,0x02000DB0
.definedatalabel layer1End,0x02000DB4
.definedatalabel layer2End,0x02000DB8

; Functions
.definethumblabel ChangeDiscRWMode,0x043D78
.definethumblabel SetErrorMode,0x044AD0
.definethumblabel ReadDiscStructMemDWORD,0x0A1EFC
.definethumblabel ReturnSense,0x0A2B4E
.definethumblabel BDReadCmd,0x0AE770
.definethumblabel ReadDiscData,0x0BF7C2
.definethumblabel ReadCDDA,0x0C8FA6
.definethumblabel MSFtoLBA,0x0C9F12
.definethumblabel DVDReadCmd,0x117B80
.definethumblabel DVDCheckLayer,0x13F080
.definethumblabel ReadBDData,0x13FC5C
.definethumblabel SetCDType,0x143740
.definethumblabel ReadDVDData,0x144EAA
.definethumblabel ReadDVDRAMData,0x147418
.definearmlabel   SetBDCharacteristics,0x1494E0
.definearmlabel   ReadDVDTOC,0x149570
.definearmlabel   ReadFromDVDSector,0x1499D8
.definearmlabel   CopySectorToDiscStructMem,0x149CB0

; Compressed Functions
.definedatalabel DVDCharacteristicsPatchAddr,0x01F9A8DA

; Inline patches
.definedatalabel ReadSpeedPatchAddr,0x01BB06
.definedatalabel ReadCommandTrueAddr,0x01BB14
.definedatalabel ReadCommandFalseAddr,0x01BC06
.definedatalabel CDDataSpeedPatchAddr,0x01F3D4
.definedatalabel SetBDCharacteristicsHookAddr,0x09E790
.definedatalabel ReadDVDTOCHookAddr,0x09F0EA
.definedatalabel ScrambleHookAddr,0x0A1B38
.definedatalabel BDIdentifierPatchAddr1,0x0E0DA4 // nop
.definedatalabel BDIdentifierPatchAddr2,0x0E0DAB // branch
.definedatalabel BDIdentifierPatchAddr3,0x0E0E8A // nop
.definedatalabel BDIdentifierPatchAddr4,0x0E0EC4 // nop
.definedatalabel CDLeadOutPatchAddr0,0x12EEEC
.definedataLabel DVDLeadOutPatchAddr0,0x13F0C7
.definedatalabel DVDLeadOutPatchAddr1,0x13F106
.definedatalabel DVDLeadOutPatchAddr2,0x13F11C
.definedatalabel DVDLeadOutPatchAddr3,0x13F12A
.definedatalabel DVDLeadOutPatchAddr4,0x13F13E
.definedatalabel DVDLeadOutPatchAddr5,0x13F150
.definedatalabel DVDLeadOutPatchAddr6,0x13F160
.definedatalabel BDLeadOutPatchAddr,0x13FB75
.definedatalabel BDScramblePatchAddr,0x13FCD4
.definedatalabel BDScrambleHookAddr,0x13FCD8
.definedatalabel BDEDCHookAddr,0x1408DA
.definedatalabel CDLeadOutPatchAddr1,0x142DF8
.definedatalabel CDLeadOutPatchAddr2,0x142E2C // nop
.definedatalabel CDLeadOutPatchAddr3,0x142E34 // nop
.definedatalabel CDLeadOutPatchAddr4,0x142E3C // nop
.definedatalabel CDLeadOutPatchAddr5,0x142ED6 // nop
.definedatalabel CDLeadOutPatchAddr6,0x142EF0 // nop

.definedataLabel DVDScramblePatchAddr,0x144FEE
.definedatalabel DVDScrambleHookAddr,0x144FF2
.definedatalabel DVDEDCHookAddr,0x14534A
.definedatalabel InquiryDataPatch,0x14FEB6

; offsets
TocOffsetValue equ 0x8D70

.include "main.asm"
.Close
