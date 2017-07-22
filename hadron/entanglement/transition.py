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

    '''Add support for transitions.  Transitions are intended to notify
    SyncManagers about fast-moving operations like moving graphical
    elements.

    Transitions are intended to inform other SyncManagers of a possible
    change as it happens.  When an object exits transition, all parties
    are required to restore state as if the transition had not happened.
    If the transitioning SyncManager wishes to propose the final state of
    the transition as permanent, a sync or forward needs to be
    synchronized.

    This class is typically used by a Mixin that adapts transition
    tracking to a particular persistence technology.  The method
    resolution order typically needs to be TransitionTrackerMixin's
    sync_construct, to look for objects in transition, followed by the
    persistence layer's sync_construct, followed by Synchronizable's
    sync_construct for totally new objects.  The easiest way to
    achieve that is to have a persistence layer Mixin.  For example,
    SqlTransitionTrackerMixin is declared as follows:

        class SqlTransitionTrackerMixin(TransitionTrackerMixin, SqlSynchronizable):

    It would be somewhat tricky for a persistence layer to be entirely
    wrapping TransitionTrackerMixin's sync_construct because the
    wrapping code would need to distinguish returns from
    TransitionTrackerMixin to see whether they were objects in
    transition or whether they were new objects that might erroneously
    shadow persisted objects.

    Not all the nodes that get to review and approve forwards and
    syncs may process transitions.  As a result, objects in transition
    may not be as trustworthy as other objects.  To maintain security,
    parties other than the SyncManager initiating a transition SHOULD
    NOT use a transitioned object as a basis for sync or forward
    operations.  If a transitioned object is used as a basis for sync
    or forward, all changes between the most recent synchronized
    version and the transitioned object MUST be evaluated and approved
    by the party making the update.  The simplest way of guaranteeing
    this is to override remove_from_transition and make sure that when
    remove_from_transition is called, future lookups and uses of the
    object get the most recently persisted version.  However, the
    instance on which remove_from_transition is called needs to remain
    with the transitioned values so that the following pattern works
    for the party initiating transition:

        while moving: 
            # updates values on obj
            obj.perform_transition()
        # We like the final state
        obj.remove_from_transition()
        persistence_layer.save(obj) #forwards or syncs

    Along the same lines, subclasses MUST guarantee that when
    TransitionTrackerMixin.sync_construct calls remove_from_transition
    and then later calls the superclass sync_construct, that the most
    recently persisted object is returned from the superclass rather
    than the object under transition. SqlTransitionTrackerMixin meets
    this requirement.

    '''


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
