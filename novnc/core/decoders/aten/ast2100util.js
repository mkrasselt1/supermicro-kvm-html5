/*
 * AST2100 Utilities
 * (c) Copyright 2015-2017 Kevin Kelley <kelleyk@kelleyk.net>.
 * Ported to ES module format for modern noVNC.
 */

export function inRangeIncl(x, a, b) {
    return (x >= a && x <= b);
}

export function clamp(x) {
    x = ~~x;
    if (x <= 0) return 0;
    if (x >= 255) return 255;
    return x;
}

function swap32(val) {
    return ((val & 0xFF) << 24)
        | ((val & 0xFF00) << 8)
        | ((val >> 8) & 0xFF00)
        | ((val >> 24) & 0xFF);
}

export function arrayEq(a, b) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; ++i) {
        if (a[i] !== b[i]) return false;
    }
    return true;
}

export class BitStream {
    constructor(data) {
        this._data = data;
        this._nextDword = 0;
        this._reservoir = 0;
        this._bitsInReservoir = 0;
        this._refill();
        this._readbuf = this._reservoir;
        this._reservoir = 0;
        this._bitsInReservoir = 0;
        this._refill();
    }

    getPos() {
        return (this._nextDword * 32) - (32 + this._bitsInReservoir);
    }

    skip(bits) {
        while (bits > 0) {
            const n = Math.min(31, bits);
            this.read(n);
            bits -= n;
        }
    }

    read(bits) {
        if (bits >= 32) throw new Error('Number of bits must be less than 32.');

        const retval = this._readbuf >>> (32 - bits);

        if (bits > this._bitsInReservoir) {
            bits -= this._bitsInReservoir;
            this._moveFromReservoir(this._bitsInReservoir);
            this._refill();
        }
        this._moveFromReservoir(bits);

        return retval;
    }

    peek(bits) {
        if (bits >= 32) throw new Error('Number of bits must be less than 32.');
        return this._readbuf >>> (32 - bits);
    }

    _moveFromReservoir(n) {
        this._readbuf = (this._readbuf << n) | (this._reservoir >>> (32 - n));
        this._reservoir <<= n;
        this._bitsInReservoir -= n;
    }

    _refill() {
        this._reservoir = 0;
        this._bitsInReservoir = 0;
        for (let i = 0; i < 4; ++i) {
            const x = this._data[(4 * this._nextDword) + i];
            if (x === undefined) throw new Error('BitStream overran available data!');
            this._reservoir = (this._reservoir << 8) | x;
            this._bitsInReservoir += 8;
        }
        this._nextDword += 1;
        this._reservoir = swap32(this._reservoir);
    }
}

export class JpegHuffmanTable {
    constructor(bits, huffval) {
        this._huffsize = new Uint8Array(1 << 16);
        this._huffvalLookup = new Uint8Array(1 << 16);
        this._bits = new Uint8Array(17);
        this._buildTables(bits, huffval);
    }

    _buildTables(bits, huffval) {
        for (let i = 0; i < 17; ++i) {
            this._bits[i] = bits[i];
        }

        let nextCodeword = 0;
        let codewordIdx = 0;
        for (let codeLen = 1; codeLen < 17; ++codeLen) {
            for (let i = 0; i < this._bits[codeLen]; ++i) {
                for (let j = 0; j < (1 << (16 - codeLen)); ++j) {
                    this._huffsize[nextCodeword + j] = codeLen;
                    this._huffvalLookup[nextCodeword + j] = huffval[codewordIdx];
                }
                nextCodeword += (1 << (16 - codeLen));
                codewordIdx += 1;
            }
        }
    }

    readCode(stream) {
        const fixedlenCode = stream.peek(16);
        const codeLen = this._huffsize[fixedlenCode];
        stream.skip(codeLen);
        return this._huffvalLookup[fixedlenCode];
    }
}
