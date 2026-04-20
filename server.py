#!/usr/bin/env python3
"""
Supermicro iKVM Web Console
Python backend that:
1. Authenticates with the IPMI BMC web interface
2. Retrieves session tokens from the JNLP
3. Runs a websocket proxy to bridge browser to BMC port 5900
4. Serves the noVNC frontend
"""

import argparse
import asyncio
import base64
import http.client
import http.server
import json
import os
import re
import ssl
import struct
import sys
import threading
import traceback
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from http.cookies import SimpleCookie
from http.cookiejar import CookieJar
from pathlib import Path

try:
    import websockets
    from websockets.asyncio.server import serve as ws_serve
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

from smbserver import MiniSMBServer


SCRIPT_DIR = Path(__file__).parent
NOVNC_DIR = SCRIPT_DIR / "novnc"


def make_ssl_context():
    """Create an SSL context that ignores certificate errors (BMC uses self-signed)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # Use a more permissive set of ciphers for old BMC firmware
    ctx.set_ciphers('DEFAULT:@SECLEVEL=0')
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1
    except (AttributeError, ValueError):
        pass
    return ctx


def ipmi_login(bmc_host, username, password):
    """Login to the IPMI BMC web interface and return session cookie."""

    # Supermicro/ATEN BMCs use plaintext credentials in the POST body
    post_data = f"name={urllib.parse.quote(username)}&pwd={urllib.parse.quote(password)}"

    for scheme in ['https', 'http']:
        print(f"[AUTH] Trying {scheme}://{bmc_host}/cgi/login.cgi")
        try:
            if scheme == 'https':
                ctx = make_ssl_context()
                conn = http.client.HTTPSConnection(bmc_host, timeout=10, context=ctx)
            else:
                conn = http.client.HTTPConnection(bmc_host, timeout=10)

            conn.request('POST', '/cgi/login.cgi', body=post_data,
                         headers={'Content-Type': 'application/x-www-form-urlencoded'})
            resp = conn.getresponse()
            body = resp.read().decode('utf-8', errors='replace')

            # Extract SID cookie (skip the "clear" cookie with expires in 1970)
            sid = None
            for header_name, header_value in resp.getheaders():
                if header_name.lower() == 'set-cookie' and 'SID=' in header_value:
                    if 'expires' not in header_value.lower():
                        sid = header_value.split('SID=')[1].split(';')[0]

            if sid:
                print(f"[AUTH] Login OK! SID={sid}")
                return {'SID': sid}, body, scheme
            else:
                print(f"[AUTH] No SID cookie in response (login may have failed)")

            conn.close()
        except Exception as e:
            print(f"[AUTH] {scheme} failed: {e}")

    return {}, "", "http"


def load_jnlp_file(jnlp_path):
    """Load session info from a local JNLP file."""
    print(f"[AUTH] Loading JNLP from file: {jnlp_path}")
    content = Path(jnlp_path).read_text(encoding='utf-8')
    root = ET.fromstring(content)
    args = [arg.text for arg in root.findall('.//argument')]

    main_class = root.find('.//application-desc')
    mc = main_class.get('main-class', '') if main_class is not None else ''
    print(f"[AUTH] Main class: {mc}")
    print(f"[AUTH] Arguments: {args}")

    if 'KVMMain' in mc and len(args) >= 5:
        return {
            'host': args[0],
            'username': args[1],
            'password': args[2],
            'access_mode': args[3] if len(args) > 3 else 'manage',
            'vnc_port': int(args[4]) if len(args) > 4 else 5900,
            'ipmi_port': int(args[5]) if len(args) > 5 else 623,
        }
    elif len(args) >= 3:
        # Try to figure out VNC port
        vnc_port = 5900
        for a in args:
            if a and a.isdigit() and 5900 <= int(a) <= 5999:
                vnc_port = int(a)
                break
        return {
            'host': args[0],
            'username': args[1],
            'password': args[2] if len(args) > 2 else args[1],
            'vnc_port': vnc_port,
        }
    return None


def get_ikvm_session(bmc_host, cookies, scheme='http'):
    """Fetch the iKVM JNLP and extract session credentials."""
    import time
    sid = cookies.get('SID', '')
    cookie_str = f"SID={sid}; langSetFlag=0; language=English"

    # Some BMCs need a moment after login before the session is usable
    time.sleep(1)

    paths = [
        '/cgi/url_redirect.cgi?url_name=ikvm&url_type=jwsk',
        '/cgi/url_redirect.cgi?url_name=man_ikvm&url_type=jwsk',
    ]

    for path in paths:
        print(f"[AUTH] Fetching JNLP: {scheme}://{bmc_host}{path}")

        try:
            if scheme == 'https':
                ctx = make_ssl_context()
                conn = http.client.HTTPSConnection(bmc_host, timeout=10, context=ctx)
            else:
                conn = http.client.HTTPConnection(bmc_host, timeout=10)

            conn.request('GET', path, headers={
                'Cookie': cookie_str,
                'Accept': 'application/x-java-jnlp-file, */*',
                'Referer': f'{scheme}://{bmc_host}/cgi/url_redirect.cgi?url_name=man_ikvm',
            })
            resp = conn.getresponse()
            content_type = resp.getheader('Content-Type', '')
            jnlp_content = resp.read().decode('utf-8', errors='replace')
            conn.close()

            print(f"[AUTH] Response: status={resp.status}, type={content_type}, size={len(jnlp_content)}")

            if resp.status != 200 or '<jnlp' not in jnlp_content.lower():
                if resp.status == 500:
                    print(f"[AUTH] 500 error - session may not be ready yet, retrying...")
                    time.sleep(2)
                    # Retry once
                    if scheme == 'https':
                        conn = http.client.HTTPSConnection(bmc_host, timeout=10, context=ctx)
                    else:
                        conn = http.client.HTTPConnection(bmc_host, timeout=10)
                    conn.request('GET', path, headers={
                        'Cookie': cookie_str,
                        'Accept': 'application/x-java-jnlp-file, */*',
                        'Referer': f'{scheme}://{bmc_host}/cgi/url_redirect.cgi?url_name=man_ikvm',
                    })
                    resp = conn.getresponse()
                    jnlp_content = resp.read().decode('utf-8', errors='replace')
                    conn.close()
                    print(f"[AUTH] Retry: status={resp.status}, size={len(jnlp_content)}")
                    if resp.status != 200 or '<jnlp' not in jnlp_content.lower():
                        continue
                else:
                    continue

            root = ET.fromstring(jnlp_content)
            args = [arg.text for arg in root.findall('.//argument')]
            print(f"[AUTH] JNLP arguments: {args}")

            if len(args) >= 5:
                return {
                    'host': args[0],
                    'username': args[1],
                    'password': args[2],
                    'access_mode': args[3] if len(args) > 3 else 'manage',
                    'vnc_port': int(args[4]) if len(args) > 4 else 5900,
                    'ipmi_port': int(args[5]) if len(args) > 5 else 623,
                }
        except ET.ParseError as e:
            print(f"[AUTH] JNLP XML parse error: {e}")
        except Exception as e:
            print(f"[AUTH] JNLP fetch error: {e}")
            traceback.print_exc()

    # Fallback: the SID itself IS the session token for ATEN auth
    print(f"[AUTH] JNLP fetch failed, using SID as session token")
    return {
        'host': bmc_host,
        'username': sid,
        'password': sid,
        'vnc_port': 5900,
    }


class WebSocketProxy:
    """Websocket-to-TCP proxy for websockets 15.x."""

    def __init__(self, listen_host, listen_port, target_host, target_port):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.target_host = target_host
        self.target_port = target_port

    async def proxy_handler(self, websocket):
        """Handle a websocket connection with ATEN-aware VNC handshake.

        The ATEN BMC has a very short timeout between sending the auth
        result and expecting ClientInit. The websocket round-trip to the
        browser is too slow, so we intercept the handshake at the proxy
        level and inject ClientInit immediately after auth success.
        """
        print(f"[PROXY] New WebSocket connection")

        try:
            reader, writer = await asyncio.open_connection(
                self.target_host, self.target_port)
            print(f"[PROXY] TCP connected to {self.target_host}:{self.target_port}")
        except Exception as e:
            print(f"[PROXY] Failed to connect to target: {e}")
            return

        # Phase 1: Intercept the VNC handshake to inject ClientInit at the right time
        try:
            # 1. Server sends RFB version (12 bytes)
            server_ver = await asyncio.wait_for(reader.read(12), timeout=5)
            print(f"[PROXY] Server version: {server_ver}")
            await websocket.send(server_ver)

            # 2. Client sends RFB version
            client_ver = await asyncio.wait_for(websocket.recv(), timeout=10)
            writer.write(client_ver if isinstance(client_ver, bytes) else client_ver.encode('latin-1'))
            await writer.drain()

            # 3. Server sends security types
            sec_types = await asyncio.wait_for(reader.read(256), timeout=5)
            print(f"[PROXY] Security types: {sec_types.hex()}")
            await websocket.send(sec_types)

            # 4. Client selects security type
            sec_choice = await asyncio.wait_for(websocket.recv(), timeout=10)
            sec_choice_bytes = sec_choice if isinstance(sec_choice, bytes) else sec_choice.encode('latin-1')
            writer.write(sec_choice_bytes)
            await writer.drain()
            chosen_type = sec_choice_bytes[0] if sec_choice_bytes else 0
            print(f"[PROXY] Client chose security type: {chosen_type:#x}")

            is_aten = (chosen_type == 0x10)

            if is_aten:
                # 5. ATEN: Server sends 24 bytes handshake data
                aten_data = await asyncio.wait_for(reader.read(24), timeout=5)
                print(f"[PROXY] ATEN handshake data ({len(aten_data)}b)")
                await websocket.send(aten_data)

                # 6. Client sends 48 bytes credentials
                creds = await asyncio.wait_for(websocket.recv(), timeout=10)
                creds_bytes = creds if isinstance(creds, bytes) else creds.encode('latin-1')

                # CRITICAL: Send credentials + ClientInit together in one write
                # The BMC has a very tight timeout after auth and may close
                # if ClientInit doesn't arrive in the same TCP segment.
                writer.write(creds_bytes + b'\x01')  # credentials + ClientInit(shared=1)
                await writer.drain()
                print(f"[PROXY] Sent credentials ({len(creds_bytes)}b) + ClientInit in one packet")

                # 7. Server sends auth result (4 bytes)
                auth_result = await asyncio.wait_for(reader.read(4), timeout=5)
                result_val = struct.unpack('>I', auth_result)[0]
                print(f"[PROXY] Auth result: {result_val} ({'OK' if result_val == 0 else 'FAIL'})")

                if result_val == 0:
                    # Read ServerInit immediately (BMC sends it right after auth+ClientInit)
                    server_init = await asyncio.wait_for(reader.read(4096), timeout=5)
                    print(f"[PROXY] ServerInit ({len(server_init)}b): {server_init[:20].hex()}...")

                    # Send auth_result + server_init together so noVNC has all
                    # the data it needs in one go (avoids websocket round-trip delay)
                    await websocket.send(auth_result + server_init)
                    print(f"[PROXY] Sent auth+ServerInit ({4 + len(server_init)}b) to browser")

                    self._eat_client_init = True
                else:
                    await websocket.send(auth_result)
                    print(f"[PROXY] Auth failed, closing")
                    return
            else:
                # Non-ATEN: let the handshake pass through normally
                self._eat_client_init = False

        except Exception as e:
            print(f"[PROXY] Handshake error: {e}")
            traceback.print_exc()
            writer.close()
            return

        # Phase 2: Normal bidirectional proxy
        print(f"[PROXY] Handshake complete, entering proxy mode")
        closed_by = None
        bytes_from_server = 0
        bytes_from_client = 0

        async def ws_to_tcp():
            nonlocal closed_by, bytes_from_client
            ate_init = False
            try:
                async for message in websocket:
                    data = message if isinstance(message, bytes) else message.encode('latin-1')
                    # Eat the ClientInit that noVNC sends (we already sent it)
                    if self._eat_client_init and not ate_init:
                        if len(data) >= 1:
                            print(f"[PROXY] Eating ClientInit byte (0x{data[0]:02x}), remaining: {len(data)-1}b")
                            data = data[1:]
                            ate_init = True
                            if not data:
                                continue
                    bytes_from_client += len(data)
                    if bytes_from_client <= 500:
                        print(f"[RELAY] client->server ({len(data)}b): {data[:40].hex()}")
                    writer.write(data)
                    await writer.drain()
            except Exception as e:
                if not closed_by:
                    closed_by = f"ws->tcp error: {type(e).__name__}: {e}"
            finally:
                writer.close()

        async def tcp_to_ws():
            nonlocal closed_by, bytes_from_server
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        closed_by = "server closed TCP"
                        break
                    bytes_from_server += len(data)
                    if bytes_from_server <= 500:
                        print(f"[RELAY] server->client ({len(data)}b): {data[:60].hex()}")
                    try:
                        await websocket.send(data)
                    except Exception as e:
                        closed_by = f"ws send error: {type(e).__name__}: {e}"
                        break
            except Exception as e:
                if not closed_by:
                    closed_by = f"tcp->ws error: {type(e).__name__}: {e}"

        try:
            await asyncio.gather(ws_to_tcp(), tcp_to_ws())
        except Exception:
            pass
        finally:
            print(f"[PROXY] Session ended: {closed_by}")
            print(f"[PROXY] Total: server={bytes_from_server}b, client={bytes_from_client}b")

    async def start(self):
        try:
            # websockets 15.x API
            from websockets.asyncio.server import serve
            server = await serve(
                self.proxy_handler,
                self.listen_host,
                self.listen_port,
                subprotocols=["binary"],
                max_size=None,
                compression=None,
            )
        except TypeError:
            # Fallback for different API versions
            server = await ws_serve(
                self.proxy_handler,
                self.listen_host,
                self.listen_port,
            )
        print(f"[PROXY] WebSocket proxy listening on ws://{self.listen_host}:{self.listen_port}")
        return server


class NoVNCHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler that serves noVNC and provides session info."""

    session_info = None
    bmc_host = None
    novnc_dir = None
    ws_port = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.novnc_dir), **kwargs)

    bmc_sid = None
    bmc_scheme = 'https'
    smb_server = None  # MiniSMBServer instance
    local_ip = None    # Our IP as seen from the BMC network

    def _bmc_request(self, post_data):
        """Make a POST request to the BMC's ipmi.cgi."""
        try:
            if self.bmc_scheme == 'https':
                ctx = make_ssl_context()
                conn = http.client.HTTPSConnection(self.bmc_host, timeout=10, context=ctx)
            else:
                conn = http.client.HTTPConnection(self.bmc_host, timeout=10)
            conn.request('POST', '/cgi/ipmi.cgi', body=post_data,
                         headers={
                             'Cookie': f'SID={self.bmc_sid}',
                             'Content-Type': 'application/x-www-form-urlencoded',
                         })
            resp = conn.getresponse()
            body = resp.read().decode('utf-8', errors='replace')
            conn.close()
            return body
        except Exception as e:
            return f'<error>{e}</error>'

    def _bmc_cgi_request(self, path, post_data):
        """Make a request to any BMC CGI endpoint."""
        try:
            if self.bmc_scheme == 'https':
                ctx = make_ssl_context()
                conn = http.client.HTTPSConnection(self.bmc_host, timeout=10, context=ctx)
            else:
                conn = http.client.HTTPConnection(self.bmc_host, timeout=10)
            method = 'POST' if post_data else 'GET'
            conn.request(method, path, body=post_data if post_data else None,
                         headers={
                             'Cookie': f'SID={self.bmc_sid}',
                             'Content-Type': 'application/x-www-form-urlencoded',
                         })
            resp = conn.getresponse()
            body = resp.read().decode('utf-8', errors='replace')
            conn.close()
            return body
        except Exception as e:
            return f'<error>{e}</error>'

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        if self.path == '/api/session':
            self._json_response({
                'host': self.bmc_host,
                'wsPort': self.ws_port,
                'session': self.session_info,
            })
            return
        elif self.path == '/api/power/status':
            body = self._bmc_request('POWER_INFO.XML=(0,0)')
            status = 'unknown'
            if 'STATUS="ON"' in body:
                status = 'on'
            elif 'STATUS="OFF"' in body:
                status = 'off'
            self._json_response({'status': status})
            return
        elif self.path == '/api/vm/status':
            vm_body = self._bmc_cgi_request('/cgi/vmstatus.cgi', '')
            share_body = self._bmc_request('VIRTUAL_MEDIA_SHARE_IMAGE.XML=(0,0)')
            # Parse VM status
            devices = []
            import re as _re
            for m in _re.finditer(r'DEVICE ID="(\d+)" STATUS="(\d+)"', vm_body):
                dev_id, status = m.group(1), int(m.group(2))
                devices.append({
                    'id': int(dev_id),
                    'status': status,
                    'mounted': status != 255 and status != 0xff,
                })
            # Parse share config
            share = {}
            sm = _re.search(r'HOST="([^"]*)".*?PATH="([^"]*)"', share_body)
            if sm:
                share = {'host': sm.group(1), 'path': sm.group(2)}
            self._json_response({'devices': devices, 'share': share})
            return
        elif self.path == '/' or self.path == '':
            self.path = '/ikvm.html'
        return super().do_GET()

    def _read_post_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length > 0:
            return self.rfile.read(length)
        return b''

    def do_POST(self):
        if self.path.startswith('/api/power/'):
            action = self.path.split('/api/power/')[1]
            commands = {
                'on':       'POWER_INFO.XML=(1,1)',
                'off':      'POWER_INFO.XML=(1,0)',
                'cycle':    'POWER_INFO.XML=(1,2)',
                'reset':    'POWER_INFO.XML=(1,3)',
                'shutdown':  'POWER_INFO.XML=(1,5)',
            }
            if action in commands:
                body = self._bmc_request(commands[action])
                print(f"[POWER] {action}: {body.strip()[:100]}")
                self._json_response({'ok': True, 'action': action})
            else:
                self._json_response({'error': f'Unknown action: {action}'}, 400)
            return

        if self.path == '/api/vm/browse':
            # Open a native file picker on the server (runs on same machine)
            def pick_file():
                try:
                    import tkinter as tk
                    from tkinter import filedialog
                    root = tk.Tk()
                    root.withdraw()
                    root.attributes('-topmost', True)
                    path = filedialog.askopenfilename(
                        title='Select ISO image',
                        filetypes=[('ISO images', '*.iso'), ('Disk images', '*.img'), ('All files', '*.*')],
                    )
                    root.destroy()
                    return path or ''
                except Exception as e:
                    print(f"[VM] File picker error: {e}")
                    return ''

            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                path = pool.submit(pick_file).result(timeout=120)
            self._json_response({'path': path})
            return

        if self.path == '/api/vm/mount':
            post_data = json.loads(self._read_post_body())

            # Support both modes: local file (starts SMB server) or manual SMB share
            iso_path = post_data.get('iso_path', '')
            host = post_data.get('host', '')
            path = post_data.get('path', '')
            user = post_data.get('user', '')
            pwd = post_data.get('pwd', '')

            if iso_path:
                # Local file mode — start SMB server
                iso_path = os.path.abspath(iso_path)
                if not os.path.isfile(iso_path):
                    self._json_response({'error': f'File not found: {iso_path}'}, 400)
                    return

                # Stop existing SMB server if running
                if NoVNCHandler.smb_server:
                    NoVNCHandler.smb_server.stop()

                smb = MiniSMBServer(iso_path)
                smb.start()
                NoVNCHandler.smb_server = smb

                host = self.local_ip
                path = f"\\vm\\{smb.iso_name}"
                user = ''
                pwd = ''
                print(f"[VM] SMB server started: \\\\{host}:{smb.port}\\vm\\{smb.iso_name}")

            if not host or not path:
                self._json_response({'error': 'Need iso_path or host+path'}, 400)
                return

            # Configure share on BMC
            share_data = (f"host={urllib.parse.quote(host)}"
                          f"&path={urllib.parse.quote(path)}"
                          f"&user={urllib.parse.quote(user)}"
                          f"&pwd={urllib.parse.quote(pwd)}")
            result = self._bmc_cgi_request('/cgi/virtual_media_share_img.cgi', share_data)
            print(f"[VM] Configure share: {result.strip()}")

            # Mount
            mount_result = self._bmc_cgi_request('/cgi/uisopin.cgi', '')
            print(f"[VM] Mount: {mount_result.strip()}")

            self._json_response({'ok': True, 'host': host, 'path': path,
                                 'config': result.strip(), 'mount': mount_result.strip()})
            return

        if self.path == '/api/vm/unmount':
            result = self._bmc_cgi_request('/cgi/uisopout.cgi', '')
            print(f"[VM] Unmount: {result.strip()}")

            # Stop SMB server if we started one
            if NoVNCHandler.smb_server:
                NoVNCHandler.smb_server.stop()
                NoVNCHandler.smb_server = None

            self._json_response({'ok': True, 'result': result.strip()})
            return

    def log_message(self, format, *args):
        pass


def create_ikvm_html(novnc_dir, ws_port):
    """Create the iKVM viewer HTML page."""
    html = f'''<!DOCTYPE html>
<html>
<head>
    <title>Supermicro iKVM Console</title>
    <meta charset="utf-8">
    <style>
        body {{
            margin: 0;
            background-color: #1a1a2e;
            overflow: hidden;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            display: flex;
            flex-direction: column;
            height: 100vh;
        }}
        #top_bar {{
            background-color: #16213e;
            color: #e0e0e0;
            padding: 6px 12px;
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 13px;
            border-bottom: 1px solid #0f3460;
            flex-wrap: wrap;
        }}
        #top_bar .title {{
            font-weight: bold;
            color: #e94560;
            margin-right: 8px;
        }}
        #status {{
            color: #a0a0a0;
        }}
        #status.connected {{
            color: #4ecca3;
        }}
        #power_status {{
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 12px;
            font-weight: bold;
        }}
        #power_status.on {{
            background: #1b4332;
            color: #52b788;
        }}
        #power_status.off {{
            background: #3d0000;
            color: #e56b6f;
        }}
        .separator {{
            width: 1px;
            height: 20px;
            background: #0f3460;
            margin: 0 4px;
        }}
        #screen {{
            width: 100%;
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .btn {{
            background: #0f3460;
            color: #e0e0e0;
            border: none;
            padding: 4px 10px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
        }}
        .btn:hover {{
            background: #1a4a7a;
        }}
        .btn.danger {{
            background: #5c1a1a;
        }}
        .btn.danger:hover {{
            background: #8b2525;
        }}
        .btn.success {{
            background: #1a4a2a;
        }}
        .btn.success:hover {{
            background: #257a3a;
        }}
        .btn:disabled {{
            opacity: 0.5;
            cursor: not-allowed;
        }}
    </style>
</head>
<body>
    <div id="top_bar">
        <span class="title">Supermicro iKVM</span>
        <span id="status">Connecting...</span>
        <span id="power_status"></span>
        <span style="flex:1"></span>
        <button class="btn" onclick="sendCtrlAltDel()">Ctrl+Alt+Del</button>
        <div class="separator"></div>
        <button class="btn success" onclick="powerAction('on')" title="Power On">Power On</button>
        <button class="btn danger" onclick="powerAction('shutdown')" title="ACPI Soft Shutdown">Shutdown</button>
        <button class="btn" onclick="powerAction('reset')" title="Hard Reset">Reset</button>
        <button class="btn" onclick="powerAction('cycle')" title="Power Cycle (off+on)">Cycle</button>
        <button class="btn danger" onclick="powerAction('off')" title="Immediate Power Off">Force Off</button>
        <div class="separator"></div>
        <button class="btn" onclick="toggleVmPanel()">Virtual Media</button>
        <button class="btn" onclick="toggleFullscreen()">Fullscreen</button>
    </div>
    <div id="vm_panel" style="display:none; background:#16213e; padding:10px 16px; border-bottom:1px solid #0f3460; color:#e0e0e0; font-size:13px;">
        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:6px;">
            <span style="font-weight:bold;">Virtual CD-ROM:</span>
            <input id="vm_iso" type="text" style="width:300px; background:#0a1628; color:#e0e0e0; border:1px solid #0f3460; padding:3px 8px;" placeholder="Select an ISO image...">
            <button class="btn" onclick="vmBrowse()">Browse...</button>
            <button class="btn success" onclick="vmMountLocal()">Mount</button>
            <button class="btn danger" onclick="vmUnmount()">Eject</button>
            <span id="vm_status" style="color:#a0a0a0;"></span>
        </div>
        <details style="font-size:12px; color:#888;">
            <summary style="cursor:pointer;">Advanced: Mount from SMB share</summary>
            <div style="display:flex; align-items:center; gap:8px; margin-top:6px; flex-wrap:wrap;">
                <label>Host: <input id="vm_host" type="text" style="width:120px; background:#0a1628; color:#e0e0e0; border:1px solid #0f3460; padding:2px 6px;"></label>
                <label>Path: <input id="vm_path" type="text" style="width:180px; background:#0a1628; color:#e0e0e0; border:1px solid #0f3460; padding:2px 6px;" placeholder="\\share\\image.iso"></label>
                <label>User: <input id="vm_user" type="text" style="width:70px; background:#0a1628; color:#e0e0e0; border:1px solid #0f3460; padding:2px 6px;"></label>
                <label>Pass: <input id="vm_pwd" type="password" style="width:70px; background:#0a1628; color:#e0e0e0; border:1px solid #0f3460; padding:2px 6px;"></label>
                <button class="btn" onclick="vmMountSmb()">Mount SMB</button>
            </div>
        </details>
    </div>
    <div id="screen"></div>

    <script type="module">
        import RFB from './core/rfb.js';

        let rfb;

        // Power status polling
        async function updatePowerStatus() {{
            try {{
                const resp = await fetch('/api/power/status');
                const data = await resp.json();
                const el = document.getElementById('power_status');
                el.textContent = data.status.toUpperCase();
                el.className = data.status;
            }} catch(e) {{}}
        }}
        updatePowerStatus();
        setInterval(updatePowerStatus, 5000);

        window.powerAction = async function(action) {{
            const dangerous = ['off', 'reset', 'cycle', 'shutdown'];
            if (dangerous.includes(action)) {{
                const labels = {{
                    off: 'FORCE POWER OFF (immediate, no graceful shutdown)',
                    reset: 'HARD RESET the server',
                    cycle: 'POWER CYCLE the server (off then on)',
                    shutdown: 'send ACPI shutdown signal',
                }};
                if (!confirm('Are you sure you want to ' + labels[action] + '?')) return;
            }}
            try {{
                const resp = await fetch('/api/power/' + action, {{method: 'POST'}});
                const data = await resp.json();
                console.log('Power action:', data);
                setTimeout(updatePowerStatus, 2000);
            }} catch(e) {{
                alert('Power action failed: ' + e.message);
            }}
        }};

        async function connect() {{
            const resp = await fetch('/api/session');
            const info = await resp.json();
            const session = info.session;

            if (!session) {{
                document.getElementById('status').textContent = 'Failed to get session';
                return;
            }}

            document.getElementById('status').textContent =
                'Connecting to ' + info.host + '...';

            const wsUrl = 'ws://' + window.location.hostname + ':{ws_port}';
            const target = document.getElementById('screen');

            rfb = new RFB(target, wsUrl, {{
                credentials: {{
                    username: session.username,
                    password: session.password,
                }},
                wsProtocols: ['binary'],
            }});

            rfb.scaleViewport = true;
            rfb.resizeSession = false;

            rfb.addEventListener('connect', () => {{
                const status = document.getElementById('status');
                status.textContent = 'Connected to ' + info.host;
                status.className = 'connected';
            }});

            rfb.addEventListener('disconnect', (e) => {{
                const status = document.getElementById('status');
                status.textContent = 'Disconnected' + (e.detail.clean ? '' : ' (error)');
                status.className = '';
            }});

            rfb.addEventListener('credentialsrequired', (e) => {{
                rfb.sendCredentials({{
                    username: session.username,
                    password: session.password,
                }});
            }});

            rfb.addEventListener('securityfailure', (e) => {{
                const status = document.getElementById('status');
                status.textContent = 'Auth failed: ' + (e.detail.reason || 'unknown');
                status.className = '';
            }});
        }}

        window.sendCtrlAltDel = function() {{
            if (rfb) rfb.sendCtrlAltDel();
        }};

        // Virtual Media
        window.toggleVmPanel = function() {{
            const panel = document.getElementById('vm_panel');
            if (panel.style.display === 'none') {{
                panel.style.display = 'block';
                updateVmStatus();
            }} else {{
                panel.style.display = 'none';
            }}
        }};

        async function updateVmStatus() {{
            try {{
                const resp = await fetch('/api/vm/status');
                const data = await resp.json();
                const el = document.getElementById('vm_status');
                const mounted = data.devices.some(d => d.mounted);
                if (mounted) {{
                    el.textContent = 'Mounted: ' + (data.share.host || '') + (data.share.path || '');
                    el.style.color = '#4ecca3';
                }} else {{
                    el.textContent = 'No image mounted';
                    el.style.color = '#a0a0a0';
                }}
                if (data.share.host) {{
                    document.getElementById('vm_host').placeholder = data.share.host;
                    document.getElementById('vm_path').placeholder = data.share.path || '';
                }}
            }} catch(e) {{}}
        }}

        window.vmBrowse = async function() {{
            document.getElementById('vm_status').textContent = 'Opening file picker...';
            try {{
                const resp = await fetch('/api/vm/browse', {{method: 'POST'}});
                const data = await resp.json();
                if (data.path) {{
                    document.getElementById('vm_iso').value = data.path;
                    document.getElementById('vm_status').textContent = '';
                }} else {{
                    document.getElementById('vm_status').textContent = 'No file selected';
                }}
            }} catch(e) {{
                document.getElementById('vm_status').textContent = 'Browse failed';
            }}
        }};

        window.vmMountLocal = async function() {{
            const isoPath = document.getElementById('vm_iso').value.trim();
            if (!isoPath) {{
                alert('Enter the path to an ISO file');
                return;
            }}
            document.getElementById('vm_status').textContent = 'Starting SMB server & mounting...';
            try {{
                const resp = await fetch('/api/vm/mount', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{iso_path: isoPath}})
                }});
                const data = await resp.json();
                if (data.error) {{
                    document.getElementById('vm_status').textContent = 'Error: ' + data.error;
                    document.getElementById('vm_status').style.color = '#e56b6f';
                }} else {{
                    console.log('VM mount:', data);
                    document.getElementById('vm_status').textContent = 'Mounted! BMC reading from local SMB...';
                    document.getElementById('vm_status').style.color = '#4ecca3';
                    setTimeout(updateVmStatus, 3000);
                }}
            }} catch(e) {{
                alert('Mount failed: ' + e.message);
            }}
        }};

        window.vmMountSmb = async function() {{
            const host = document.getElementById('vm_host').value;
            const path = document.getElementById('vm_path').value;
            const user = document.getElementById('vm_user').value;
            const pwd = document.getElementById('vm_pwd').value;
            if (!host || !path) {{
                alert('Host and Path are required');
                return;
            }}
            document.getElementById('vm_status').textContent = 'Mounting from SMB...';
            try {{
                const resp = await fetch('/api/vm/mount', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{host, path, user, pwd}})
                }});
                const data = await resp.json();
                console.log('VM mount:', data);
                setTimeout(updateVmStatus, 3000);
            }} catch(e) {{
                alert('Mount failed: ' + e.message);
            }}
        }};

        window.vmUnmount = async function() {{
            try {{
                const resp = await fetch('/api/vm/unmount', {{method: 'POST'}});
                const data = await resp.json();
                console.log('VM unmount:', data);
                document.getElementById('vm_status').textContent = 'Unmounting...';
                setTimeout(updateVmStatus, 2000);
            }} catch(e) {{
                alert('Unmount failed: ' + e.message);
            }}
        }};

        window.toggleFullscreen = function() {{
            if (!document.fullscreenElement) {{
                document.documentElement.requestFullscreen();
            }} else {{
                document.exitFullscreen();
            }}
        }};

        connect();
    </script>
</body>
</html>'''
    (novnc_dir / 'ikvm.html').write_text(html, encoding='utf-8')


async def main():
    parser = argparse.ArgumentParser(description='Supermicro iKVM Web Console')
    parser.add_argument('--bmc-host', default='192.0.2.11',
                        help='BMC/IPMI IP address (default: 192.0.2.11)')
    parser.add_argument('--username', '-u', default='ADMIN',
                        help='IPMI username (default: ADMIN)')
    parser.add_argument('--password', '-p', default='ADMIN',
                        help='IPMI password')
    parser.add_argument('--web-port', type=int, default=8080,
                        help='Web server port (default: 8080)')
    parser.add_argument('--ws-port', type=int, default=6080,
                        help='WebSocket proxy port (default: 6080)')
    parser.add_argument('--vnc-port', type=int, default=5900,
                        help='BMC VNC port (default: 5900)')
    parser.add_argument('--no-auth', action='store_true',
                        help='Skip IPMI web auth, use credentials directly as ATEN session tokens')
    parser.add_argument('--jnlp', type=str, default=None,
                        help='Path to a console-redirection.jnlp file (skip web auth)')
    args = parser.parse_args()

    if not HAS_WEBSOCKETS:
        print("ERROR: 'websockets' package is required. Install with: pip install websockets")
        sys.exit(1)

    print(f"=== Supermicro iKVM Web Console ===")
    print(f"BMC Host: {args.bmc_host}")

    session_info = None
    scheme = 'https'

    if args.jnlp:
        # Load session from a JNLP file
        session_info = load_jnlp_file(args.jnlp)
        if session_info:
            print(f"\n[OK] Loaded from JNLP: user='{session_info['username']}', port={session_info['vnc_port']}")
            args.vnc_port = session_info['vnc_port']
            args.bmc_host = session_info['host']
        else:
            print(f"\n[ERROR] Failed to parse JNLP file")
            sys.exit(1)
    elif not args.no_auth:
        # Step 1: Login to IPMI web interface
        print(f"\n[1/3] Logging into IPMI web interface...")
        cookies, body, scheme = ipmi_login(args.bmc_host, args.username, args.password)

        # Step 2: Get iKVM session - try even without cookies (IP-based session)
        print(f"\n[2/3] Retrieving iKVM session...")
        session_info = get_ikvm_session(args.bmc_host, cookies, scheme)

        if session_info:
            print(f"\n[OK] Session: user='{session_info['username']}', port={session_info['vnc_port']}")
            args.vnc_port = session_info['vnc_port']
        else:
            print(f"\n[WARN] Could not get JNLP session. Using direct credentials.")
            print(f"[WARN] This may fail if the BMC requires session tokens.")
            session_info = {
                'host': args.bmc_host,
                'username': args.username,
                'password': args.password,
                'vnc_port': args.vnc_port,
            }
    else:
        session_info = {
            'host': args.bmc_host,
            'username': args.username,
            'password': args.password,
            'vnc_port': args.vnc_port,
        }

    # Step 3: Start servers
    print(f"\n[3/3] Starting servers...")

    create_ikvm_html(NOVNC_DIR, args.ws_port)

    NoVNCHandler.session_info = session_info
    NoVNCHandler.bmc_host = args.bmc_host
    NoVNCHandler.novnc_dir = NOVNC_DIR
    NoVNCHandler.ws_port = args.ws_port
    NoVNCHandler.bmc_sid = session_info.get('username', '')  # SID = session token
    NoVNCHandler.bmc_scheme = scheme

    # Detect our local IP (as reachable from the BMC)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((args.bmc_host, 80))
        local_ip = s.getsockname()[0]
        s.close()
    except:
        local_ip = '127.0.0.1'
    NoVNCHandler.local_ip = local_ip
    print(f"[NET] Local IP: {local_ip}")

    httpd = http.server.HTTPServer(('0.0.0.0', args.web_port), NoVNCHandler)
    http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    http_thread.start()
    print(f"[HTTP] Serving noVNC on http://localhost:{args.web_port}")

    proxy = WebSocketProxy('0.0.0.0', args.ws_port, args.bmc_host, args.vnc_port)
    ws_server = await proxy.start()

    print(f"\n{'='*50}")
    print(f"  Open http://localhost:{args.web_port} in your browser")
    print(f"{'='*50}\n")

    try:
        await asyncio.Future()
    except (KeyboardInterrupt, SystemExit):
        print("\nShutting down...")
        ws_server.close()
        httpd.shutdown()


if __name__ == '__main__':
    asyncio.run(main())
