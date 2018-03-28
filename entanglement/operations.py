#!/usr/bin/python3
# Copyright (C) 2017, 2018, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


from . import interface

# We assume objects have a method sync_owner that will return their owner or None if locally owned.  We assume that sync_owners have a method destination that returns a SyncDestination and that the context has both a sender and owner 

class SyncOperation:

    primary_keys_required = True # Does this operation require primary keys to be set
    
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


    def __eq__(self, other):
        if isinstance(other, str):
            return str(self) == other
        return super().__eq__(other)

    def __hash__(self):
        return id(self)

        def __ne__(self, other):
            res = (self == other)
            if res is NotImplemented: return NotImplemented
            return not res
        
    def should_listen_constructed(self, obj, msg, **info):
        '''Implement owner checks etc'''
        raise NotImplementedError

    def flood(self, obj, **info):
        "Flood an incoming received object.  Not called for outgoing objects."
        raise NotImplementedError
    def sync_value(self):
        return self.name

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
            dest = manager.dest_by_hash(obj.sync_owner.destination.dest_hash)
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
                dest = manager.dest_by_hash(obj.sync_owner.destination.dest_hash)
                manager.synchronize(obj, operation = 'delete',
                                    attributes_to_sync = obj.sync_primary_keys,
                                    destinations = [dest],
                                    response_for = response_for)
                

delete_operation = delete_operation()

class transition_operation(SyncOperation):

    name = 'transition'

    def incoming(self, obj, **info):
        response_for = info.get('response_for', None)
        manager = info['manager']
        if response_for:
            future = manager.loop.create_future()
            future.add_done_callback(obj._remove_from_transition_cb)
            response_for.add_future(future)
        obj.store_for_transition(response_for = response_for,
                                 sender = info.get('sender'))
        return super().incoming(obj, **info)
    
    
    def should_listen_constructed(self, obj, msg, **info):
        from .transition import TransitionTrackerMixin
        if not isinstance(obj, TransitionTrackerMixin):
            raise interface.SyncBadOperation('{} does not support transitions'.format(
                msg['_sync_type']))
        return True

    def flood(self, obj, manager, sender, response_for, **info):
        owner_dest = None
        exclude = [sender]
        # We flood to everyone except the sender.  However, when
        # flooding toward the owner, we'd prefer to preserve the
        # response_for so that the initial sender can get an error
        # response if they like
        if not obj.sync_is_local:
            owner_dest = manager.dest_by_hash(obj.sync_owner.destination.dest_hash)
        if owner_dest:
            manager.synchronize(obj,
                                operation = str(self),
                                attributes_to_sync = info.get('attributes', None),
                                response_for = response_for,
                                destinations = [owner_dest],
                                exclude = exclude)
            exclude.append(owner_dest)
        manager.synchronize(obj, operation = str(self),
                            attributes_to_sync = info.get('attributes', None),
                            exclude = exclude)

transition_operation = transition_operation()

class create_operation(SyncOperation):

    primary_keys_required = False
    name = 'create'
    

    def should_listen_constructed(self, obj, msg, sender, context, **info):
        should_listen_primary_keys = getattr(obj, 'sync_should_listen_primary_keys', None)
        #If the class has a sync_should_listen_primary_keys method, it
        #must return true in order for the create to be able to set a
        #primary key value
        intersection = set(obj.__class__.sync_primary_keys).intersection(msg.keys())
        if len(intersection) > 0:
            if not (should_listen_primary_keys and should_listen_primary_keys(msg, **info) is True):
                raise interface.SyncUnauthorized('Create set {}, which are primary keys'.format(intersection))

        if not hasattr(context, 'owner'):
            raise interface.SyncBadEncodingError('Create requires _sync_owner')
        if sender == context.owner.destination:
            raise interface.WrongSyncDestination('create loop detected')
        return True

    def flood(self, obj, context, manager, response_for, **info):

        if obj.sync_is_local:
            manager.synchronize(obj, response_for = response_for)
        else:
            manager.synchronize(obj,
                                operation = str(self),
                                destinations = [manager.dest_by_hash(context.owner.destination.dest_hash)],
                                response_for = response_for,
                                attributes_to_sync = info.get('attributes', None))

create_operation = create_operation()

                                
            

            
class ResponseOperation(SyncOperation):

    "an operation that floods a response back toward the initial senders"

    def should_listen_constructed(self, obj, msg, **info):
        return True

    def flood(self, obj, response_for, manager, sender, operation, **info):
        if response_for is None: return
        for p in list(response_for.forwards.keys()):
            try:
                dest = manager.dest_by_hash(p.dest.dest_hash)
                if dest:
                    manager.synchronize(obj,
                                        operation = str(operation),
                                        destinations = [dest],
                                        response_for = response_for,
                                        attributes_to_sync = info.get('attributes', None))
            except AttributeError: pass

class error_operation(ResponseOperation):

    name = 'error'

error_operation = error_operation()
