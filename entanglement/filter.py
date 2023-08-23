# Copyright (C)  2022, 2023, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import dataclasses
import collections.abc
from .network import SyncDestinationBase, SyncDestination
from .interface import Synchronizable
from .memory import key_for

class AllSentItemsSet(collections.abc.MutableSet):

    __slots__ = ('set',)
    def add(self,o):
        self.set.add((o.sync_type, key_for(o)))

    def discard(self, o):
        self.set.discard((o.sync_type, key_for(o)))

    def __contains__(self, o):
        return (o.sync_type, key_for(o)) in self.set

    def __iter__(self):
        raise NotImplementedError

    def __len__(self):
        return len(self.set)

    def __init__(self):
        self.set = set()
        

class FilteredMixin:

    def should_send(self, obj, old_filters=False, **kwargs):
        operation = kwargs.get('operation', 'sync')
        if operation == 'delete':
            if obj in self.all_sent_objects:
                self.all_sent_objects.remove(obj)
                return True
            
        send = None
        for f in self.filter_entries if not old_filters else self.old_filter_entries:
            res = f.should_send(obj, **kwargs)
            if res is True:
                send = True
            elif res is False:
                send = False
                break
        # None is neutral
        if send is None: send = self.filter_default_permissive
        if send:
            if operation == 'sync':
                self.all_sent_objects.add(obj)
            return True
        if operation == 'sync' and obj in self.all_sent_objects:
            manager = kwargs.get('manager')
            if manager: synthesize_withdrawl(obj, manager=manager, destination=self)
        return False
    
    def withdraw_objects(self, manager):
        for o in self._find_objects():
            if not o in self.all_sent_objects: continue
            if not self.should_send(o, manager=manager, operation='sync'):
                synthesize_withdrawl(o, manager=manager, destination=self)

        self.old_filter_entries = tuple(self.filter_entries)

    def _find_all_objects(self):
        filter_entries = self.filter_entries
        already_returned = set()
        for f in filter_entries:
            try:
                for o in f.all_objects():
                    if id(o) in already_returned: continue
                    already_returned.add(id(o))
                    yield o
            except (AttributeError, NotImplementedError): continue
            if not f.dynamic:
                # The filter promises  that it will not pass any object not included in all_objects
                break


    def add_filter(self, filter):
        if not self.old_filter_entries: self.old_filter_entries = tuple(self.filter_entries)
        assert filter not in self.filter_entries
        assert not filter.attachment, "This filter is already attached"
        filter.attachment = self
        self.filter_entries.append(filter)
        self.filter_entries.sort(key=lambda f:f.order)
        
    def remove_filter(self, filter):
        assert filter in self.filter_entries
        if not self.old_filter_entries: self.old_filter_entries = tuple(self.filter_entries)
        self.filter_entries.remove(filter)
        
        
class FilteredDestinationMixin(FilteredMixin, SyncDestinationBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filter_entries = []
        self.old_filter_entries = None
        self.all_sent_objects = AllSentItemsSet()

    async def send_initial_objects(self, manager):
        for o in self._find_all_objects():
            if not o in self.all_sent_objects:
                manager.synchronize(o, destinations=[self])

    def potential_new_objects(self, manager, objects):
        for o in objects:
            if o not  in self.all_sent_objects:
                manager.synchronize(o, destinations=[self])

    def potential_withdrawn_objects(self, manager, objects):
        for o in objects:
            if o in self.all_sent_objects:
                if not self.should_send(self, o, manager=manager, operation='sync'):
                    manager.synchronize(o, operation='delete', destinations=[self])
    

@dataclasses.dataclass
class FilterBase:

    '''An abstract class representing a filter.
    :meth:`should_send` is called for each object.

    Filters also serve as a source of objects.  :meth:`all_objects` can be implemented to provide a set of objects to consider sending on initial connect.

    '''
    
#: Smaller ordered filters are processed first
    order:int = dataclasses.field(default=100, compare=True)
    attachment: FilteredMixin  = dataclasses.field(compare=False, init=False)

    #: If true, this filter has a set of objects for which it will return True or None  that are not statically known.  In other words :meth:`all_objects` even if implemented cannot return a superset of  objects for which the filter does not return *False*.
    dynamic: bool = True

    def __init__(self, order=None):
        if order is not None: self.order = order
        self.attachment = None
        


    def __hash__(self):
        return id(self.attachment)+self.order

    def __eq__(self, other):
        return self is other


    def all_objects(self):
        '''
        When a new client connects, a destination needs to flood objects to them.  Similarly, in most cases when a filter is added, objects need to be flooded.  This method returns a set of objects that the filter potentially will pass (return *None* or *True*).  If :var:`dynamic` is true then the filter must return *False* for any object that all_objects does not generate.
        '''
        raise NotImplementedError

    def should_send(self, o, **info):
        '''
        An abstract method that should be overridden. Called by :meth:`FilteredMixin.should_send` for each object.  Returns are handled as follows:

        True
            This filter recommends sending.  All other filters are still called.

        False
            The object is not sent even if previous filters have recommended sending.

        None
            The filter is neutral on sending the object.
        '''
        return False

class Filter(FilterBase):

    def __init__(self, filter, *,
                 store=None, type=None,
                 registry=None,
                 **kwargs):
        super().__init__(**kwargs)
        self.filter = filter
        self.store = store
        self.type = type
        self.registry = registry

    def all_objects(self):
        if self.store: yield from self.store.values()
        elif self.registry and hasattr(self.registry, 'stores_by_class'):
            for store in self.registry.stores_by_class.values():
                yield from store.values()
        else: raise NotImplementedError

    def should_send(self, o, **info):
        if self.type and not isinstance(o, self.type):
            return None
        if self.registry  and o.sync_type not in self.registry.registry:
            return None
        return self.filter(o)

class SyncOwnerFilter(FilterBase):

    def should_send(self, o, **info):
        if o.sync_type == 'SyncOwner':
            return True

    def __init__(self, registry=None, *, order=1):
        super().__init__(order)
        self.registry = registry

    def all_objects(self):
        if not self.registry: raise NotImplementedError
        for o in self.registry.store_for_class(self.registry.registry['SyncOwner']).values():
            yield o
            
        
class FilteredSyncDestination(SyncDestination, FilteredDestinationMixin): pass

def synthesize_withdrawl(o, manager, destination):
    manager.synchronize(o, attributes_to_sync=o.sync_primary_keys, destinations=[destination], operation='delete')
    
