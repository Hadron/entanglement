# Copyright (C) 2018, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import heapq


class DirtyMember:

    __slots__ = ('obj', 'operation', 'attrs', 'response_for', 'priority')

    def __eq__(self, other):
        return self.obj.sync_compatible(other.obj)

    def __hash__(self):
        return self.obj.sync_hash()

    def __lt__(self, other):
        return self.priority < other.priority
    
    def __repr__(self):
        keydict = {}
        try:
            keys = self.obj.sync_primary_keys
            for k in keys:
                keydict[k] = getattr(self.obj, k, None)
        except Exception: pass
        return "DirtyElement <keys : {k}, obj: {o}, priority: {p}>".format(
            k = keydict,
            o = repr(self.obj),
            p = self.priority)

    def update(self, elt):
        "Returns true if the list needs to be re-heapified"
        obj = elt.obj
        operation = elt.operation
        attrs = elt.attrs
        response_for = elt.response_for
        priority = elt.priority
        heapify = self.priority > priority
        assert self.obj.sync_compatible(obj)
        if attrs:
            attrs = frozenset(attrs)
            if not self.attrs:
                old_attrs = set(self.obj.to_sync().keys())
            else: old_attrs = self.attrs
            for a in old_attrs - attrs:
                try: setattr(obj, a, getattr(self.obj, a))
                except AttributeError: pass
            attrs = attrs | old_attrs
        self.obj = obj
        self.operation = operation
        self.attrs = attrs if attrs else None
        if self.response_for:
            self.response_for.merge(response_for)
        else: self.response_for = response_for
        if heapify: self.priority = priority
        return heapify

    def __init__(self, obj, operation, attrs, response_for, priority):
        self.obj = obj
        self.operation = operation
        if attrs:
            self.attrs = frozenset(attrs)
        else: self.attrs = None
        self.response_for = response_for
        self.priority = priority
        

class DirtyQueue:

    # We maintain both a heap in priority order and a dict mapping
    # DirtyElements to themselves.  We need to support several
    # operations efficiently.  We need to send the highest priority
    # item.  We need to merge (coalesce or replace) an existing item.
    # We need to add a new item.  The dict is required for the merge
    # operation to be efficient.

    def pop(self):
        try: elt = heapq.heappop(self.heap)
        except IndexError: raise StopIteration
        del self.dict[elt]
        return elt

    def add_or_replace(self, elt):
        if elt not in self.dict:
            try:
                heapq.heappush(self.heap, elt)
                self.dict[elt] = elt
            except:
                # This really can't happen, but maintain our internal consistency
                if elt in self.dict: del self.dict[elt]
                self.heap = list(self.dict.values())
                heapq.heapify(self.heap)
                raise
        else:
            re_heapify = self.dict[elt].update(elt)
            if re_heapify: heapq.heapify(self.heap)
            
    def __contains__(self, elt):
        return elt in self.dict

    def __len__(self):
        return len(self.heap)

    def __iter__(self):
        return iter(self.heap)
    def __init__(self):
        self.dict = dict()
        self.heap = []
        
