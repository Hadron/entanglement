#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


import base64, contextlib, hashlib, logging, re
try:
    from OpenSSL import crypto as _crypto
except ImportError:
    _crypto  = None

try:
    from sqlalchemy.types import TypeDecorator, String
    from sqlalchemy import inspect
except ImportError:
    TypeDecorator = None
    
class CertHash(bytes):
    "represents a hash of a certificate"

    def __new__(self,hash):
        "Construct from a RFC 6920 URI, the base64 of a SHa256sum"
        if isinstance(hash, str):
            m = re.match( r'(?:ni://[^/]*/sha-256;)?([-a-zA-Z0-9_=]+)', hash)
            if m:
                return bytes.__new__(self, base64.urlsafe_b64decode(m.group(1)))
        return bytes.__new__(self,hash)

    def __init__(self, *args):
        if len(self) != 32:
            raise ValueError("A SHa256 checksum is exactly 32 bytes")

        

    def __str__(self):
        return "ni:///sha-256;" + str(base64.urlsafe_b64encode(self), 'utf-8')

    def __repr__(self):
        return '"'+str(self)+'"'

    def sync_encode_value(self): return str(self)

    def __eq__(self, other):
        res = super().__eq__(other)
        if res == True: return True
        if isinstance(other, str):
            try:
                return self.__eq__(CertHash(other))
            except: return res
            

    def __hash__(self): return hash(str(self))

    @classmethod
    def from_der_cert(cls, cert):
        if isinstance(cert, _crypto.X509):
            cert = _crypto.dump_certificate(_crypto.FILETYPE_ASN1, cert)
        return CertHash(hashlib.sha256(cert).digest())
    

def certhash_from_file(fn):
    if not _crypto: raise NotImplementedError
    with open(fn, 'rb') as f:
        der_cert = _crypto.dump_certificate(_crypto.FILETYPE_ASN1,
                                            _crypto.load_certificate(_crypto.FILETYPE_PEM,
                                                                     f.read()))
    return CertHash(hashlib.sha256(der_cert).digest())


if TypeDecorator:
    class SqlCertHash(TypeDecorator):
        impl = String(60)

        def process_bind_param(self, value, dialect):
            return str(CertHash(value))

        def process_result_value(self, value, dialect):
            return CertHash(value)

        def __init__(self, *args, **kwargs):
            super().__init__(50, *args, **kwargs)
            

def get_or_create(session, model, filter_by, defaults = {}):
    primary_key = tuple(map(lambda x:x.name, inspect(model).primary_key)
                        )
    primary_key_set = set(primary_key)
    filter_by_set = set(filter_by.keys())
    if filter_by_set == primary_key_set:
        primary_key_values = tuple(map(lambda x: filter_by.get(x), primary_key))
        inst = session.query(model).get(primary_key_values)
    else: inst = session.query(model).filter_by(**filter_by).first()
    if inst: return inst
    d = filter_by.copy()
    d.update(defaults)
    inst = model(**d)
    session.add(inst)
    return inst

@contextlib.contextmanager
def entanglement_logs_disabled():
    l = logging.getLogger('hadron.entanglement')
    oldlevel = l.level
    l.setLevel(logging.CRITICAL+1)
    yield
    l.setLevel(oldlevel)
    
