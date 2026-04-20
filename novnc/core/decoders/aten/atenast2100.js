/*
 * ATEN AST2100 Decoder wrapper for modern noVNC
 * Wraps the AST2100 DCT decoder to match noVNC's decoder interface.
 */

import * as Log from '../../util/logging.js';
import Ast2100Decoder from './ast2100.js';

export default class AtenAst2100DecoderWrapper {
    constructor() {
        this._atenLen = -1;
        this._decoder = null;
    }

    decodeRect(x, y, width, height, sock, display, depth) {
        // Phase 1: Read ATEN header
        if (this._atenLen === -1) {
            if (sock.rQwait("ATEN_AST2100 header", 8)) { return false; }
            sock.rQshift32(); // mysteryFlag
            this._atenLen = sock.rQshift32();
        }

        // Screen off: -640x-480 as uint16
        if (width === 64896 && height === 65056) {
            Log.Debug('ATEN AST2100: screen is off.');
            if (this._atenLen > 0) {
                if (sock.rQwait("ATEN_AST2100 skip", this._atenLen)) { return false; }
                sock.rQshiftBytes(this._atenLen);
            }
            this._atenLen = -1;
            return true;
        }

        if (this._atenLen === 0) {
            Log.Warn('ATEN AST2100: data length is zero but screen not off.');
            this._atenLen = -1;
            return true;
        }

        // Read all the frame data
        if (sock.rQwait("ATEN_AST2100 data", this._atenLen)) { return false; }
        const data = sock.rQshiftBytes(this._atenLen);
        this._atenLen = -1;

        // Initialize or resize decoder
        if (!this._decoder) {
            this._decoder = new Ast2100Decoder(width, height, (bx, by, bw, bh, buf) => {
                display.blitImage(bx, by, bw, bh, buf, 0);
            });
        }

        // Handle resize
        if (display.width !== width || display.height !== height) {
            Log.Debug("ATEN_AST2100 resize desktop to " + width + "x" + height);
            display.resize(width, height);
            this._decoder.setSize(width, height);
        }

        try {
            this._decoder.decode(data);
        } catch (e) {
            Log.Error("ATEN AST2100 decode error: " + e);
        }

        return true;
    }
}
