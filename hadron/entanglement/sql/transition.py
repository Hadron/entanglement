#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


from ..transition import TransitionTrackerMixin
from . import SqlSynchronizable
from sqlalchemy import inspect
import sqlalchemy.exc

class SqlTransitionTrackerMixin(TransitionTrackerMixin, SqlSynchronizable):


    def store_for_transition(self):
        if self.sync_owner:
            self.sync_owner.destination # Lazy load so we can check in sync_construct
            ins = inspect(self.sync_owner)
            if ins.session:
                if self.sync_owner.destination: ins.session.expunge(self.sync_owner.destination)
                ins.session.expunge(self.sync_owner)
        ins = inspect(self)
        if ins.session:
            ins.session.expunge(self)
        return super().store_for_transition()



    def transition_modified_attrs(self):
        inspect_inst = inspect(self)
        return frozenset(self.__class__._sync_properties.keys()) - frozenset(inspect_inst.unmodified)
