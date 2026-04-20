/*
 * noVNC: HTML5 VNC client
 * Copyright (C) 2019 The noVNC authors
 * Licensed under MPL 2.0 (see LICENSE.txt)
 *
 * See README.md for usage and integration instructions.
 */

export const encodings = {
    encodingRaw: 0,
    encodingCopyRect: 1,
    encodingRRE: 2,
    encodingHextile: 5,
    encodingZlib: 6,
    encodingTight: 7,
    encodingZRLE: 16,
    encodingTightPNG: -260,
    encodingJPEG: 21,
    encodingH264: 50,

    // ATEN iKVM encodings
    encodingAtenAST2100: 0x57,
    encodingAtenASTJPEG: 0x58,
    encodingAtenHermon: 0x59,
    encodingAtenYarkon: 0x60,
    encodingAtenPilot3: 0x61,

    pseudoEncodingQualityLevel9: -23,
    pseudoEncodingQualityLevel0: -32,
    pseudoEncodingDesktopSize: -223,
    pseudoEncodingLastRect: -224,
    pseudoEncodingCursor: -239,
    pseudoEncodingQEMUExtendedKeyEvent: -258,
    pseudoEncodingQEMULedEvent: -261,
    pseudoEncodingDesktopName: -307,
    pseudoEncodingExtendedDesktopSize: -308,
    pseudoEncodingXvp: -309,
    pseudoEncodingFence: -312,
    pseudoEncodingContinuousUpdates: -313,
    pseudoEncodingExtendedMouseButtons: -316,
    pseudoEncodingCompressLevel9: -247,
    pseudoEncodingCompressLevel0: -256,
    pseudoEncodingVMwareCursor: 0x574d5664,
    pseudoEncodingExtendedClipboard: 0xc0a1e5ce
};

export function encodingName(num) {
    switch (num) {
        case encodings.encodingRaw:      return "Raw";
        case encodings.encodingCopyRect: return "CopyRect";
        case encodings.encodingRRE:      return "RRE";
        case encodings.encodingHextile:  return "Hextile";
        case encodings.encodingZlib:     return "Zlib";
        case encodings.encodingTight:    return "Tight";
        case encodings.encodingZRLE:     return "ZRLE";
        case encodings.encodingTightPNG: return "TightPNG";
        case encodings.encodingJPEG:     return "JPEG";
        case encodings.encodingH264:     return "H.264";
        case encodings.encodingAtenAST2100: return "ATEN AST2100";
        case encodings.encodingAtenASTJPEG: return "ATEN ASTJPEG";
        case encodings.encodingAtenHermon:  return "ATEN Hermon";
        case encodings.encodingAtenYarkon:  return "ATEN Yarkon";
        case encodings.encodingAtenPilot3:  return "ATEN Pilot3";
        default:                         return "[unknown encoding " + num + "]";
    }
}
