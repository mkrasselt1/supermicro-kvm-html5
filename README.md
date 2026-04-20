# supermicro-kvm-html5

HTML5 replacement for Supermicro's legacy Java-based iKVM remote console. Full remote KVM (keyboard/video/mouse) in your browser — no Java required.

![License](https://img.shields.io/badge/license-MPL--2.0-blue)

## Features

- **Remote Console** — Full graphical KVM via HTML5/WebSocket (based on [noVNC](https://github.com/novnc/noVNC) with ATEN protocol patches)
- **Power Control** — Power on/off, reset, cycle, ACPI shutdown
- **Virtual Media** — Mount ISO images as virtual CD-ROM (from local file or SMB share)
- **Ctrl+Alt+Del** — Send special key combinations
- **No Java** — Runs entirely in your browser

## Tested Hardware

> **This has been developed and tested on a single board. It may or may not work on yours.**

| Board | BMC Chip | Firmware | Status |
|-------|----------|----------|--------|
| **Supermicro X9SCL/X9SCM** | Nuvoton WPCM450 | ATEN 2016-11-10 (v0352) | Working |

The ATEN iKVM protocol is used across many Supermicro generations (X7 through X11), so there's a reasonable chance this works on similar boards. However, the protocol has quirks that vary between firmware versions.

**If you try this on a different board, please open an issue and report whether it works!** Include your board model, BMC chip, and firmware version. Even failure reports help — they tell us what to fix.

## Quick Start

### Requirements

- Python 3.10+
- `websockets` Python package
- A Supermicro board with IPMI/iKVM (ATEN-based BMC)

### Install & Run

```bash
git clone https://github.com/youruser/supermicro-kvm-html5.git
cd supermicro-kvm-html5
pip install websockets

python server.py --bmc-host 192.0.2.11 --username ADMIN --password YOUR_PASSWORD
```

Then open **http://localhost:8080** in your browser.

### Windows

```cmd
start.bat 192.0.2.11 ADMIN YOUR_PASSWORD
```

### Linux/Mac

```bash
chmod +x start.sh
./start.sh 192.0.2.11 ADMIN YOUR_PASSWORD
```

## How It Works

```
Browser (noVNC) <--WebSocket--> Python Proxy <--TCP:5900--> BMC (ATEN iKVM)
                                     |
                                     +----- HTTPS ------> BMC Web Interface
                                                          (login, JNLP, power, VM)
```

1. The Python backend logs into the BMC's web interface and retrieves a session token
2. A WebSocket proxy bridges your browser to the BMC's VNC port (5900)
3. The proxy intercepts the ATEN-specific VNC handshake (the BMC has a <100ms timeout for ClientInit)
4. noVNC (patched with ATEN decoders) renders the remote screen in your browser
5. Power control and virtual media use the BMC's CGI API

### ATEN Protocol Patches

Standard noVNC doesn't support the ATEN iKVM protocol. This project adds:

- **ATEN authentication** — Heuristic detection of ATEN servers via TightVNC security type, 24-byte handshake, null-padded credentials
- **AST2100 decoder** — DCT-based video compression used by ASPEED BMC chips (clean-room reverse engineering by [Kevin Kelley](https://github.com/kelleyk/noVNC))
- **HERMON decoder** — RGB555 subrect-based encoding for older Winbond/Renesas BMCs
- **HID keycodes** — ATEN uses USB HID codes instead of X11 keysyms
- **Custom message types** — Keepalive, session, video info messages that conflict with standard VNC message types

## Virtual Media

Mount ISO images as a virtual CD-ROM visible to the server:

1. Click **Virtual Media** in the toolbar
2. Click **Browse...** and select an ISO file
3. Click **Mount**

The tool starts a minimal SMB server and tells the BMC to mount from it. The BMC must be able to reach your machine on the network (SMBv1, port 445 or 10445).

Alternatively, expand "Advanced" to mount from an existing SMB share.

## Command Line Options

```
python server.py [options]

Options:
  --bmc-host HOST    BMC/IPMI IP address (default: 192.0.2.11)
  --username USER    IPMI username (default: ADMIN)
  --password PASS    IPMI password
  --web-port PORT    Web server port (default: 8080)
  --ws-port PORT     WebSocket proxy port (default: 6080)
  --vnc-port PORT    BMC VNC port (default: 5900)
  --jnlp FILE        Use a downloaded .jnlp file instead of web auth
  --no-auth          Skip web auth, use credentials directly
```

## Known Issues & Limitations

- The BMC only allows one KVM session at a time
- Session tokens expire — if disconnected, restart the server
- The ATEN video decoder may produce visual artifacts in some screen modes
- Virtual media requires SMBv1 (the BMC's client is old)
- Port 445 may be blocked by Windows' own SMB server (falls back to 10445)

## Contributing

This started as a weekend project to replace a broken Java applet. There's plenty of room for improvement:

- **Test on more boards** — Report success/failure with your hardware
- **Video quality** — The AST2100 decoder could be optimized
- **Auto-reconnect** — Handle session timeouts gracefully
- **Direct ISO streaming** — Use the port 623 protocol instead of SMB
- **TLS support** — Some newer BMCs require TLS-wrapped VNC

## Credits

- [noVNC](https://github.com/novnc/noVNC) — The HTML5 VNC client this is built on
- [Kevin Kelley](https://github.com/kelleyk/noVNC/tree/bmc-support) — Original ATEN protocol reverse engineering and AST2100 decoder
- [Eclypsium USBAnywhere](https://github.com/eclypsium/USBAnywhere) — Virtual media protocol research
- [samozy/iusb](https://github.com/samozy/iusb) — IUSB protocol documentation

## License

- noVNC components: [MPL 2.0](https://www.mozilla.org/en-US/MPL/2.0/)
- AST2100 decoder: Copyright 2015-2017 Kevin Kelley (clean-room reverse engineering)
- Server and proxy code: MPL 2.0
