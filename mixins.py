#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


class Synchronizable:

    def to_sync(self):
        '''Return a dictionary containing the attributes of self that should be synchronized.'''
        raise NotImplementedError

    def __hash__(self):
        '''Hash all the primary keys.'''
        return sum(map(lambda x: getattr(self, x).__hash__(), self.__class__.sync_primary_keys))

    def __eq__(self, other):
        '''Return true if the primary keys of self match the primary keys of other'''
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
