#!/usr/bin/python3
# Copyright (C) 2017, 2018, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


from ..transition import TransitionTrackerMixin
from . import SqlSynchronizable
from ..interface import SyncError
from sqlalchemy import inspect
import sqlalchemy.exc
from sqlalchemy.orm import CompositeProperty

class SqlTransitionTrackerMixin(TransitionTrackerMixin, SqlSynchronizable):

    '''

    A TransitionTrackerMixin for SqlSynchronizables.  Typical usage looks like

        obj = session.query(...).one()
        result_future = None
        while desire_to_transition:
            obj.attr = transitioned_value
            fut = obj.perform_transition(manager)
            # now obj is detached from session.  Also, on first perform_transition, fut is set
            if not result_future: result_future = fut
            if result_future.done(): break #Transition broken
            #Loop some more performing more transitions
        if not result_future.done()
            # We like the results
            session.add(obj)
            session.sync_commit() # if obj is non local
            session.commit() # in case it is local
        await result_future
        # result_future returns None for local objects, the updated object for nonlocal objects, and raises BrokenTransition if our transition is broken.

    '''
    
    def _remove_from_session(self):
        state = inspect(self)
        if not state.session:
            return
        if hasattr(state.session, 'sync_dirty') and self in (x[0] for x in state.session.sync_dirty):
            raise DirtyTransitionError("You cannot transition a dirty object.  If a flush is not obvious, consider disabling auto_flush")
        with state.session.no_autoflush:
            if self.sync_owner:
                ins = inspect(self.sync_owner)
                if ins.session:
                    ins.session.expunge(self.sync_owner)
            state.session.expunge(self)

    def store_for_transition(self, *args, **kwargs):
        self._remove_from_session()
        #Invalidate any composits so they can be recreated
        self._invalidate_composits()
        return super().store_for_transition(*args, **kwargs)

    def perform_transition(self, *args):
        "In an SQL synchronizable, performing transition removes an object from any session."
        self._remove_from_session()
        return super().perform_transition(*args)



    def transition_modified_attrs(self):
        inspect_inst = inspect(self)
        return frozenset(self.__class__._sync_properties.keys()) - frozenset(inspect_inst.unmodified)

    def _invalidate_composits(self):
        "Invalidate any CompositProperties.  This breaks the abstractions somewhat because outside of a session there's no good way to do this."
        ins = inspect(self)
        for a in ins.mapper.attrs.values():
            if isinstance(a, CompositeProperty):
                try: del self.__dict__[a.key]
                except KeyError: pass
                

class DirtyTransitionError(SyncError): pass
