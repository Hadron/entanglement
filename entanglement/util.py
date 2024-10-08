#!/usr/bin/python3
# Copyright (C) 2017, 2018, 2019, 2022, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


import base64, contextlib, functools, hashlib, logging, re, uuid
try:
    from OpenSSL import crypto as _crypto
except ImportError:
    _crypto  = None

try:
    from sqlalchemy.types import TypeDecorator, String
    from sqlalchemy import inspect
except ImportError:
    TypeDecorator = None
    
class DestHash(bytes):
    "represents a hash of a value that uniquely identifies a destination.  The most common DestHash is a CertHash (hash of a DER-encoded X.509 certificate)."

    def __new__(self, hash):
        "Construct from a RFC 6920 URI containing a base64-encoded SHA256 checksum"
        if isinstance(hash, str):
            m = re.match( r'(?:ni://[^/]*/sha-256;)?([-a-zA-Z0-9_=]+)', hash)
            if not m: raise ValueError('unable to parse hash string', hash)
            return bytes.__new__(self, base64.urlsafe_b64decode(m.group(1)))
        return bytes.__new__(self, hash)

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
                return self.__eq__(DestHash(other))
            except: return res

    def __ne__(self, other):
        return not (self == other)
    

    def __hash__(self): return hash(str(self))

    @classmethod
    def from_string(cls, s):
        return cls(hashlib.sha256(bytes(s, 'utf-8')).digest())

    @classmethod
    def from_unix_dest_info(cls, path, pid, uid, gid):
        # We assume the path is not under the control of the attacker. Even so, make this prefix free
        path = path.replace(':','_')
        s = "{p}:{pid}:{uid}:{gid}".format(
            p = path,
            pid = pid,
            gid = gid,
            uid = uid)
        return cls.from_string(s)
    
class CertHash(DestHash):
    
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
    class SqlDestHash(TypeDecorator):
        impl = String(60)

        cache_ok = True

        def process_bind_param(self, value, dialect):
            if value is None: return None
            return str(DestHash(value))

        def process_result_value(self, value, dialect):
            if value is None: return None
            return DestHash(value)

        def __init__(self, *args, **kwargs):
            super().__init__(50, *args, **kwargs)

class GUID(TypeDecorator):
    # Also see:
    # http://docs.sqlalchemy.org/en/latest/core/custom_types.html#backend-agnostic-guid-type
    
    impl = String

    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        else:
            if not isinstance(value, uuid.UUID):
                return uuid.UUID(value).hex
            else:
                return value.hex

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        else:
            return uuid.UUID(value)


def get_or_create(session, model, filter_by, defaults = {}):
    primary_key = tuple(map(lambda x:x.name, inspect(model).primary_key)
                        )
    primary_key_set = set(primary_key)
    filter_by_set = set(filter_by.keys())
    if filter_by_set == primary_key_set:
        primary_key_values = tuple(map(lambda x: filter_by.get(x), primary_key))
        inst = session.get(model, primary_key_values)
    else: inst = session.query(model).filter_by(**filter_by).first()
    if inst: return inst
    d = filter_by.copy()
    d.update(defaults)
    inst = model(**d)
    session.add(inst)
    return inst

@contextlib.contextmanager
def entanglement_logs_disabled():
    l = logging.getLogger('entanglement')
    oldlevel = l.level
    l.setLevel(logging.CRITICAL+1)
    yield
    l.setLevel(oldlevel)
    
class memoproperty:
    "A property that only supports getting and that stores the result the first time on the instance to avoid recomputation"


    def __init__(self, fun):
        functools.update_wrapper(self, fun)
        self.fun = fun
        self.name = fun.__name__

    def __get__(self, instance, owner):
        if instance is None: return self
        #Because we don't define set or del, we should not be called
        #if name is already set on instance.  So if we set name we
        #will be bypassed in the future
        res = self.fun(instance)
        setattr(instance, self.name, res)
        return res
