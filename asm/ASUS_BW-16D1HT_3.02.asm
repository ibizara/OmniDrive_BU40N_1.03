.arm.little
.thumb

.Open "../firmware/ASUS_BW-16D1HT_3.02.bin","../patched_firmware/ASUS_BW-16D1HT_3.02_OmniDrive.bin",0

; Free Space
.definedatalabel FreeSpaceStart,0x1B5000
.definedatalabel FreeSpaceEnd,0x1B7FFF

; Command Table
.definedatalabel CommandTableDVDRead1,0x152E14
.definedatalabel CommandTableDVDRead2,0x152E1C
.definedatalabel CommandTableDVDRead3,0x152E24
.definedataLabel CommandTableBDRead1,0x15308C
.definedataLabel CommandTableBDRead2,0x153094
.definedatalabel CommandTableEnd,0x152CE8 // 00 03 00 00 00 00 00 00

; Data
.definedatalabel mediaType,0x01FF9DFE
.definedatalabel BDDIOffset,0x01FFA37C
.definedatalabel forceUnitAccess,0x01FFBDCD
.definedatalabel seekLayer,0x01FFBDDD
.definedatalabel isDiscPTP,0x01FFBDE3
.definedatalabel memoryStart,0x02000C34 // 0x01C08000
.definedatalabel discStructOffset,0x02000C38 // 0x321C80
.definedatalabel lastSector,0x02000C5C
.definedatalabel startAddress,0x02000C60
.definedatalabel transferLength,0x02000C6C
.definedatalabel readTimeCounter,0x02000C80
.definedatalabel cdb,0x02000CFC
.definedatalabel layer0End,0x02000D6C
.definedatalabel layer1End,0x02000D70
.definedatalabel layer2End,0x02000D74

; Functions
.definethumblabel ChangeDiscRWMode,0x0415FC // 10 B5 04 00 00 20
.definethumblabel SetErrorMode,0x04230C
.definethumblabel ReadDiscStructMemDWORD,0x0A1D14 // 09 68 12 68 89 18 (-2)
.definethumblabel ReturnSense,0x0A296A // 00 04 00 23 18 43 09 02 08 43 ?? 49 10 43 08 60 70 47
.definethumblabel BDReadCmd,0x0AE574
.definethumblabel ReadDiscData,0x0BF244 // F3 B5 C8 20
.definethumblabel ReadCDDA,0x0C89DA // F0 B5 ?? 48 8D B0 
.definethumblabel MSFtoLBA,0x0C995E
.definethumblabel DVDReadCmd,0x11A61C
.definethumblabel DVDCheckLayer,0x1418A8 //  64 1E 00 22 (-0xE)
.definethumblabel ReadBDData,0x14241C
.definethumblabel SetCDType,0x14637C // 03 00 07 22 17 48 12 02 00 B5 
.definethumblabel ReadDVDData,0x147AF6 // 00 0A ? ? ? 04 C9 18 (-0x38)
.definethumblabel ReadDVDRAMData,0x149EB8 // F1 B5 00 26 00 22
.definearmlabel   SetBDCharacteristics,0x14BF2C
.definearmlabel   ReadDVDTOC,0x14BFBC // branch at FF 21 01 70 28 78 (-8)
.definearmlabel   ReadFromDVDSector,0x14C414 // branch at 00 90 00 20 6B 46 D8 70 18 78 (-4)
.definearmlabel   CopySectorToDiscStructMem,0x14C714 // branch ? ? ? 04 88 42 E8 D1 (+whatever)

; Compressed Functions
.definedatalabel DVDCharacteristicsPatchAddr,0x01F96DAA

; Inline patches
.definedatalabel ReadSpeedPatchAddr,0x018AE6
.definedatalabel ReadCommandTrueAddr,0x018AF4
.definedatalabel ReadCommandFalseAddr,0x018BE6
.definedatalabel CDDataSpeedPatchAddr,0x01C328
.definedatalabel SetBDCharacteristicsHookAddr,0x09E6BC
.definedatalabel ReadDVDTOCHookAddr,0x09F000
.definedatalabel ScrambleHookAddr,0x0A1990
.definedatalabel BDIdentifierPatchAddr1,0x0E0442 // nop
.definedatalabel BDIdentifierPatchAddr2,0x0E0449 // branch
.definedatalabel BDIdentifierPatchAddr3,0x0E0522 // nop
.definedatalabel BDIdentifierPatchAddr4,0x0E055E // nop
.definedatalabel CDLeadOutPatchAddr0,0x131D28
.definedataLabel DVDLeadOutPatchAddr0,0x1418EF
.definedatalabel DVDLeadOutPatchAddr1,0x14192E
.definedatalabel DVDLeadOutPatchAddr2,0x141944
.definedatalabel DVDLeadOutPatchAddr3,0x141952
.definedatalabel DVDLeadOutPatchAddr4,0x141966
.definedatalabel DVDLeadOutPatchAddr5,0x141978
.definedatalabel DVDLeadOutPatchAddr6,0x141988
.definedatalabel BDLeadOutPatchAddr,0x142335
.definedatalabel BDScramblePatchAddr,0x142494
.definedatalabel BDScrambleHookAddr,0x142498
.definedatalabel BDEDCHookAddr,0x142F6A
.definedatalabel CDLeadOutPatchAddr1,0x145A34
.definedatalabel CDLeadOutPatchAddr2,0x145A68 // nop
.definedatalabel CDLeadOutPatchAddr3,0x145A70 // nop
.definedatalabel CDLeadOutPatchAddr4,0x145A78 // nop
.definedatalabel CDLeadOutPatchAddr5,0x145B12 // nop
.definedatalabel CDLeadOutPatchAddr6,0x145B2C // nop
.definedataLabel DVDScramblePatchAddr,0x147C38
.definedatalabel DVDScrambleHookAddr,0x147C3C
.definedatalabel DVDEDCHookAddr,0x147F8C
.definedatalabel InquiryDataPatch,0x152686

; offsets
TocOffsetValue equ 0x8D80

.include "main.asm"
.Close