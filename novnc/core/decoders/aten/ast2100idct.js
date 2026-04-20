/*
 * AST2100 IDCT
 * (c) Copyright 2015-2017 Kevin Kelley <kelleyk@kelleyk.net>.
 * Ported to ES module format for modern noVNC.
 */

const FIX_1_082392200 = 277;
const FIX_1_414213562 = 362;
const FIX_1_847759065 = 473;
const FIX_2_613125930 = 669;

const CONST_BITS = 8;
const PASS1_BITS = 0;
const END_PASS1_DESCALE_BITS = CONST_BITS;

function rangeLimit(x) {
    x += 128;
    return Math.max(0, Math.min(255, x));
}

function descale(x, n) {
    return x >> n;
}

function fixedDequant(scaledQuantTable, buf, i) {
    return fixedMul(scaledQuantTable[i], buf[i]);
}

function fixedMul(a, b) {
    return descale(a * b, CONST_BITS);
}

function aanIdctCol(scaledQuantTable, buf, workspace, x) {
    const dequant = (idx) => fixedDequant(scaledQuantTable, buf, idx);
    const mul = fixedMul;

    let allAcZero = true;
    for (let y = 1; y < 8; ++y) {
        if (buf[8 * y + x] !== 0) {
            allAcZero = false;
            break;
        }
    }
    if (allAcZero) {
        const dcval = descale(dequant(8 * 0 + x), END_PASS1_DESCALE_BITS);
        for (let y = 0; y < 8; ++y) {
            workspace[8 * y + x] = dcval;
        }
        return;
    }

    // Even part
    let tmp0 = dequant(8 * 0 + x);
    let tmp1 = dequant(8 * 2 + x);
    let tmp2 = dequant(8 * 4 + x);
    let tmp3 = dequant(8 * 6 + x);

    let tmp10 = tmp0 + tmp2;
    let tmp11 = tmp0 - tmp2;
    let tmp13 = tmp1 + tmp3;
    let tmp12 = mul((tmp1 - tmp3), FIX_1_414213562) - tmp13;

    tmp0 = tmp10 + tmp13;
    tmp3 = tmp10 - tmp13;
    tmp1 = tmp11 + tmp12;
    tmp2 = tmp11 - tmp12;

    // Odd part
    let tmp4 = dequant(8 * 1 + x);
    let tmp5 = dequant(8 * 3 + x);
    let tmp6 = dequant(8 * 5 + x);
    let tmp7 = dequant(8 * 7 + x);

    let z13 = tmp6 + tmp5;
    let z10 = tmp6 - tmp5;
    let z11 = tmp4 + tmp7;
    let z12 = tmp4 - tmp7;

    tmp7 = z11 + z13;
    tmp11 = mul((z11 - z13), FIX_1_414213562);
    let z5 = mul((z10 + z12), FIX_1_847759065);
    tmp10 = mul(FIX_1_082392200, z12) - z5;
    tmp12 = mul(-FIX_2_613125930, z10) + z5;

    tmp6 = tmp12 - tmp7;
    tmp5 = tmp11 - tmp6;
    tmp4 = tmp10 + tmp5;

    workspace[x + 8 * 0] = descale(tmp0 + tmp7, END_PASS1_DESCALE_BITS);
    workspace[x + 8 * 7] = descale(tmp0 - tmp7, END_PASS1_DESCALE_BITS);
    workspace[x + 8 * 1] = descale(tmp1 + tmp6, END_PASS1_DESCALE_BITS);
    workspace[x + 8 * 6] = descale(tmp1 - tmp6, END_PASS1_DESCALE_BITS);
    workspace[x + 8 * 2] = descale(tmp2 + tmp5, END_PASS1_DESCALE_BITS);
    workspace[x + 8 * 5] = descale(tmp2 - tmp5, END_PASS1_DESCALE_BITS);
    workspace[x + 8 * 4] = descale(tmp3 + tmp4, END_PASS1_DESCALE_BITS);
    workspace[x + 8 * 3] = descale(tmp3 - tmp4, END_PASS1_DESCALE_BITS);
}

function aanIdctRow(scaledQuantTable, buf, workspace, y) {
    const wsptr = (x) => workspace[8 * y + x];
    const mul = fixedMul;

    // Even part
    let tmp10 = wsptr(0) + wsptr(4);
    let tmp11 = wsptr(0) - wsptr(4);
    let tmp13 = wsptr(2) + wsptr(6);
    let tmp12 = mul((wsptr(2) - wsptr(6)), FIX_1_414213562) - tmp13;

    let tmp0 = tmp10 + tmp13;
    let tmp3 = tmp10 - tmp13;
    let tmp1 = tmp11 + tmp12;
    let tmp2 = tmp11 - tmp12;

    // Odd part
    let z13 = wsptr(5) + wsptr(3);
    let z10 = wsptr(5) - wsptr(3);
    let z11 = wsptr(1) + wsptr(7);
    let z12 = wsptr(1) - wsptr(7);

    let tmp7 = z11 + z13;
    tmp11 = mul((z11 - z13), FIX_1_414213562);
    let z5 = mul((z10 + z12), FIX_1_847759065);
    tmp10 = mul(FIX_1_082392200, z12) - z5;
    tmp12 = mul(-FIX_2_613125930, z10) + z5;

    let tmp6 = tmp12 - tmp7;
    let tmp5 = tmp11 - tmp6;
    let tmp4 = tmp10 + tmp5;

    const setOut = (x, val) => {
        val = descale(val, PASS1_BITS + 3);
        val = rangeLimit(val);
        buf[y * 8 + x] = val;
    };

    setOut(0, tmp0 + tmp7);
    setOut(7, tmp0 - tmp7);
    setOut(1, tmp1 + tmp6);
    setOut(6, tmp0 - tmp6);
    setOut(2, tmp2 + tmp5);
    setOut(5, tmp2 - tmp5);
    setOut(4, tmp3 + tmp4);
    setOut(3, tmp3 - tmp4);
}

export function idctFixedAan(scaledQuantTable, buf, dstBuf) {
    const workspace = new Int32Array(64);

    for (let x = 0; x < 8; ++x) {
        aanIdctCol(scaledQuantTable, buf, workspace, x);
    }

    for (let y = 0; y < 8; ++y) {
        aanIdctRow(scaledQuantTable, dstBuf, workspace, y);
    }

    return dstBuf;
}
