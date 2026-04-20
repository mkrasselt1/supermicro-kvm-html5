"""
Minimal SMB1 server for serving a single ISO file to a Supermicro BMC.

This is NOT a full SMB implementation — it handles only the minimum subset
needed for the BMC's virtual media client to read an ISO file:
- SMB1/CIFS (NT LM 0.12 dialect) only
- Anonymous authentication
- Single read-only share named "vm"
- Single file serving

The BMC's virtual media client is simple and predictable, so we only
need to handle the exact command sequence it uses.
"""

import os
import struct
import socket
import threading
import time


# SMB1 command codes
SMB_COM_NEGOTIATE = 0x72
SMB_COM_SESSION_SETUP_ANDX = 0x73
SMB_COM_TREE_CONNECT_ANDX = 0x75
SMB_COM_TREE_DISCONNECT = 0x71
SMB_COM_OPEN_ANDX = 0x2D
SMB_COM_READ_ANDX = 0x2E
SMB_COM_CLOSE = 0x04
SMB_COM_TRANS2 = 0x32
SMB_COM_ECHO = 0x2B
SMB_COM_NT_CREATE_ANDX = 0xA2

# SMB flags
FLAGS_REPLY = 0x80
FLAGS2_UNICODE = 0xC001
FLAGS2_NT_STATUS = 0xC003

# NT Status codes
STATUS_SUCCESS = 0x00000000
STATUS_NO_MORE_FILES = 0x80000006
STATUS_INVALID_HANDLE = 0xC0000008
STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034
STATUS_NOT_IMPLEMENTED = 0xC0000002


def pack_smb_header(command, status=0, flags=FLAGS_REPLY, flags2=FLAGS2_NT_STATUS,
                    tid=0, pid=0, uid=0, mid=0):
    """Build a 32-byte SMB1 header."""
    return struct.pack('<4sBIBH12sHHHHH',
        b'\xffSMB',       # Protocol
        command,           # Command
        status,            # NT Status
        flags,             # Flags
        flags2,            # Flags2
        b'\x00' * 12,     # Extra (PID high, signature, reserved)
        tid,               # Tree ID
        pid,               # Process ID
        uid,               # User ID
        mid,               # Multiplex ID
    )


class MiniSMBServer:
    """A minimal SMB1 server that serves a single file."""

    def __init__(self, iso_path, host='0.0.0.0', port=445):
        self.iso_path = os.path.abspath(iso_path)
        self.iso_name = os.path.basename(iso_path)
        self.iso_size = os.path.getsize(iso_path)
        self.host = host
        self.port = port
        self._server_socket = None
        self._running = False
        self._thread = None
        self._iso_handles = {}  # fid -> open file handle

    def start(self):
        """Start the SMB server in a background thread."""
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._server_socket.bind((self.host, self.port))
        except OSError as e:
            if e.errno == 10048 or 'Address already in use' in str(e):
                # Port 445 in use (Windows SMB), try alternate
                self.port = 10445
                self._server_socket.bind((self.host, self.port))
            else:
                raise
        self._server_socket.listen(2)
        self._server_socket.settimeout(1)
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        print(f"[SMB] Serving {self.iso_name} ({self.iso_size // (1024*1024)}MB) on port {self.port}")

    def stop(self):
        """Stop the SMB server."""
        self._running = False
        for fh in self._iso_handles.values():
            try:
                fh.close()
            except:
                pass
        self._iso_handles.clear()
        if self._server_socket:
            self._server_socket.close()
        if self._thread:
            self._thread.join(timeout=3)
        print("[SMB] Server stopped")

    def _serve(self):
        while self._running:
            try:
                conn, addr = self._server_socket.accept()
                print(f"[SMB] Connection from {addr}")
                handler = threading.Thread(target=self._handle_client,
                                          args=(conn, addr), daemon=True)
                handler.start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_client(self, conn, addr):
        """Handle a single SMB client connection."""
        conn.settimeout(30)
        tid = 0
        uid = 1
        fid_counter = 1

        try:
            while self._running:
                # Read NetBIOS session header (4 bytes)
                hdr = self._recv_exact(conn, 4)
                if not hdr:
                    break
                msg_len = struct.unpack('>I', b'\x00' + hdr[1:4])[0]
                if msg_len == 0 or msg_len > 1024 * 1024:
                    break

                data = self._recv_exact(conn, msg_len)
                if not data or len(data) < 32:
                    break

                # Parse SMB header
                if data[:4] != b'\xffSMB':
                    break

                cmd = data[4]
                req_flags = data[9]
                req_flags2 = struct.unpack('<H', data[10:12])[0]
                req_tid = struct.unpack('<H', data[24:26])[0]
                req_pid = struct.unpack('<H', data[26:28])[0]
                req_uid = struct.unpack('<H', data[28:30])[0]
                req_mid = struct.unpack('<H', data[30:32])[0]

                params = data[32:]  # Everything after the 32-byte header

                response = None

                if cmd == SMB_COM_NEGOTIATE:
                    response = self._handle_negotiate(req_pid, req_mid)
                elif cmd == SMB_COM_SESSION_SETUP_ANDX:
                    response = self._handle_session_setup(req_pid, req_mid, uid)
                elif cmd == SMB_COM_TREE_CONNECT_ANDX:
                    tid = 1
                    response = self._handle_tree_connect(req_pid, req_mid, uid, tid)
                elif cmd == SMB_COM_NT_CREATE_ANDX:
                    fid = fid_counter
                    fid_counter += 1
                    self._iso_handles[fid] = open(self.iso_path, 'rb')
                    response = self._handle_nt_create(req_pid, req_mid, uid, tid, fid, params)
                elif cmd == SMB_COM_READ_ANDX:
                    response = self._handle_read(req_pid, req_mid, uid, tid, params)
                elif cmd == SMB_COM_CLOSE:
                    fid = struct.unpack('<H', params[1:3])[0] if len(params) >= 3 else 0
                    if fid in self._iso_handles:
                        self._iso_handles[fid].close()
                        del self._iso_handles[fid]
                    response = self._handle_close(req_pid, req_mid, uid, tid)
                elif cmd == SMB_COM_TRANS2:
                    response = self._handle_trans2(req_pid, req_mid, uid, tid, params)
                elif cmd == SMB_COM_TREE_DISCONNECT:
                    response = self._handle_tree_disconnect(req_pid, req_mid, uid)
                elif cmd == SMB_COM_ECHO:
                    response = self._handle_echo(req_pid, req_mid, data)
                else:
                    # Unknown command — send error
                    smb_hdr = pack_smb_header(cmd, STATUS_NOT_IMPLEMENTED,
                                              tid=tid, pid=req_pid, uid=uid, mid=req_mid)
                    response = smb_hdr + b'\x00\x00\x00'

                if response:
                    # Wrap in NetBIOS session header
                    nb_hdr = struct.pack('>I', len(response))
                    nb_hdr = bytes([0]) + nb_hdr[1:]  # Session message type 0
                    conn.sendall(nb_hdr + response)

        except (ConnectionResetError, BrokenPipeError, socket.timeout, OSError):
            pass
        finally:
            conn.close()
            print(f"[SMB] {addr} disconnected")

    def _recv_exact(self, conn, n):
        data = b''
        while len(data) < n:
            try:
                chunk = conn.recv(n - len(data))
                if not chunk:
                    return None
                data += chunk
            except:
                return None
        return data

    def _handle_negotiate(self, pid, mid):
        smb_hdr = pack_smb_header(SMB_COM_NEGOTIATE, pid=pid, mid=mid)
        # Negotiate response for NT LM 0.12
        params = struct.pack('<H', 17)  # Word count
        params += struct.pack('<H', 0)   # Dialect index (NT LM 0.12)
        params += struct.pack('<B', 0x03)  # Security mode (user level, encrypt)
        params += struct.pack('<H', 1)   # Max MPX
        params += struct.pack('<H', 1)   # Max VCs
        params += struct.pack('<I', 65535)  # Max buffer size
        params += struct.pack('<I', 65535)  # Max raw
        params += struct.pack('<I', 0)   # Session key
        params += struct.pack('<I', 0xF3F3)  # Capabilities (unicode, NT status, large files, etc)
        params += struct.pack('<Q', 0)   # System time
        params += struct.pack('<h', 0)   # Server timezone
        params += struct.pack('<B', 0)   # Encryption key length
        # Byte count + data
        data = struct.pack('<H', 0)  # Byte count
        return smb_hdr + params + data

    def _handle_session_setup(self, pid, mid, uid):
        smb_hdr = pack_smb_header(SMB_COM_SESSION_SETUP_ANDX, pid=pid, uid=uid, mid=mid)
        params = struct.pack('<B', 3)   # Word count
        params += struct.pack('<B', 0xFF)  # AndX command (none)
        params += struct.pack('<B', 0)   # Reserved
        params += struct.pack('<H', 0)   # AndX offset
        params += struct.pack('<H', 0)   # Action (guest)
        # Byte count
        data = struct.pack('<H', 0)
        return smb_hdr + params + data

    def _handle_tree_connect(self, pid, mid, uid, tid):
        smb_hdr = pack_smb_header(SMB_COM_TREE_CONNECT_ANDX, tid=tid, pid=pid, uid=uid, mid=mid)
        params = struct.pack('<B', 3)   # Word count
        params += struct.pack('<B', 0xFF)  # AndX command
        params += struct.pack('<B', 0)   # Reserved
        params += struct.pack('<H', 0)   # AndX offset
        params += struct.pack('<H', 0x01)  # Optional support
        # Byte count + service type
        service = b'A:\x00'
        data = struct.pack('<H', len(service)) + service
        return smb_hdr + params + data

    def _handle_nt_create(self, pid, mid, uid, tid, fid, params):
        smb_hdr = pack_smb_header(SMB_COM_NT_CREATE_ANDX, tid=tid, pid=pid, uid=uid, mid=mid)
        resp = struct.pack('<B', 34)    # Word count
        resp += struct.pack('<B', 0xFF)  # AndX command
        resp += struct.pack('<B', 0)    # Reserved
        resp += struct.pack('<H', 0)    # AndX offset
        resp += struct.pack('<B', 0)    # Oplock level
        resp += struct.pack('<H', fid)  # FID
        resp += struct.pack('<I', 1)    # Create action (opened)
        resp += struct.pack('<Q', 0)    # Creation time
        resp += struct.pack('<Q', 0)    # Last access time
        resp += struct.pack('<Q', 0)    # Last write time
        resp += struct.pack('<Q', 0)    # Change time
        resp += struct.pack('<I', 0x20)  # File attributes (archive)
        resp += struct.pack('<Q', self.iso_size)  # Allocation size
        resp += struct.pack('<Q', self.iso_size)  # End of file
        resp += struct.pack('<H', 0)    # File type (disk)
        resp += struct.pack('<H', 0)    # Device state
        resp += struct.pack('<B', 0)    # Directory flag
        # Byte count
        data = struct.pack('<H', 0)
        return smb_hdr + resp + data

    def _handle_read(self, pid, mid, uid, tid, params):
        if len(params) < 25:
            return None

        wc = params[0]
        fid = struct.unpack('<H', params[3:5])[0]
        offset = struct.unpack('<I', params[5:9])[0]
        max_count = struct.unpack('<H', params[9:11])[0]

        # High offset (for files > 4GB)
        if wc >= 12 and len(params) >= 25:
            offset_high = struct.unpack('<I', params[21:25])[0]
            offset |= (offset_high << 32)

        fh = self._iso_handles.get(fid)
        if not fh:
            smb_hdr = pack_smb_header(SMB_COM_READ_ANDX, STATUS_INVALID_HANDLE,
                                      tid=tid, pid=pid, uid=uid, mid=mid)
            return smb_hdr + b'\x00\x00\x00'

        fh.seek(offset)
        file_data = fh.read(max_count)

        smb_hdr = pack_smb_header(SMB_COM_READ_ANDX, tid=tid, pid=pid, uid=uid, mid=mid)
        data_offset = 32 + 25 + 2  # SMB header + params + byte count
        # Pad to align data
        pad = b'\x00'

        resp = struct.pack('<B', 12)     # Word count
        resp += struct.pack('<B', 0xFF)  # AndX command
        resp += struct.pack('<B', 0)     # Reserved
        resp += struct.pack('<H', 0)     # AndX offset
        resp += struct.pack('<H', 0)     # Remaining
        resp += struct.pack('<H', 0)     # Data compaction mode
        resp += struct.pack('<H', 0)     # Reserved
        resp += struct.pack('<H', len(file_data))  # Data length
        resp += struct.pack('<H', data_offset + len(pad))  # Data offset
        resp += struct.pack('<I', 0)     # Data length high
        resp += struct.pack('<I', 0)     # Reserved
        resp += struct.pack('<H', 0)     # Reserved

        byte_data = pad + file_data
        resp += struct.pack('<H', len(byte_data))  # Byte count
        resp += byte_data

        return smb_hdr + resp

    def _handle_close(self, pid, mid, uid, tid):
        smb_hdr = pack_smb_header(SMB_COM_CLOSE, tid=tid, pid=pid, uid=uid, mid=mid)
        return smb_hdr + struct.pack('<B', 0) + struct.pack('<H', 0)

    def _handle_trans2(self, pid, mid, uid, tid, params):
        # Simple TRANS2 response — return empty/error for most queries
        smb_hdr = pack_smb_header(SMB_COM_TRANS2, tid=tid, pid=pid, uid=uid, mid=mid)
        resp = struct.pack('<B', 0)   # Word count
        resp += struct.pack('<H', 0)  # Byte count
        return smb_hdr + resp

    def _handle_tree_disconnect(self, pid, mid, uid):
        smb_hdr = pack_smb_header(SMB_COM_TREE_DISCONNECT, pid=pid, uid=uid, mid=mid)
        return smb_hdr + struct.pack('<B', 0) + struct.pack('<H', 0)

    def _handle_echo(self, pid, mid, data):
        smb_hdr = pack_smb_header(SMB_COM_ECHO, pid=pid, mid=mid)
        return smb_hdr + struct.pack('<B', 1) + struct.pack('<H', 0) + struct.pack('<H', 0)

    @property
    def share_path(self):
        """Return the UNC path the BMC should use to mount."""
        return f"\\\\{{host}}\\vm\\{self.iso_name}"
