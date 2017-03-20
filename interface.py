#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio


class SynchronizableMeta(type):
    '''A metaclass for capturing Synchronizable classes.  In python3.6, no metaclass will be needed; __init__subclass will be sufficient.'''

    def __init__(cls, name, bases, _dict):
        if cls.sync_registry:
            if not isinstance(cls.sync_registry, SyncRegistry):
                raise TypeError("Class {cls} sets sync_registry to something that is not a SyncRegistry".format(cls = cls.__name__))
            cls.sync_registry.register_syncable(cls.sync_type, cls)

    sync_registry = property(doc = "A registry of classes that this Syncable belongs to.  Registries can be associated with a connection; only classes in registries associated with a connection are permitted to be synchronized over that connection")

    @sync_registry.getter
    def sync_registry(inst): return inst.__dict__.get('sync_registry', None)
    
class Synchronizable( metaclass = SynchronizableMeta):

    def to_sync(self):
        '''Return a dictionary containing the attributes of self that should be synchronized.'''
        raise NotImplementedError

    @classmethod
    def sync_receive(self, msg):
        raise NotImplementedError()

    @classmethod
    def sync_should_listen(self, msg):
        '''Return True or raise SynchronizationUnauthorized'''
        return True
    
    def __hash__(self):
        '''Hash all the primary keys.'''
        return sum(map(lambda x: getattr(self, x).__hash__(), self.__class__.sync_primary_keys))

    def __eq__(self, other):
        '''Return true if the primary keys of self match the primary keys of other'''
        return (self.__class__ == other.__class__) and \
            ball(map(lambda k: getattr(self,k).__eq__(getattr(other,k)), self.__class__.sync_primary_keys))

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

class SyncError(RuntimeError): pass

class WrongSyncDestination(SyncError):

    def __init__(msg = None, *args, dest = None, got_hash = None,
                 **kwargs):
        if not msg and dest:
            msg = "Incorrect certificate hash received from connection to {dest}".format(dest)
            if got_hash: msg = msg + " (got {got})".format(got = got_hash)
        super().__init__(msg, *args, **kwargs)
        
