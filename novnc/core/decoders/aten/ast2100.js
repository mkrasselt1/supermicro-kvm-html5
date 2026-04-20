/*
 * AST2100 Decoder for ATEN iKVM
 * (c) Copyright 2015-2017 Kevin Kelley <kelleyk@kelleyk.net>.
 * Ported to ES module format for modern noVNC.
 */

import * as Log from '../../util/logging.js';
import { idctFixedAan } from './ast2100idct.js';
import { BitStream, JpegHuffmanTable, inRangeIncl, clamp } from './ast2100util.js';
import {
    ZIGZAG_ORDER, DCTSIZE, DCTSIZE2,
    ATEN_QT_LUMA, ATEN_QT_CHROMA,
    TABLE_CLASS_AC, TABLE_CLASS_DC,
    BITS_AC_LUMA, BITS_AC_CHROMA,
    BITS_DC_LUMA, BITS_DC_CHROMA,
    HUFFVAL_AC_LUMA, HUFFVAL_AC_CHROMA,
    HUFFVAL_DC_LUMA, HUFFVAL_DC_CHROMA,
    AAN_IDCT_SCALING_FACTORS,
    YUVTORGB_Y_TABLE, YUVTORGB_CR_R_TABLE,
    YUVTORGB_CR_G_TABLE, YUVTORGB_CB_G_TABLE,
    YUVTORGB_CB_B_TABLE
} from './ast2100const.js';

export default class Ast2100Decoder {
    constructor(width, height, blitCallback) {
        this._blitCallback = blitCallback;
        this._frameWidth = width;
        this._frameHeight = height;

        this.subsamplingMode = -1;

        this.quantTables = [new Int32Array(64), new Int32Array(64)];
        this._loadedQuantTables = [-1, -1];

        this.huffTables = [
            [new JpegHuffmanTable(BITS_DC_LUMA, HUFFVAL_DC_LUMA),
             new JpegHuffmanTable(BITS_DC_CHROMA, HUFFVAL_DC_CHROMA)],
            [new JpegHuffmanTable(BITS_AC_LUMA, HUFFVAL_AC_LUMA),
             new JpegHuffmanTable(BITS_AC_CHROMA, HUFFVAL_AC_CHROMA)],
        ];

        this._scanComponents = [
            { huffTableSelectorDC: 0, huffTableSelectorAC: 0 },  // Y
            { huffTableSelectorDC: 1, huffTableSelectorAC: 1 },  // Cb
            { huffTableSelectorDC: 1, huffTableSelectorAC: 1 }   // Cr
        ];
        this._scanPrevDc = [0, 0, 0];

        this._mcuPosX = 0;
        this._mcuPosY = 0;

        this._initializeVq();

        this._tmpBufY = [
            new Int16Array(DCTSIZE2),
            new Int16Array(DCTSIZE2),
            new Int16Array(DCTSIZE2),
            new Int16Array(DCTSIZE2)
        ];
        this._tmpBufCb = new Int16Array(DCTSIZE2);
        this._tmpBufCr = new Int16Array(DCTSIZE2);

        this._componentBufY = [
            new Uint8Array(DCTSIZE2),
            new Uint8Array(DCTSIZE2),
            new Uint8Array(DCTSIZE2),
            new Uint8Array(DCTSIZE2)
        ];
        this._componentBufCb = new Uint8Array(DCTSIZE2);
        this._componentBufCr = new Uint8Array(DCTSIZE2);

        this._outputBuf = new Uint8Array(DCTSIZE2 * 4);
    }

    setSize(width, height) {
        this._frameWidth = width;
        this._frameHeight = height;
    }

    _initializeVq() {
        this._vqCodewordLookup = [0, 1, 2, 3];
        this._vqCodebook = [
            [0x00, 0x80, 0x80],
            [0xFF, 0x80, 0x80],
            [0x80, 0x80, 0x80],
            [0xC0, 0x80, 0x80]
        ];
    }

    _getMcuSize() {
        return { 444: 8, 422: 16 }[this.subsamplingMode];
    }

    _loadQuantTable(slot, srcTable) {
        for (let y = 0; y < 8; ++y) {
            for (let x = 0; x < 8; ++x) {
                this.quantTables[slot][y * 8 + x] =
                    ~~(srcTable[y * 8 + x] * AAN_IDCT_SCALING_FACTORS[x] * AAN_IDCT_SCALING_FACTORS[y] * 65536.0);
            }
        }
    }

    _advancePosition() {
        const mcuSize = this._getMcuSize();
        let widthInMcus = ~~(this._frameWidth / mcuSize);
        if (this._frameWidth % mcuSize !== 0) widthInMcus += 1;
        let heightInMcus = ~~(this._frameHeight / mcuSize);
        if (this._frameHeight % mcuSize !== 0) heightInMcus += 1;

        this._mcuPosX += 1;
        if (this._mcuPosX >= widthInMcus) {
            this._mcuPosX = 0;
            this._mcuPosY += 1;
        }
        if (this._mcuPosY >= heightInMcus) {
            this._mcuPosY = 0;
        }
    }

    decode(data) {
        this._scanPrevDc = [0, 0, 0];
        this._mcuPosX = 0;
        this._mcuPosY = 0;

        const quantTableSelectorLuma = data[0];
        const quantTableSelectorChroma = data[1];
        const subsamplingMode = (data[2] << 8) | data[3];

        if (this.subsamplingMode !== subsamplingMode) {
            this.subsamplingMode = subsamplingMode;
        }

        if (quantTableSelectorLuma !== this._loadedQuantTables[0]) {
            if (!inRangeIncl(quantTableSelectorLuma, 0, 0xB)) {
                throw new Error('Out-of-range selector for luma quant table: ' + quantTableSelectorLuma);
            }
            this._loadQuantTable(0, ATEN_QT_LUMA[quantTableSelectorLuma]);
            this._loadedQuantTables[0] = quantTableSelectorLuma;
        }
        if (quantTableSelectorChroma !== this._loadedQuantTables[1]) {
            if (!inRangeIncl(quantTableSelectorChroma, 0, 0xB)) {
                throw new Error('Out-of-range selector for chroma quant table: ' + quantTableSelectorChroma);
            }
            this._loadQuantTable(1, ATEN_QT_CHROMA[quantTableSelectorChroma]);
            this._loadedQuantTables[1] = quantTableSelectorChroma;
        }

        if (this.subsamplingMode !== 422 && this.subsamplingMode !== 444) {
            throw new Error('Unexpected value for subsamplingMode: 0x' + this.subsamplingMode.toString(16));
        }

        this._stream = new BitStream(data);
        this._stream.skip(16);
        this._stream.skip(16);

        while (true) {
            const controlFlag = this._stream.read(4);

            if (controlFlag === 0 || controlFlag === 4 || controlFlag === 8 || controlFlag === 0xC) {
                if (controlFlag === 8 || controlFlag === 0xC) {
                    this._mcuPosX = this._stream.read(8);
                    this._mcuPosY = this._stream.read(8);
                }
                if (controlFlag === 4 || controlFlag === 0xC) {
                    throw new Error('Unexpected control flag: alternate quant table');
                }
                this._parseMcu();
            } else if (inRangeIncl(controlFlag, 5, 7) || inRangeIncl(controlFlag, 0xD, 0xF)) {
                if (controlFlag >= 0xD) {
                    this._mcuPosX = this._stream.read(8);
                    this._mcuPosY = this._stream.read(8);
                }
                const codewordSize = (controlFlag & 7) - 5;
                this._parseVqBlock(codewordSize);
            } else if (controlFlag === 9) {
                break;
            } else {
                throw new Error('Unexpected control flag: 0x' + controlFlag.toString(16));
            }
        }
    }

    _parseVqBlock(codewordSize) {
        const mcuSize = this._getMcuSize();
        if (mcuSize !== 8) throw new Error('Unexpected MCU size for VQ block!');

        const yBuf = this._componentBufY[0];
        const cbBuf = this._componentBufCb;
        const crBuf = this._componentBufCr;

        const setColor = (j, codeword) => {
            const color = this._vqCodebook[this._vqCodewordLookup[codeword]];
            yBuf[j] = color[0];
            cbBuf[j] = color[1];
            crBuf[j] = color[2];
        };

        for (let i = 0; i < (1 << codewordSize); ++i) {
            const hasNewColor = this._stream.read(1);
            const codebookSlotIdx = this._stream.read(2);
            if (hasNewColor) {
                const color = [this._stream.read(8), this._stream.read(8), this._stream.read(8)];
                this._vqCodebook[codebookSlotIdx] = color;
            }
            this._vqCodewordLookup[i] = codebookSlotIdx;
        }

        if (codewordSize === 0) {
            for (let i = 0; i < 64; ++i) setColor(i, 0);
        } else {
            for (let i = 0; i < 64; ++i) setColor(i, this._stream.read(codewordSize));
        }

        for (let j = 0; j < 64; ++j) {
            this._ycbcrToRgb(this._outputBuf, j, yBuf[j], cbBuf[j], crBuf[j]);
        }

        this._blitCallback(8 * this._mcuPosX, 8 * this._mcuPosY, 8, 8, this._outputBuf);
        this._advancePosition();
    }

    _parseMcu() {
        const qtLuma = this.quantTables[0];
        const qtChroma = this.quantTables[1];

        this._parseDataUnit(0, this._tmpBufY[0]);
        idctFixedAan(qtLuma, this._tmpBufY[0], this._componentBufY[0]);

        if (this.subsamplingMode !== 444) {
            this._parseDataUnit(0, this._tmpBufY[1]);
            idctFixedAan(qtLuma, this._tmpBufY[1], this._componentBufY[1]);
            this._parseDataUnit(0, this._tmpBufY[2]);
            idctFixedAan(qtLuma, this._tmpBufY[2], this._componentBufY[2]);
            this._parseDataUnit(0, this._tmpBufY[3]);
            idctFixedAan(qtLuma, this._tmpBufY[3], this._componentBufY[3]);
        }
        this._parseDataUnit(1, this._tmpBufCb);
        idctFixedAan(qtChroma, this._tmpBufCb, this._componentBufCb);
        this._parseDataUnit(2, this._tmpBufCr);
        idctFixedAan(qtChroma, this._tmpBufCr, this._componentBufCr);

        if (this.subsamplingMode !== 444) {
            for (let dy = 0; dy < 2; ++dy) {
                for (let dx = 0; dx < 2; ++dx) {
                    const componentBufY = this._componentBufY[dx * 2 + dy];
                    for (let y = 0; y < 8; ++y) {
                        for (let x = 0; x < 8; ++x) {
                            const hy = ~~((8 * dx + y) / 2);
                            const hx = ~~((8 * dy + x) / 2);
                            this._ycbcrToRgb(this._outputBuf, y * 8 + x,
                                componentBufY[y * 8 + x],
                                this._componentBufCb[hy * 8 + hx],
                                this._componentBufCr[hy * 8 + hx]);
                        }
                    }
                    this._blitCallback(16 * this._mcuPosX + 8 * dy, 16 * this._mcuPosY + 8 * dx, 8, 8, this._outputBuf);
                }
            }
        } else {
            for (let j = 0; j < 64; ++j) {
                this._ycbcrToRgb(this._outputBuf, j,
                    this._componentBufY[0][j],
                    this._componentBufCb[j],
                    this._componentBufCr[j]);
            }
            this._blitCallback(8 * this._mcuPosX, 8 * this._mcuPosY, 8, 8, this._outputBuf);
        }

        this._advancePosition();
    }

    _parseDataUnit(componentIdx, buf) {
        const scanComponent = this._scanComponents[componentIdx];
        const dcHufftable = this.huffTables[TABLE_CLASS_DC][scanComponent.huffTableSelectorDC];
        const acHufftable = this.huffTables[TABLE_CLASS_AC][scanComponent.huffTableSelectorAC];

        const setValue = (i, val) => {
            buf[ZIGZAG_ORDER[i]] = val;
        };

        const dcDelta = this._readEncodedValueDC(dcHufftable);
        this._scanPrevDc[componentIdx] += dcDelta;
        buf[0] = this._scanPrevDc[componentIdx];

        for (let i = 1; i < 64;) {
            const x = acHufftable.readCode(this._stream);
            const runlen = x >>> 4;
            const size = x & 0x0F;

            if (size === 0) {
                if (runlen === 0) {
                    while (i < 64) {
                        setValue(i, 0);
                        ++i;
                    }
                    break;
                } else if (runlen === 0xF) {
                    for (let j = 0; j < 16; ++j) setValue(i + j, 0);
                    i += 16;
                    continue;
                }
            }

            for (let j = 0; j < runlen; ++j) setValue(i + j, 0);
            i += runlen;
            setValue(i, this._readEncodedValueAC(size));
            i += 1;
        }

        return buf;
    }

    _readEncodedValueDC(huffTable) {
        const category = huffTable.readCode(this._stream);
        return this._readEncodedValueAC(category);
    }

    _readEncodedValueAC(category) {
        if (category === 0) return 0;

        let value;
        const valSign = this._stream.read(1);

        if (valSign === 0) {
            value = -(1 << category) + 1;
        } else {
            value = (1 << category - 1);
        }

        if (category > 1) {
            const moreBits = this._stream.read(category - 1);
            value += moreBits;
        }

        return value;
    }

    _ycbcrToRgb(outputBuf, outputOffset, y, cb, cr) {
        outputOffset *= 4;
        outputBuf[outputOffset + 0] = clamp(YUVTORGB_Y_TABLE[y] + YUVTORGB_CR_R_TABLE[cr]);
        outputBuf[outputOffset + 1] = clamp(YUVTORGB_Y_TABLE[y] + YUVTORGB_CR_G_TABLE[cr] + YUVTORGB_CB_G_TABLE[cb]);
        outputBuf[outputOffset + 2] = clamp(YUVTORGB_Y_TABLE[y] + YUVTORGB_CB_B_TABLE[cb]);
        outputBuf[outputOffset + 3] = 0xFF;
    }
}
