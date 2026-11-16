"""Bounded reads and same-filesystem atomic replacement helpers."""
import os
import json
from system.canonical import dumps


def path(root, relative):
    return root.rstrip('/') + '/' + relative.lstrip('/')


def exists(filename):
    try:
        os.stat(filename)
        return True
    except OSError as exc:
        if exc.args and exc.args[0] == 2:
            return False
        raise


def mkdirs(directory):
    current = '/' if directory.startswith('/') else ''
    for part in directory.split('/'):
        if not part:
            continue
        current = current.rstrip('/') + '/' + part if current else part
        try:
            os.mkdir(current)
        except OSError:
            mode = getattr(os, 'lstat', os.stat)(current)[0]
            if mode & 0xf000 != 0x4000:
                raise ValueError('not a directory')


def read_bytes(filename, limit):
    with open(filename, 'rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('file too large')
    return data


def read_json(filename, limit=4096):
    return json.loads(read_bytes(filename, limit).decode('utf-8'))


def sync():
    function = getattr(os, 'sync', None)
    if function:
        function()


def atomic_bytes(filename, data):
    temporary = filename + '.tmp'
    with open(temporary, 'wb') as stream:
        stream.write(data)
        stream.flush()
    sync()
    os.rename(temporary, filename)
    sync()


def atomic_json(filename, value):
    atomic_bytes(filename, dumps(value).encode('utf-8'))


def secret(root, name):
    value = read_bytes(path(root, 'config/' + name), 32)
    if len(value) != 32:
        raise ValueError('factory secret must be 32 bytes')
    return value
