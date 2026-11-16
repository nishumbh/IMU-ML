"""WPA2 AP provisioning, available ONLY in explicit bounded maintenance.

HTTP is not TLS. A USB-generated unique AP password protects the radio link;
setup.key is an out-of-band factory token entered as 64 hexadecimal digits.
The page never contains a secret. This is a bench prototype, not a production
provisioning protocol. Never expose this server to an untrusted wired bridge.
"""
import json
import os
from system import storage
from system.config import validate_device, validate_wifi
from system.crypto import constant_time_equal, hex_bytes
from system.transport import (Deadline, read_headers, content_length,
                              read_body, send_all)

MAX_REQUEST = 4096
# No externally loaded resources and no secret values in page source/responses.
FORM = b'''<!doctype html><meta charset="utf-8"><title>Device setup</title>
<p>Factory maintenance only. HTTP over the private WPA2 setup AP, not TLS.
Enter the factory setup token from USB. Reset after configuration/update.</p>
<form id="f">
<label>Setup token (hex)<input id="token" type="password" required></label><br>
<label>Role<select id="role"><option>master</option><option>slave</option></select></label>
<label>Iteration<select id="iteration"><option>1</option><option>2</option></select></label>
<label>Channel<input id="channel" type="number" min="1" max="11" value="1"></label><br>
<label>Peer MAC (12 hex)<input id="peer_mac"></label><br>
<label>PMK (32 hex)<input id="pmk" type="password"></label><br>
<label>LMK (32 hex)<input id="lmk" type="password"></label><br>
<label>Wi-Fi SSID<input id="ssid"></label><br>
<label>Wi-Fi password<input id="password" type="password"></label><br>
<label>OTA bundle URL<input id="ota_url" type="url"></label><br>
<button>Save configuration</button><button id="update" type="button">Fetch signed update</button>
</form><p id="status"></p>
<script>
const v=x=>document.getElementById(x).value;
async function send(path,body){
 document.getElementById('status').textContent='Working';
 try {let r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 document.getElementById('status').textContent=r.ok?'Done. Reset when ready.':'Request refused';}
 catch(e){document.getElementById('status').textContent='Request failed';}}
document.getElementById('f').onsubmit=e=>{e.preventDefault();
 let device={role:v('role'),iteration:Number(v('iteration')),channel:Number(v('channel')),peer_mac:v('peer_mac')};
 if(v('pmk'))device.pmk=v('pmk');if(v('lmk'))device.lmk=v('lmk');
 send('/configure',{token:v('token'),device:device,wifi:{ssid:v('ssid'),password:v('password'),ota_url:v('ota_url')}});};
document.getElementById('update').onclick=()=>send('/update',{token:v('token')});
</script>'''


def authenticate(token, root='/'):
    try:
        candidate = hex_bytes(token, 32)
    except (ValueError, TypeError):
        candidate = b'\x00' * 32
        valid = False
    else:
        valid = True
    matched = constant_time_equal(candidate, storage.secret(root, 'setup.key'))
    return bool(matched and valid)


def configure(body, root='/'):
    """Authenticated complete config replacement, never arbitrary file writes."""
    if not isinstance(body, dict) or set(body) != {'token', 'device', 'wifi'}:
        raise ValueError('invalid provisioning fields')
    if not authenticate(body['token'], root):
        raise ValueError('authentication failed')
    device = validate_device(body['device'])
    wifi = validate_wifi(body['wifi'], device['iteration'])
    # A reboot between the two writes must not activate partially provisioned
    # configuration. The bootloader refuses normal operation while this marker
    # exists; a fully authenticated retry or manual USB repair can clear it.
    marker = storage.path(root, 'config/provisioning.incomplete')
    storage.atomic_bytes(marker, b'1')
    storage.atomic_json(storage.path(root, 'config/wifi.json'), wifi)
    storage.atomic_json(storage.path(root, 'config/device.json'), device)
    os.remove(marker)
    storage.sync()
    return True


def read_request(sock, deadline):
    first, headers, initial, header_size = read_headers(
        sock, deadline, MAX_REQUEST, include_size=True)
    parts = first.split(' ')
    if len(parts) != 3 or parts[2] not in ('HTTP/1.0', 'HTTP/1.1'):
        raise ValueError('invalid request line')
    method, target = parts[0], parts[1]
    if method == 'GET' and target == '/':
        if initial or 'transfer-encoding' in headers or headers.get('content-length', '0') != '0':
            raise ValueError('GET body is not allowed')
        return method, target, None
    if method != 'POST' or target not in ('/configure', '/update'):
        raise ValueError('unsupported endpoint')
    if headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise ValueError('JSON required')
    length = content_length(headers, MAX_REQUEST - header_size)
    data = read_body(sock, initial, length, deadline)
    body = json.loads(data.decode('utf-8'))
    if not isinstance(body, dict):
        raise ValueError('JSON object required')
    return method, target, body


def _respond(sock, status, payload, deadline, html=False):
    mime = 'text/html; charset=utf-8' if html else 'application/json'
    head = ('HTTP/1.1 ' + status + '\r\nContent-Type: ' + mime +
            '\r\nContent-Length: ' + str(len(payload)) +
            '\r\nConnection: close\r\nCache-Control: no-store\r\n'
            'X-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\n\r\n')
    send_all(sock, head.encode('ascii') + payload, deadline)


def handle_client(sock, root='/', update=None, timeout=5, session_deadline=None):
    """One bounded request, generic responses only; update callback takes no args."""
    deadline = Deadline(timeout)
    try:
        method, target, body = read_request(sock, deadline)
        if method == 'GET':
            _respond(sock, '200 OK', FORM, deadline, html=True)
            return 'form'
        if not authenticate(body.get('token'), root):
            _respond(sock, '403 Forbidden', b'{"ok":false}', deadline)
            return 'denied'
        if target == '/configure':
            configure(body, root)
            action = 'configured'
        else:
            if set(body) != {'token'} or update is None:
                raise ValueError('update accepts only a token')
            update()
            action = 'updated'
        # An update may legitimately exceed the short request-read deadline.
        if session_deadline is not None:
            deadline = Deadline(min(3, session_deadline.remaining()))
        _respond(sock, '200 OK', b'{"ok":true,"restart_required":true}', deadline)
        return action
    except Exception:
        try:
            _respond(sock, '400 Bad Request', b'{"ok":false}', deadline)
        except Exception:
            pass
        return 'refused'
