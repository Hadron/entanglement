Review of Entanglement Listen/Send Hooks
========================================

(1) Overview
------------

Entanglement has multiple hooks for controlling what messages are listened to and are sent to/from clients.

Note that there are 4 main components involved

* Object (a Synchronizable)
* Destination
* Protocol (ws_handler or socket)
* SyncRegistry

The root of this system can be seen in `network.py` for `SyncManager`, where the sequence of checks are:

.. code-block:: python

    def should_send(self, obj, destination, registry, sync_type, **info):
        '''
        1. destination.should_send( obj, **info):
        2. registry.should_send( obj, **info):
        3. obj.sync_should_send(**info):
        '''

    def should_listen(self, msg, cls, **info):
        '''
        1. sender.should_listen(msg, cls, **info) is not True:
        2. registry.should_listen(msg, cls, **info)is not True:
        3. cls.sync_should_listen(msg, **info) is not True:
            * here cls is the entanglement object class
        '''

    def should_listen_constructed(self, obj, msg, **info):
        '''
        1. info['registry'].should_listen_constructed(obj, msg, **info)
        2. obj.sync_should_listen_constructed(msg, **info)
        '''


(2) Sequence of checks
----------------------

The sequence of calls is the following:

Incoming messages

1. Destination::should_listen
2. SyncRegistry::should_listen
3. Object::sync_should_listen
4. SyncRegistry::should_listen_constructed
5. Object::sync_should_listen_constructed
6. Object::sync_receive_constructed
7. SyncRegistry:sync_receive

Outgoing messages

1. Destination::should_send
2. SyncRegistry::should_send
3. Object::sync_should_send


(3) Example hooks
-----------------

The following 'skeleton' code shows the available methods that can be modified.

A few notes:

* Use the super method on unhandled cases
* Some methods return `True/False` while others should just raise `SyncUnauthorized` to reject
* Most methods have `**info/**kwargs` as input. These dictionaries have useful context info/references

.. code-block:: python

    class Registry:

        def should_listen(self, msg, cls, **info):
            # Custom code return True/False
            return super().should_listen(msg,cls,**info)

        def should_listen_constructed(self, obj, msg, **info):
            # Custom code return True/False
            return super().should_listen_constructed(obj,msg,**info)

        def sync_receive(self, obj, operation, **info):
            # Custom code
            super().sync_receive(obj, operation = operation, **info)

        def should_send(self, obj, destination, **info):
            # Custom code return True/False
            return super().should_send(obj, destination, **info)


    class EntanglementObject:

        @classmethod
        def sync_should_listen(cls, msg, **info):
            # Attributes that can be pulled:
            sender = info.get('sender') # sender is a Destination
            ws_handler = sender.protocol.ws_handler # if relevant
            operation = info.get('operation')
            attributes = info.get('attributes')

            # Custom code should raise SyncUnauthorized to reject

        def sync_should_listen_constructed(self, msg, **info):
            # Custom code return True/False
            return super().sync_should_listen_constructed(msg,**info)

        def sync_receive_constructed(self, msg, **kwargs):
            # Attributes that can be pulled:
            operation = kwargs.get('operation',None)
            context = kwargs.get('context')
            context.session
            protocol = kwargs.get('protocol')
            ws_handler = getattr(protocol,'ws_handler',None)

        def sync_should_send(self, destination, **info):
            # Custom code return True/False


    class Destination:

        def should_listen(self, msg, cls, **info):
            protocol = info.get('protocol')
            ws_handler = protocol.ws_handler
            operation = info.get('operation')
            manager = info.get('manager')

            # Custom code should raise SyncUnauthorized to reject

        def sync_should_listen_co nstructed(self, obj, msg, **info):
            super().sync_should_listen_constructed(obj, msg,**info)

        def should_send(self, obj, **kwargs):
            return super().should_send(obj,**kwargs)

        async def connected(self, manager, *args, **kwargs):
            res = await super().connected(manager, *args, **kwargs)

            ws_handler = self.protocol.ws_handler
            return res


    class SyncWsHandler:

        async def get(self, media_uuid_str=None):
            return await super().get()

        def open(self, *args, **kwargs):
            self.application
            super().open(*args, **kwargs)

        def close(self, *args, **kwargs):
            super().close(*args, **kwargs)

        def find_sync_destination(self, *args, **kwargs):
            return Destination


