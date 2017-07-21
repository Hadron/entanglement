#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


from . import interface

# We assume objects have a method sync_owner that will return their owner or None if locally owned.  We assume that sync_owners have a method destination that returns a SyncDestination and that the context has both a sender and owner 

class SyncOperation:

    def incoming(self, obj, registry, **info):
        operation = str(info.get('operation', sync_operation))
        meth = getattr(registry, 'incoming_'+operation, None)
        meth_post_flood = getattr(registry, 'after_flood_'+operation, None)
        res = None
        if callable(meth):
            res =  meth(obj, registry = registry, **info)
        self.flood(obj, registry = registry, **info)
        if callable(meth_post_flood): meth_post_flood(obj, registry = registry, **info)
        return res

    def __str__(self):
        return  self.name #should be set by subclasses

    def should_listen_constructed(self, obj, msg, **info):
        '''Implement owner checks etc'''
        raise NotImplementedError

    def flood(self, obj, **info):
        "Flood an incoming received object.  Not called for outgoing objects."
        raise NotImplementedError

class MethodOperation(SyncOperation):

    def __init__(self, name, incoming):
        self.name = name
        self.incoming = incoming

    def should_listen_constructed(self, obj, msg, **info):
        return True

    def flood(self, obj, **info):
        pass
    
class sync_operation(SyncOperation):

    name = 'sync'

    def should_listen_constructed(self, obj, msg, sender, context,  **info):
        if obj.sync_owner is interface.EphemeralUnflooded: return True
        if obj.sync_is_local: 
            raise interface.SyncBadOwner("{} synchronized one of our objects to us".format(sender))
        if obj.sync_owner.destination != sender:
            raise interface.SyncBadOwner("{} sent an object belonging to {}".format(sender, obj.sync_owner.destination))
        if hasattr(context, 'owner') and context.owner.destination != sender:
            raise interface.SyncBadOwner("{} sent an object with owner {} belonging to {}".format(
                sender, owner, owner.destination))
        return True

    def flood(self, obj, manager, sender, response_for, **info):
        if obj.sync_owner is interface.EphemeralUnflooded: return
        assert obj.sync_owner.destination == sender
        manager.synchronize(obj, exclude = [sender], operation = 'sync',
                            response_for = response_for)

sync_operation = sync_operation()

class forward_operation(SyncOperation):

    name = 'forward'

    def should_listen_constructed(self, obj, msg, sender, **info):
        if obj.sync_owner is interface.EphemeralUnflooded:
            raise interface.SyncBadOwner("{} is EphemeralUnflooded and cannot be forwarded".format(obj))
        if not obj.sync_is_local:
            if obj.sync_owner.destination == sender:
                raise interface.SyncBadOwner("{} forwarded {} which we think they own".format(
                    sender, obj))
        return True

    def flood(self, obj, manager, sender, response_for,  **info):
        if obj.sync_is_local:
            manager.synchronize(obj, operation = 'sync', response_for = response_for)
        else:
            dest = manager.dest_by_cert_hash(obj.sync_owner.destination.cert_hash)
            manager.synchronize(obj, destinations = [dest], operation = 'forward',
                                attributes_to_sync = info.get('attributes'),
                                response_for = response_for)
            


forward_operation = forward_operation()

class delete_operation(SyncOperation):

    name = 'delete'

    def should_listen_constructed(self, obj, msg, **info):
        if obj.sync_owner is interface.EphemeralUnflooded:
            raise interface.SyncBadOwner("{} is EphemeralUnflooded and cannot be deleted".format(obj))
        return True

    def flood(self, obj, sender, manager, response_for, **info):
        if obj.sync_is_local:
            manager.synchronize(obj, operation ='delete', attributes_to_sync = obj.sync_primary_keys,
                                response_for = response_for)
        else:
            if obj.sync_owner.destination == sender:
                manager.synchronize(obj, operation = 'delete',
                                    attributes_to_sync = obj.sync_primary_keys,
                                    exclude = [sender],
                                    response_for = response_for)
            else: #not from direction of object owner, so forward there
                dest = manager.dest_by_cert_hash(obj.sync_owner.destination.cert_hash)
                manager.synchronize(obj, operation = 'delete',
                                    attributes_to_sync = obj.sync_primary_keys,
                                    destinations = [dest],
                                    response_for = response_for)
                

delete_operation = delete_operation()
