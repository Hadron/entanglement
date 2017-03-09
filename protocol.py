# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, json, struct, weakref

_msg_header = ">I" # A 4-byte big-endien size
_msg_header_size = struct.calcsize(_msg_header)
assert _msg_header_size == 4

class SyncProtocol(asyncio.Protocol):

    def __init__(self, manager, **kwargs):
        super().__init__()
        self._manager = manager
        self.loop = manager.loop
        self.dirty = set()
        self.waiter = None
        self.task = None
        self.transport = None
        self.reader = asyncio.StreamReader(loop = self.loop)

    def synchronize_object(self,obj):
        """Send obj out to be synchronized"""
        self.dirty.add(obj)
        if self.task is None:
            self.task = self.loop.create_task(self._run_sync())

    async def _run_sync(self):
        if self.waiter: await self.waiter
        try:
            while True:
                elt = self.dirty.pop()
                self._send_sync_message(elt)
                if self.waiter: await self.waiter
        except KeyError: #empty set
            self.task = None

    def _send_sync_message(self, obj):
        sync_rep = obj.to_sync()
        sync_rep['_sync_type'] = obj.sync_type
        js = bytes(json.dumps(sync_rep), 'utf-8')
        assert len(js) <= 65536
        header = struct.pack(_msg_header, len(js))
        self.transport.write(header + js)

    async def _read_task(self):
        while True:
            header = await self.reader.readexactly(_msg_header_size)
            jslen = struct.unpack(_msg_header, header)[0]
            assert jslen <= 65536
            js = await self.reader.readexactly(jslen)
            sync_repr = json.loads(str(js, 'utf-8'))
            print(sync_repr)

    def data_received(self, data):
        self.reader.feed_data(data)

    def eof_received(self): return False

    def connection_lost(self, exc):
        if not hasattr(self, 'loop'): return
        self.reader.feed_eof()
        if self.task: self.task.cancel()
        if self.reader_task: self.reader_task.cancel()
        if self.waiter: self.waiter.cancel()
        del self.transport
        del self.loop
        del self._manager

    def connection_made(self, transport):
        self.transport = transport
        self.reader.set_transport(transport)
        self.reader_task = self.loop.create_task(self._read_task())
        self._manager._transports.append(weakref.ref(self.transport))

    def pause_writing(self):
        if self.waiter: return
        self.waiter = self.loop.create_future()

    def resume_writing(self):
        assert self.waiter is not None
        self.waiter.set_result(None)
        self.waiter = None
        
