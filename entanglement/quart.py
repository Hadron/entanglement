# Copyright (C) 2023, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from .bandwidth import BwLimitMonitor
from .protocol import SyncProtocolBase, logger, protocol_logger
from .sql import SqlSyncDestination
from .network import SyncDestination

import os
import json
import asyncio
import logging

from quart.globals import websocket

protocol_logger.setLevel('INFO')

class QuartSyncWsHandler(object):

    def __init__(self, *, app, manager, websocket):
        self.app = app
        self.manager = manager
        self.websocket = websocket

    def find_sync_destination(self, *args, **kwargs):
        return SqlSyncDestination(os.urandom(32), 'client')

    async def run(self):

        self.destination = self.find_sync_destination()
        self.protocol = QuartSyncWsProtocol(handler=self)

        await self.protocol.setup_connection()

class QuartSyncWsProtocol(SyncProtocolBase):

    def __init__(self, *, handler):

        self.handler = handler
        self.manager = handler.manager
        self.destination = handler.destination
        super().__init__(self.manager, dest=self.destination, incoming=True)
        self.bwprotocol = BwLimitMonitor(loop=self.loop, chars_per_sec=10000, bw_quantum=0.1)
        if self.dest not in self.manager.destinations:
            self.manager.add_destination(self.destination)

    async def setup_connection(self):

        async def wrapper():
            try:
                return await self._manager._incoming_connection(self)
            except:
                logging.exception(f'exception from connection handler')

        if getattr(self, '_manager', None):
            if self._manager.loop.is_closed():
                ws_handler.close(reason = "manager shutting down")
            self._manager.loop.create_task(wrapper())

        await self.handler.websocket.accept()

        while True:
            data = await self.handler.websocket.receive()
            js = json.loads(data)
            flags = js.pop('_flags', 0)
            protocol_logger.debug(f"#{self._in_counter}: Receiving {js} from {self.dest} (flags {flags})")
            self._handle_receive(js, flags)

    def _enable_reading(self):
        pass

    def connection_lost(self, exc):
        raise ValueError

    def close(self):
        raise ValueError

    async def _send_json(self, sync_rep, flags):
        sync_rep['_flags'] = int(flags)
        s = json.dumps(sync_rep)
        b = bytes(s, 'utf-8')
        protocol_logger.debug(f"#{self._out_counter}: Sending `{s}' to {self.dest} (flags {flags})")
        await self.handler.websocket.send(s)

    @property
    def dest_hash(self):
        return self.dest.dest_hash
