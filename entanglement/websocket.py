# Copyright (C) 2017, 2021, 2025, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from .bandwidth import BwLimitMonitor
from .protocol import SyncProtocolBase, logger, protocol_logger
from .network import SyncDestination, SyncManager
import json

found_websocket_implementation = False

try:
    import tornado.websocket

    class SyncWsHandler(tornado.websocket.WebSocketHandler):

        '''Represents a tornado handler for connecting to an entanglement
        SyncManager.  Your application object  needs to have a
        sync_manager property, or manager needs to be set on classes or
        instances of this object prior to the get method being called.
        Similarly, either your application must have a
        find_sync_destination method, or the find_sync_destination method
        below needs to be overridden.
    '''

        async def get(self, *args, **kwargs):
            if getattr(self, 'manager', None) is None:
                self.manager = self.application.sync_manager
            self.dest = self.find_sync_destination(*args, **kwargs)
            if self.dest is None:
                self.set_status(403)
                self.finish("Not authorized destination")
                return
            res =  super().get(*args, **kwargs)
            if res is not None:
                return await res
            else: return res

        def open(self, *args, **kwargs):
            async def send(message):
                self.write_message(message)
            async def close():
                self.close()
            if self.dest in self.manager.destinations:
                if self.dest.dest_hash in self.manager._connections:
                    logger.warning("Web socket destination {} replaces a        connection".format(self.dest))
            protocol = SyncWsProtocol(self.manager, self. dest)
            protocol.web_socket_connected(send=send, close=close)

        def on_close(self):
            if self.dest and self.dest.protocol:
                self.dest.protocol.close()
                self.dest.protocol = None
            if self.dest and getattr(self.dest, 'ephemeral', True):
                self.manager.remove_destination(self.dest)



        def on_message(self, message):
            js = json.loads(message)
            flags = js.pop('_flags', 0)
            protocol_logger.debug("#{c}: Receiving {js} from {d} (flags {f})".format(
                    f = flags, c = self.dest.protocol._in_counter,
                    js = message, d = self.dest))
            self.dest.protocol._handle_receive(js, flags)

        def find_sync_destination(self, *get_args, **get_kwargs):
            '''Return the SyncDestination that this web socket should use
            or None if this connection should not be permitted.  By
            default this calls find_sync_destination(request, *get_args,
            **get_kwargs) on the application.  If you wish different
            behavior, for example because you have two entanglement
            endpoints, override this method.
    '''
            return self.application.find_sync_destination(self.request,
            *get_args, **get_kwargs)

    found_websocket_implementation = True
except ImportError: pass

try:
    import fastapi

    async def fastapi_entanglement_loop(socket:fastapi.WebSocket, destination:SyncDestination, *, manager:SyncManager):
        already_closed = False
        async def send(message):
            await socket.send_text(message)
        async def close():
            if not already_closed:
                await socket.close()
        if destination             in manager.destinations:
            if destination.dest_hash in manager._connections:
                logger.warning("Web socket destination {} replaces a        connection".format(destination))
        protocol = SyncWsProtocol(manager, destination)
        await socket.accept()
        try:
            protocol.web_socket_connected(send=send, close=close)
            async for js in socket.iter_json():
                flags = js.pop('_flags', 0)
                protocol_logger.debug("#{c}: Receiving {js} from {d} (flags {f})".format(
                    f = flags, c = protocol._in_counter,
                    js = js, d = destination))
                protocol._handle_receive(js, flags)
            already_closed = True
        finally:
            if destination.protocol:
                protocol.close()
                destination.protocol = None
                if getattr(destination, 'ephemeral', True):
                    manager.remove_destination(destination)
                if not already_closed:
                    await socket.close()

    found_websocket_implementation = True
except ImportError: pass

if not found_websocket_implementation:
    raise ImportError('Neither tornado nor fastap are installed.')

class SyncWsProtocol(SyncProtocolBase):

    def __init__(self, manager, dest):
        super().__init__(manager, dest = dest, incoming = True)
        self.bwprotocol = BwLimitMonitor(loop = self.loop, chars_per_sec = 10000, bw_quantum=0.1)
        if dest not in manager.destinations:
            manager.add_destination(dest)
        self.handle_send = None
        self.handle_close = None

    def _enable_reading(self): pass
    
    def web_socket_connected(self, send, close):
        self.handle_send = send
        self.handle_close = close
        if getattr(self, '_manager', None):
            if self._manager.loop.is_closed():
                raise RuntimeError('manager shutting down')
            self._manager.loop.create_task(self._manager._incoming_connection(self))


    def connection_lost(self, exc):
        self.handle_close = None
        self.handle_message = None
        super().connection_lost(exc)

    def close(self):
        if self.handle_close:
            self._manager.loop.create_task(self.handle_close())
        self.handle_close = None
        self.handle_send = None
        self.connection_lost(None)

    async def _send_json(self, sync_rep, flags):
        sync_rep['_flags'] = int(flags)
        js = json.dumps(sync_rep)
        protocol_logger.debug("#{c}: Sending `{js}' to {d} (flags {f})".format(
            js = js, d = self.dest,
            c = self._out_counter, f = flags))
        if self.handle_send:
            await self.handle_send(js)

    @property
    def dest_hash(self):
        try:
            return self.dest.dest_hash
        except AttributeError: return None

        
