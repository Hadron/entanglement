Example Listen/Send hooks
-------------------------

The following 'skeleton' code shows the available methods that can be modified.
See the `contract` for discussion of how these hooks are called.

A few notes:

* Use the super method on unhandled cases
* Send methods  return `True/False` while should_listen should just raise `SyncUnauthorized` to reject
* Most methods have `**info/**kwargs` as input. These dictionaries have useful context info/references

.. code-block:: python

    class Registry(SyncRegistry):

        def should_listen(self, msg, cls, **info):
            # Custom code return True/False
            return super().should_listen(msg,cls,**info)

        def should_listen_constructed(self, obj, msg, **info):
            # Custom code return True/False
            return super().should_listen_constructed(obj,msg,**info)

        def incoming_sync(self, obj, operation, **info):
            # Custom code

        def should_send(self, obj, destination, **info):
            # Custom code return True/False
            return super().should_send(obj, destination, **info)


    class EntanglementObject(Synchronizable):

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


    class Destination(SyncDestination):

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
            # Note that until connected returns, outgoing syncs do not
            # go to this destination unless the destination is
            # explicitly listed in the call to synchronize

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


