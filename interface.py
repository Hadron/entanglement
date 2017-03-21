#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, types


class SynchronizableMeta(type):
    '''A metaclass for capturing Synchronizable classes.  In python3.6, no metaclass will be needed; __init__subclass will be sufficient.'''

    def __init__(cls, name, bases, _dict):
        if cls.sync_registry:
            if not isinstance(cls.sync_registry, SyncRegistry):
                raise TypeError("Class {cls} sets sync_registry to something that is not a SyncRegistry".format(cls = cls.__name__))
            cls.sync_registry.register_syncable(cls.sync_type, cls)

    def __new__(cls, name, bases, ns):
        sync_meta = {}
        def default_encoder(propname):
            return lambda obj: getattr(obj, propname, None)
        for k,v in list(ns.items()):
            if isinstance(v, sync_property):
                sync_meta[k] = v
                v.declaring_class = name
                if v.wraps:
                    ns[k] = v.wraps
                else: del ns[k]
                del v.wraps
                if not v.encoderfn: v.encoderfn = default_encoder(k)
                if not v.decoderfn: v.decoderfn = lambda obj, val: val
        ns['_sync_meta'] = sync_meta
        return type.__new__(cls, name, bases, ns)

    sync_registry = property(doc = "A registry of classes that this Syncable belongs to.  Registries can be associated with a connection; only classes in registries associated with a connection are permitted to be synchronized over that connection")

    @sync_registry.getter
    def sync_registry(inst):
        for c in inst.__mro__:
            if 'sync_registry' in c.__dict__: return c.__dict__['sync_registry']
        return None


    @property
    def _sync_properties(cls):
        '''Returns a mapping of key in a sync representation to a sync property.  Unlike the _sync_meta key, this mapping combines entries from base classes.
        '''
        if '_sync_properties_cache' in cls.__dict__:
            return cls._sync_properties_cache
        d = {}
        for c in cls.__mro__:
            if hasattr(c,'_sync_meta'):
                for k,v in c._sync_meta.items():
                    if k not in d: d[k] = v
        cls._sync_properties_cache = types.MappingProxyType(d)
        return cls._sync_properties_cache

class sync_property:

    '''Represents a property that can be synchronized.
    Simplest usage:
        color = sync_property()

    Color will be read and written in the synchronization of the
    object using its default JSON representation

        manager =sync_property()
        @manager.encoder
        def encoder(obj):
            return obj.manager_id
        @manager.decoder
            def decoder(obj, value):
            return Manager.get_by_id(value)

    '''

    def __init__(self, wraps = None, doc = None, *,
                 encoder = None,
                 decoder = None,
                 constructor = False):
        self.wraps = wraps
        self.encoderfn =  encoder
        self.decoderfn = decoder
        self.constructor = constructor
        self.__doc__ = doc
        if wraps and not doc:
            if hasattr(wraps, '__doc__'):
                self.__doc__ = wraps.__doc__

    def encoder(self, encoderfn):
        "If encoderfn(instance) returns non-None, then the value returned will be encoded for this property"
        self.encoderfn = encoderfn

    def decoder(self, decoderfn):
        "If the property is specified, then decoderfn(obj, value_from_json) will be called.  If it returns non-None, then setattr(obj, prop_name, return_value) will be called. If constructor is not False, obj may be None"
        self.decoderfn = decoderfn

class Synchronizable( metaclass = SynchronizableMeta):

    def to_sync(self):
        '''Return a dictionary containing the attributes of self that should be synchronized.'''
        d = {}
        for k,v in self.__class__._sync_properties.items():
            try: val = v.encoderfn(self)
            except BaseException as e:
                raise ValueError("Failed encoding {} using encoder from class {}".format(k, v.declaring_class)) from e
            if val is not None: d[k] = val
        return d

    @classmethod
    def _sync_pkeys_dict(cls, msg):
        '''return a dictionary containing the decoded value of all of the primary keys in an incoming sync representation
        '''
        d = {}
        if not set(cls.sync_primary_keys).issubset(msg.keys()):
            raise SyncBadEncodingError("Encoding must contain primary keys: {}".format(cls.sync_primary_keys))
        for k in cls.sync_primary_keys:
            d[k] = cls._sync_properties[k].decoderfn(None, msg[k])
        return d

    @classmethod
    def _sync_construct(cls, msg):
        '''Return a new object of cls consistent with msg that can be filled in with the rest of the contents of msg.

        For many classes, this could simply call the class.  It could also be overridden to look up an existing instance of a class in a database.  The default implementation calls the constructor with sync properties where the constructor argument to the property is set.  If constructor is set to a number, that ordinal index is used.  If True, the property name is used as a constructor keyword.
        '''
        cprops = dict(filter( lambda x: bool(x[1].constructor), cls._sync_properties.items()))
        maxord = 0
        for p in cprops.values():
            # sadly isinstance(True, int) is True
            #We assume any constructor value that is not True is an int
            if p.constructor is not True:maxord = max(maxord,p.constructor)
        args = [None] * maxord
        kwargs = {}
        for k,v in cprops.items():
            if k in msg:
                try:
                    if v.constructor is not True: #it's an int
                        args[v.constructor-1] = v.decoderfn(None, msg[k])
                    else: kwargs[k] = v.decoderfn(None, msg[k])
                    del msg[k]
                except Exception as e:
                    raise SyncBadEncodingError("Error decoding {}".format(k), msg = msg) from e
        return cls(*args, **kwargs)


    @classmethod
    def sync_receive(cls, msg, **kwargs):
        obj = cls._sync_construct(msg)
        for k, v in msg.items():
            if k not in cls._sync_properties:
                if k.startswith('_'): continue
                raise SyncBadEncodingError('{} unknown property in sync encoding'.format(k), msg = msg)
            try:
                setattr(obj, k, cls._sync_properties[k].decoderfn(obj, v))
            except Exception as e:
                raise SyncBadEncodingError("Failed to decode {}".format(k),
                                           msg = msg) from e
        return obj

    @classmethod
    def sync_should_listen(self, msg):
        '''Return True or raise SynchronizationUnauthorized'''
        return True



    def __hash__(self):
        '''Hash all the primary keys.'''
        if self.__class__.sync_primary_keys is Unique:
            return id(self)
        return sum(map(lambda x: getattr(self, x).__hash__(), self.__class__.sync_primary_keys))

    def __eq__(self, other):
        '''Return true if the primary keys of self match the primary keys of other'''
        if self.__class__.sync_primary_keys is Unique:
            return self is other
        if self.__class__ != other.__class__: return NotImplemented
        return all(map(lambda k: getattr(self,k).__eq__(getattr(other,k)), self.__class__.sync_primary_keys))

    sync_primary_keys = property(doc = "tuple of attributes comprising  primary keys")
    @sync_primary_keys.getter
    def sync_primary_keys(self):
        raise NotImplementedError

    class _Sync_type:
        "The type of object being synchronized"

        def __get__(self, instance, owner):
            return owner.__name__

    sync_type = _Sync_type()
    del _Sync_type

Unique = "Unique" #Constant indicating that a synchronizable is not combinable with any other instance



class SyncRegistry:

    '''A registry of Syncable classes.  A connection may accept
    synchronization from one or more registries.  A Syncable typically
    belongs to one registry.  A registry can be thought of as a schema of
    related objects implementing some related synchronizable interface.'''

    def __init__(self):
        self.registry = {}

    def register_syncable(self, type_name, cls):
        if type_name in self.registry:
            raise ValueError("`{} is already registered in this registry.".format(type_name))
        self.registry[type_name] = cls

    def should_listen(self, msg, cls):
        "Authorization check as well as a check on whether we want to ignore the class for some reason"
        return True

    def sync_receive(self, object, **kwargs):
        "Called after the object is constructed. May do nothing, may arrange to merge into a database, etc."
        pass

    def exception_receiving(self, exc, **kwargs):
        "Called when an exception happens constructing an object. or in registry.sync_receive.  The manager will make sure the exception is logged."
        pass



error_registry = SyncRegistry()

class SyncError(RuntimeError, Synchronizable):

    args = sync_property()
    context = sync_property()
    @context.encoder
    def get_context(obj):
        if obj.__context__: return str(obj.__context__)
    @context.decoder
    def setcontext(obj, value): pass

    cause = sync_property()
    @cause.encoder
    def get_cause(obj):
        if obj.__cause__: return str(obj.__cause__)
    @cause.decoder
    def ignore(obj, value): pass

    sync_registry = error_registry
    sync_primary_keys = Unique

    def to_sync(selff):
        d = super().to_sync()
        d['_sync_is_error'] = True
        return d

class UnregisteredSyncClass(SyncError): pass

class WrongSyncDestination(SyncError):

    def __init__(msg = None, *args, dest = None, got_hash = None,
                 **kwargs):
        if not msg and dest:
            msg = "Incorrect certificate hash received from connection to {dest}".format(dest)
            if got_hash: msg = msg + " (got {got})".format(got = got_hash)
        super().__init__(msg, *args, **kwargs)

class SyncBadEncodingError(SyncError):

    msg = sync_property(constructor =True)

    def __init__(self, *args, msg = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.msg = msg
