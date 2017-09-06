# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from .bandwidth import BwLimitMonitor
from .protocol import SyncProtocolBase, logger, protocol_logger
from .network import SyncDestination
import json

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

    @tornado.web.asynchronous
    def get(self, *args, **kwargs):
        if getattr(self, 'manager', None) is None:
            self.manager = self.application.sync_manager
        self.dest = self.find_sync_destination(*args, **kwargs)
        if self.dest is None:
            self.set_status(403)
            self.finish("Not authorized destination")
            return
        return super().get(*args, **kwargs)

    def open(self, *args, **kwargs):
        if self.dest in self.manager.destinations:
            if self.dest.dest_hash in self.manager._connections:
                logger.warning("Web socket destination {} replaces a        connection".format(self.dest))
        protocol = SyncWsProtocol(self.manager, self. dest)
        protocol.web_socket_connected(self)

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
    
        

class SyncWsProtocol(SyncProtocolBase):

    def __init__(self, manager, dest):
        super().__init__(manager, dest = dest, incoming = True)
        self.bwprotocol = BwLimitMonitor(loop = self.loop, chars_per_sec = 10000, bw_quantum=0.1)
        if dest not in manager.destinations:
            manager.add_destination(dest)
        self.ws_handler = None

    def web_socket_connected(self, ws_handler):
        self.ws_handler = ws_handler
        if getattr(self, '_manager', None):
            if self._manager.loop.is_closed():
                ws_handler.close(reason = "Manager shutting down")
            self._manager.loop.create_task(self._manager._incoming_connection(self))


    def connection_lost(self, exc):
        self.ws_handler = None
        super().connection_lost(exc)

    def close(self):
        if self.ws_handler:
            self.ws_handler.close()
        self.connection_lost(None)

    def _send_json(self, sync_rep, flags):
        sync_rep['_flags'] = int(flags)
        js = bytes(json.dumps(sync_rep), 'utf-8')
        protocol_logger.debug("#{c}: Sending `{js}' to {d} (flags {f})".format(
            js = js, d = self.dest,
            c = self._out_counter, f = flags))
        self.ws_handler.write_message(js)

    @property
    def dest_hash(self):
        try:
            return self.dest.dest_hash
        except AttributeError: return None

        
