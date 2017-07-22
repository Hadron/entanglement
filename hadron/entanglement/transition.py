#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


import logging, weakref
from . import interface
from .protocol import logger



transition_objects = weakref.WeakKeyDictionary()

class TransitionTrackerMixin (interface.Synchronizable):

    "Tracks entanglement Synchronizable objects for fast moving changes like transitions"

    class transition_tracked_objects:
        def __get__(self, inst, cls):
            "Return a dictionary mapping primary keys to objects.  Subclasses may override if it makes finding the right object easier in polymorphic situations.  It's a requirement that the tuple of primary keys be unique across any classes sharing a transition_tracked_objects dictionary."
            global transition_objects
            return transition_objects.setdefault(cls, {})

    transition_tracked_objects = transition_tracked_objects()

    @classmethod
    def sync_construct(cls, msg, **info):
        objs = cls.transition_tracked_objects
        sender = info['sender']
        operation = info['operation']
        try:
            primary_keys = cls.sync_primary_keys
            pkey_values = tuple(map( lambda x: cls._sync_properties[x].decoderfn(msg, x, msg[x]), primary_keys))
        except KeyError:
            return super().sync_construct(msg, **info)
        key = pkey_values
        if key not in objs:
            return super().sync_construct(msg, **info)
        obj = objs[key]
        if operation != 'transition':
            obj.remove_from_transition()
            return super().sync_construct(msg, **info)
        obj._transition_operation_count += 1
        return obj

        
    def perform_transition(self, manager):
        if self.sync_is_local :
            destinations = None
        else:
            cert_hash = self.sync_owner.destination.cert_hash
            destinations = [manager.dest_by_cert_hash(cert_hash)]
        attrs =self.transition_modified_attrs()
        if attrs is not None:
            attrs = frozenset(self.sync_primary_keys) | attrs
        manager.synchronize(self,
                            destinations = destinations,
                            attributes_to_sync = attrs,
                            operation = 'transition')
        self.store_for_transition()
        self._transition_operation_count += 1
        
    def store_for_transition(self):
        key = self.transition_key()
        objs = self.transition_tracked_objects
        if key not in objs:
            logger.debug("Starting transition for {}".format(self))
            self._transition_operation_count = 0
        objs[key] = self
        
            
    def remove_from_transition(self):
        key = self.transition_key()
        objs = self.transition_tracked_objects
        if key in objs:
            del objs[key]
            logger.debug("Removing {} from transition; {} operations".format(self, self._transition_operation_count))

    def transition_key(self):
        "Return the tuple under which an object is stored in transition_tracked_objects; do not override because this code is duplicated in sync_constructed"
        primary_keys = self.sync_primary_keys
        pkey_values = tuple(map( lambda x: getattr(self, x), primary_keys))
        return  pkey_values

    @classmethod
    def get_from_transition(cls, *key):
        "Return an object under transition based on its key else None"
        if len(key) == 1 and isinstance(key[0], tuple):
            key = key[0]
        objs = cls.transition_tracked_objects
        return objs.get(key, None)

    def transition_modified_attrs(self):
        "Override to return the set of attributes that have been modified and should be included in the transition message."
        return None
    
