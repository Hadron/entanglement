# Copyright (C)  2022, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations

import asyncio, dataclasses, typing, uuid
import collections.abc
from abc import abstractmethod
from .interface import *
from .interface import SyncBadOwner, SyncBadEncodingError
from .util import DestHash, memoproperty
from . import operations

def key_for(s):
    keys = s.sync_primary_keys
    assert isinstance(keys, tuple)
    properties = s.__class__._sync_properties
    res = []
    for k in keys:
        property = properties[k]
        v = getattr(s, k) # raises if absent
        res.append(v)
    if len(res) == 1:
        return res[0]
    return tuple(res)


class AbstractSyncStore(collections.abc.Mapping):


    @staticmethod
    def key_from_msg(cls: typing.Type[Synchronizable], msg):
        keys = cls.sync_primary_keys
        properties = cls._sync_properties
        res = []
        for k in keys:
            property = properties[k]
            if k not in msg: return None
            res.append(property._decode_value(msg[k]))
        if len(res) == 1:
            return res[0]
        return tuple(res)

    def add(self, s):
        self.store[key_for(s)] = s

    def __getitem__(self, k):
        try:
            if isinstance(k,Synchronizable): hash(k)
        except TypeError:
            return self.store[key_for(k)]
        if k in self.store: return self.store[k]
        if isinstance(k, Synchronizable):
            return self.store[key_for(k)]
        raise KeyError

    def __delitem__(self, k):
        if k in self.store: del self.store[k]
        elif  isinstance(k, Synchronizable):
            del self.store[key_for(k)]

    def __len__(self):
        return len(self.store)

    def __iter__(self):
        return iter(self.store)
    
    def remove(self, s:Synchronizable):
        del self.store[key_for(s)]

    def __contains__(self, o):
        if isinstance(o, Synchronizable):
            return super().__contains__(key_for(o))
        else: return super().__contains__(o)

    @classmethod
    @abstractmethod
    def store_factory(cls):
        '''Return a mutable mapping such as a dict to be used for storing Synchronizables of a given class.
        '''
        raise NotImplementedError

    def __init__(self):
        self.store = self.store_factory()

class SyncStore(AbstractSyncStore):

    @classmethod
    def store_factory(cls):
        return dict()

class StoreInSyncStoreMixin(Synchronizable):

    #: The class to store this object with.  If None, this object gets its own store.  Objects should be stored together when they are subclasses that have overlapping primary keys and using code does not want to know the exact type to do a lookup.
    sync_store_with = None

    _sync_owner: uuid.UUID = sync_property()

    @classmethod
    def sync_construct(cls, msg, **info):
        try:
            sync_owner_id = uuid.UUID(msg['_sync_owner'])
        except KeyError:
            raise SyncBadEncodingError('_sync_owner is required')
        registry = info['registry']
        owner_store = registry.store_for_class(SyncOwner)
        try: owner = owner_store[sync_owner_id]
        except KeyError: raise SyncBadOwner('Must synchronize owner before object')
        sender = info.get('sender')
        if sender and owner.dest_hash == sender.dest_hash:
            owner.destination = sender
        store = registry.store_for_class(cls)
        obj = store.get(store.key_from_msg(cls, msg))
        if obj is None:
            obj = super().sync_construct(msg, **info)
        obj.sync_owner = owner
        return obj

class SyncStoreRegistry(SyncRegistry):

    #: What class is used for stores
    sync_store_factory: typing.Type[AbstractSyncStore] = SyncStore

    def __init__(self, sync_store_factory=None):
        if sync_store_factory: self.sync_store_factory = sync_store_factory
        self.stores_by_class = {}
        super().__init__()
        self.register_operation('sync', operations.sync_operation)
        self.register_operation('forward', operations.forward_operation)
        self.register_operation('create', operations.create_operation)
        self.register_operation('delete', operations.delete_operation)
        

    def store_for_class(self, cls):
        store_cls = cls.sync_store_with or cls
        try: store = self.stores_by_class[store_cls]
        except KeyError:
            store = self.sync_store_factory()
            self.stores_by_class[store_cls] = store
        return store

    def add_to_store(self, obj):
        store = self.store_for_class(type(obj))
        store.add(obj)

    def get_or_create(self, _type, *keys):
        assert len(keys) == len(_type.sync_primary_keys)
        if len(keys) == 1:
            lookup = keys[0]
        else: lookup = keys
        store = self.store_for_class(_type)
        try: return store[lookup]
        except KeyError:
            obj = _type(
                **{k:v for k,v in zip(_type.sync_primary_keys, keys)})
            store.add(obj)
            return obj
        
        
    def remove_from_store(self, obj):
        store = self.store_for_class(type(obj))
        store.remove(obj)

    def store_synchronize(self, obj, **kwargs):
        if getattr(obj, '_sync_owner', None) is None:
            obj._sync_owner = self.local_owner._sync_owner
        self.add_to_store(obj)
        if self.manager:
            return self.manager.synchronize(obj, **kwargs)

    def incoming_sync(self, obj, **info):
        assert not obj.sync_is_local
        self.add_to_store(obj)

    def incoming_delete(self, obj, **info):
        sender = info['sender']
        if sender.dest_hash == obj.sync_owner.dest_hash:
            try:
                self.remove_from_store(obj)
            except KeyError: pass

    @memoproperty
    def local_owner(self):
        owner_store = self.store_for_class(SyncOwner)
        for o in owner_store.values():
            if o.sync_is_local: return o
        owner = SyncOwner()
        self.store_synchronize(owner)
        return owner
    
@dataclasses.dataclass
class SyncOwner(StoreInSyncStoreMixin):

    _sync_owner: uuid.UUID = sync_property(dataclasses.field(default_factory=uuid.uuid4))
    dest_hash: typing.Optional[DestHash] = None

    sync_primary_keys = ('_sync_owner', )
    sync_priority = 2    

    @property
    def id(self) -> uuid.UUID: return self._sync_owner

    @property
    def sync_is_local(self):
        return self.dest_hash is None

    @classmethod
    def sync_construct(cls, msg, registry, **info):
        store = registry.store_for_class(SyncOwner)
        owner = store.get(store.key_from_msg(cls, msg))
        sender = info.get('sender')
        
        if (owner and sender) and owner.dest_hash != sender.dest_hash:
            raise SyncBadOwner(f'{sender.dest_hash} sent us a {cls.__name__} that belongs to {owner.dest_hash}')
        if owner is None:
            owner = Synchronizable.sync_construct.__func__(cls, msg, registry=registry, **info)
            owner.dest_hash = sender.dest_hash
        return owner
    
