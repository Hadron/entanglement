#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


import logging, uuid, weakref
from . import interface, sync_property, sql

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
    instance on which remove_from_transition is called typically retains transitioned values.

    The following usage pattern is typical; this is presented in
    pseudo-code.  See SqlTransitionTrackerMixin for a more concrete
    example.  If the originator does choose to make the transition
    persistent, the transition_id SHOULD be preserved in the forward
    or sync to avoid a BrokenTransition error.

        while moving: 
            # updates values on obj
            obj.perform_transition()
        # We like the final state
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

    transition_id = sync_property(encoder = sql.encoders.uuid_encoder,
                                  decoder = sql.encoders.uuid_decoder)
    
    @classmethod
    def sync_construct(cls, msg, **info):
        objs = cls.transition_tracked_objects
        sender = info['sender']
        response_for = info.get('response_for', None)
        operation = info['operation']
        manager = info['manager']
        try:
            primary_keys = cls.sync_primary_keys
            pkey_values = tuple(map( lambda x: cls._sync_properties[x].decoderfn(msg, x, msg[x]), primary_keys))
        except KeyError:
            return super().sync_construct(msg, **info)
        key = pkey_values
        if key not in objs:
            return super().sync_construct(msg, **info)
        obj = objs[key]
        same_transition = obj.same_transition(msg, **info)
        if operation != 'transition' or (not same_transition):
            if same_transition:
                # Exit transition via operation from transition initiator
                if response_for: response_for.merge(obj._transition_response_for)
                # If we've made it to object owner, drop the
                # transition_id so it's not present in a sync
                if obj.sync_is_local:
                    try: del msg['transition_id']
                    except KeyError: pass
            elif obj._transition_response_for: #Not same transition
                obj._broken_transition(manager)
            obj.remove_from_transition()
            return super().sync_construct(msg, **info)
        obj._transition_operation_count += 1
        return obj

    def same_transition(self, msg, **info):
        sender = info.get('sender', None)
        return (str(self.transition_id) == msg.get('transition_id', None) )  \
               and (self._transition_destination == sender)

    def _broken_transition(self, manager):
        try:
            self._transition_response_for(BrokenTransition(self))
            manager.synchronize(BrokenTransition(self),
                                                operation = 'error',
                                                destinations = [self._transition_destination],
                                                response_for = self._transition_response_for)
        except Exception:
            logger.exception('Sending out BrokenTransition error')

    def perform_transition(self, manager):
        first_transition = getattr(self, 'transition_id', None) is None
        if first_transition:
            self.transition_id = uuid.uuid4()
        if self.sync_is_local :
            destinations = None
        else:
            dest_hash = self.sync_owner.destination.dest_hash
            destinations = [manager.dest_by_hash(dest_hash)]
        attrs =self.transition_modified_attrs()
        if attrs is not None:
            attrs = frozenset(self.sync_primary_keys) | attrs
        fut = manager.synchronize(self,
                            destinations = destinations,
                            attributes_to_sync = attrs,
                                      operation = 'transition',
                                      response = first_transition)
        if fut:
                # we store the future directly so that when we remove from transition we can notify locally
            self._transition_future = fut
            # Adding this callback removes the object from transition in case the transition is broken but we don't get to see the breaking message, only the error or even less likely a no response
            fut.add_done_callback(self._remove_from_transition_cb)
        self._transition_operation_count = 1+getattr(self, '_transition_operation_count', 0)
        self.store_for_transition(manager = manager)
        return self._transition_future
        
    def store_for_transition(self, response_for = None, sender = None, manager = None):
        key = self.transition_key()
        if getattr(self, 'transition_id', None) is None:
            raise interface.SyncBadEncodingError('Objects in transition must have a transition_id')
        objs = self.transition_tracked_objects
        if key in objs:
            obj = objs[key]
        else: obj = None
        if obj and obj.transition_id != self.transition_id:
            if manager: obj._broken_transition(manager)
            obj.remove_from_transition()
            obj = None
        if not obj:
            logger.debug("Starting transition for {}".format(self))
            self._transition_operation_count = 0
            self._transition_response_for = response_for
            self._transition_destination = sender
        objs[key] = self
        
            
    def remove_from_transition(self):
        key = self.transition_key()
        objs = self.transition_tracked_objects
        if key in objs:
            o = objs[key]
            #if o._transition_response_for: o._transition_response_for.no_response()
            del o._transition_response_for
            del o._transition_destination
            if hasattr(o,'_transition_future'):
                if not o._transition_future.done():
                    o._transition_future.set_exception(BrokenTransition(o))
                del o._transition_future
            del o.transition_id
            del objs[key]
            logger.debug("Removing {} from transition; {} operations".format(self, o._transition_operation_count))

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

    def _remove_from_transition_cb(self, future):
        if future.exception() is None:
            # If we're local then it's expected that we get no response.
            if self.sync_is_local and future.result() is None: return
        return self.remove_from_transition()

class BrokenTransition(interface.SyncError):

    transition_id = sync_property(encoder = sql.encoders.uuid_encoder,
                                  decoder = sql.encoders.uuid_decoder)

    def __init__(self, obj = None):
        msg = None
        if obj:
            self.transition_id = obj.transition_id
            pkeys_dict = obj.to_sync(attributes = obj.sync_primary_keys)
            msg = "Broken transition {id} of {t} with keys {k}".format(
                id = obj.transition_id,
                t = obj.sync_type,
                k = pkeys_dict)
        super().__init__(msg)


__all__ = ['TransitionTrackerMixin', 'BrokenTransition']
