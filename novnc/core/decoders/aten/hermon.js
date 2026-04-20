/*
 * ATEN HERMON Decoder for iKVM
 * Based on kelleyk's noVNC bmc-support branch.
 * Ported to ES module format for modern noVNC.
 *
 * Handles encoding 0x59 - raw RGB555 subrect-based updates.
 */

import * as Log from '../../util/logging.js';

export default class AtenHermonDecoder {
    constructor() {
        this._atenLen = -1;
        this._atenType = -1;
        this._subrectBuf = null;
    }

    decodeRect(x, y, width, height, sock, display, depth) {
        // Phase 1: Read the ATEN header (mysteryFlag + data length)
        if (this._atenLen === -1) {
            if (sock.rQwait("ATEN_HERMON header", 8)) { return false; }
            sock.rQshift32(); // mysteryFlag
            this._atenLen = sock.rQshift32();

            // Screen off detection: dimensions are -640, -480 as uint16
            if (width === 64896 && height === 65056) {
                Log.Info("ATEN iKVM screen is probably off");
                this._atenLen = 0;
                this._atenType = -1;
                return true;
            }

            // Handle resize
            if (display.width !== width || display.height !== height) {
                Log.Debug("ATEN_HERMON resize desktop to " + width + "x" + height);
                display.resize(width, height);
            }
        }

        if (this._atenLen === 0) {
            this._atenLen = -1;
            this._atenType = -1;
            return true;
        }

        // Phase 2: Read the sub-header
        if (this._atenType === -1) {
            if (sock.rQwait("ATEN_HERMON sub-header", 10)) { return false; }
            this._atenType = sock.rQshift8();
            sock.rQshift8(); // padding
            sock.rQshift32(); // number of subrects
            const len2 = sock.rQshift32();
            if (this._atenLen !== len2) {
                Log.Warn('ATEN_HERMON length mismatch');
            }
            this._atenLen -= 10;
        }

        // Phase 3: Read pixel data
        while (this._atenLen > 0) {
            if (this._atenType === 0) {
                // Subrect mode: 6 bytes header + 16x16 pixel block
                const blockBytes = 6 + (16 * 16 * 2); // 2 bytes per pixel (RGB555)
                if (sock.rQwait("ATEN_HERMON subrect", blockBytes)) { return false; }

                sock.rQshift16(); // a
                sock.rQshift16(); // b
                const sy = sock.rQshift8();
                const sx = sock.rQshift8();

                // Read RGB555 data and convert to RGBA
                const pixelData = sock.rQshiftBytes(16 * 16 * 2);
                const rgba = new Uint8Array(16 * 16 * 4);
                for (let i = 0; i < 16 * 16; i++) {
                    const pixel = pixelData[i * 2] | (pixelData[i * 2 + 1] << 8);
                    rgba[i * 4 + 0] = ((pixel >> 10) & 0x1F) * 255 / 31;
                    rgba[i * 4 + 1] = ((pixel >> 5) & 0x1F) * 255 / 31;
                    rgba[i * 4 + 2] = (pixel & 0x1F) * 255 / 31;
                    rgba[i * 4 + 3] = 255;
                }
                display.blitImage(sx * 16, sy * 16, 16, 16, rgba, 0);
                this._atenLen -= blockBytes;
            } else if (this._atenType === 1) {
                // RAW mode - read line by line
                const bytesPerLine = width * 2; // RGB555
                if (sock.rQwait("ATEN_HERMON raw line", bytesPerLine)) { return false; }

                const pixelData = sock.rQshiftBytes(bytesPerLine);
                const rgba = new Uint8Array(width * 4);
                for (let i = 0; i < width; i++) {
                    const pixel = pixelData[i * 2] | (pixelData[i * 2 + 1] << 8);
                    rgba[i * 4 + 0] = ((pixel >> 10) & 0x1F) * 255 / 31;
                    rgba[i * 4 + 1] = ((pixel >> 5) & 0x1F) * 255 / 31;
                    rgba[i * 4 + 2] = (pixel & 0x1F) * 255 / 31;
                    rgba[i * 4 + 3] = 255;
                }
                const curY = y + (height - Math.ceil(this._atenLen / bytesPerLine));
                display.blitImage(x, curY, width, 1, rgba, 0);
                this._atenLen -= bytesPerLine;
            } else {
                Log.Error('Unknown ATEN_HERMON type: ' + this._atenType);
                return false;
            }
        }

        this._atenLen = -1;
        this._atenType = -1;
        return true;
    }
}
