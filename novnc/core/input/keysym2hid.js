/*
 * X11 Keysym to USB HID Usage Code mapping
 * Used by ATEN iKVM for keyboard input.
 * Based on kelleyk's noVNC bmc-support branch.
 */

import KeyTable from "./keysym.js";

const XK2HID = {};

// F1-F12
for (let i = KeyTable.XK_F1; i <= KeyTable.XK_F12; i++) {
    XK2HID[i] = 0x3a + (i - KeyTable.XK_F1);
}

// A-Z and a-z
for (let i = KeyTable.XK_A; i <= KeyTable.XK_Z; i++) {
    XK2HID[i] = 0x04 + (i - KeyTable.XK_A);
    XK2HID[i + (KeyTable.XK_a - KeyTable.XK_A)] = 0x04 + (i - KeyTable.XK_A);
}

// 1-9
for (let i = KeyTable.XK_1; i <= KeyTable.XK_9; i++) {
    XK2HID[i] = 0x1e + (i - KeyTable.XK_1);
}

XK2HID[KeyTable.XK_0]            = 0x27;
XK2HID[KeyTable.XK_Return]       = 0x28;
XK2HID[KeyTable.XK_Escape]       = 0x29;
XK2HID[KeyTable.XK_BackSpace]    = 0x2a;
XK2HID[KeyTable.XK_Tab]          = 0x2b;
XK2HID[KeyTable.XK_space]        = 0x2c;
XK2HID[KeyTable.XK_minus]        = 0x2d;
XK2HID[KeyTable.XK_equal]        = 0x2e;
XK2HID[KeyTable.XK_bracketleft]  = 0x2f;
XK2HID[KeyTable.XK_bracketright] = 0x30;
XK2HID[KeyTable.XK_backslash]    = 0x31;
XK2HID[KeyTable.XK_semicolon]    = 0x33;
XK2HID[KeyTable.XK_apostrophe]   = 0x34;
XK2HID[KeyTable.XK_grave]        = 0x35;
XK2HID[KeyTable.XK_comma]        = 0x36;
XK2HID[KeyTable.XK_period]       = 0x37;
XK2HID[KeyTable.XK_slash]        = 0x38;

XK2HID[KeyTable.XK_Print]        = 0x46;
XK2HID[KeyTable.XK_Scroll_Lock]  = 0x47;
XK2HID[KeyTable.XK_Pause]        = 0x48;
XK2HID[KeyTable.XK_Insert]       = 0x49;
XK2HID[KeyTable.XK_Home]         = 0x4a;
XK2HID[KeyTable.XK_Page_Up]      = 0x4b;
XK2HID[KeyTable.XK_Delete]       = 0x4c;
XK2HID[KeyTable.XK_End]          = 0x4d;
XK2HID[KeyTable.XK_Page_Down]    = 0x4e;
XK2HID[KeyTable.XK_Right]        = 0x4f;
XK2HID[KeyTable.XK_Left]         = 0x50;
XK2HID[KeyTable.XK_Down]         = 0x51;
XK2HID[KeyTable.XK_Up]           = 0x52;

XK2HID[KeyTable.XK_Control_L]    = 0xe0;
XK2HID[KeyTable.XK_Control_R]    = XK2HID[KeyTable.XK_Control_L];
XK2HID[KeyTable.XK_Shift_L]      = 0xe1;
XK2HID[KeyTable.XK_Shift_R]      = XK2HID[KeyTable.XK_Shift_L];
XK2HID[KeyTable.XK_Alt_L]        = 0xe2;
XK2HID[KeyTable.XK_Alt_R]        = XK2HID[KeyTable.XK_Alt_L];
XK2HID[KeyTable.XK_Super_L]      = 0xe3;
XK2HID[KeyTable.XK_Super_R]      = XK2HID[KeyTable.XK_Super_L];

XK2HID[KeyTable.XK_Caps_Lock]    = 0x39;
XK2HID[KeyTable.XK_Num_Lock]     = 0x53;

// Shifted character mappings (map to the same HID code as unshifted)
XK2HID[KeyTable.XK_less]         = XK2HID[KeyTable.XK_comma];
XK2HID[KeyTable.XK_greater]      = XK2HID[KeyTable.XK_period];
XK2HID[KeyTable.XK_exclam]       = XK2HID[KeyTable.XK_1];
XK2HID[KeyTable.XK_at]           = XK2HID[KeyTable.XK_2];
XK2HID[KeyTable.XK_numbersign]   = XK2HID[KeyTable.XK_3];
XK2HID[KeyTable.XK_dollar]       = XK2HID[KeyTable.XK_4];
XK2HID[KeyTable.XK_percent]      = XK2HID[KeyTable.XK_5];
XK2HID[KeyTable.XK_asciicircum]  = XK2HID[KeyTable.XK_6];
XK2HID[KeyTable.XK_ampersand]    = XK2HID[KeyTable.XK_7];
XK2HID[KeyTable.XK_asterisk]     = XK2HID[KeyTable.XK_8];
XK2HID[KeyTable.XK_parenleft]    = XK2HID[KeyTable.XK_9];
XK2HID[KeyTable.XK_parenright]   = XK2HID[KeyTable.XK_0];
XK2HID[KeyTable.XK_underscore]   = XK2HID[KeyTable.XK_minus];
XK2HID[KeyTable.XK_plus]         = XK2HID[KeyTable.XK_equal];
XK2HID[KeyTable.XK_braceleft]    = XK2HID[KeyTable.XK_bracketleft];
XK2HID[KeyTable.XK_braceright]   = XK2HID[KeyTable.XK_bracketright];
XK2HID[KeyTable.XK_bar]          = XK2HID[KeyTable.XK_backslash];
XK2HID[KeyTable.XK_colon]        = XK2HID[KeyTable.XK_semicolon];
XK2HID[KeyTable.XK_quotedbl]     = XK2HID[KeyTable.XK_apostrophe];
XK2HID[KeyTable.XK_asciitilde]   = XK2HID[KeyTable.XK_grave];
XK2HID[KeyTable.XK_question]     = XK2HID[KeyTable.XK_slash];

export default XK2HID;
