# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, json, logging, struct, weakref
from .util import CertHash
from .interface import SyncError

logger = logging.getLogger("hadron.entanglement")
protocol_logger = logging.getLogger('hadron.entanglement.protocol')
#protocol_logger.setLevel('DEBUG')
protocol_logger.setLevel('ERROR')

_msg_header = ">I" # A 4-byte big-endien size
_msg_header_size = struct.calcsize(_msg_header)
assert _msg_header_size == 4

class DirtyMember:

    __slots__ = ('obj', 'operation', 'attrs')

    def __eq__(self, other):
        return self.obj.sync_compatible(other.obj)

    def __hash__(self):
        return self.obj.sync_hash()

    def __repr__(self):
        keydict = {}
        try:
            keys = self.obj.sync_primary_keys
            for k in keys:
                keydict[k] = getattr(self.obj, k, None)
        except Exception: pass
        return "DirtyElement <keys : {k}, obj: {o}>".format(
            k = keydict,
            o = repr(self.obj))

    def update(self, obj, operation, attrs):
        assert self.obj.sync_compatible(obj)
        if (attrs or self.attrs) and (self.attrs - set(attrs)): #Removing attributes
            raise NotImplementedError("Would need merge to handle removing outgoing attributes")
        self.obj = obj
        self.operation = operation
        self.attrs = frozenset(attrs) if attrs else None

    def __init__(self, obj, operation, attrs):
        self.obj = obj
        self.operation = operation
        if attrs:
            self.attrs = frozenset(attrs)
        else: self.attrs = None

class SyncProtocol(asyncio.Protocol):

    def __init__(self, manager, incoming = False,
                 dest = None,
                 **kwargs):
        super().__init__()
        self._manager = manager
        self.loop = manager.loop
        #self.dirty is where we add new object not equal to anything we are currently considering
        # self.current_dirty is where we send from, which may be self.dirty
        #In a drain, we'll stop adding objects to self.current_dirty (switching the pointers) and wait for the sync to complete
        #Note though that objects equal to something in current_dirty are added there
        self.dirty = dict()
        self.current_dirty = self.dirty
        self.drain_future = None
        self.waiter = None
        self.task = None
        self.transport = None
        self.reader = asyncio.StreamReader(loop = self.loop)
        self.dest = dest
        self._incoming = incoming

    def _synchronize_object(self,obj,
                            operation, attributes):
        """Send obj out to be synchronized; this is an internal interface that should only be called by SyncManager.synchronize"""
        elt = DirtyMember(obj, operation, attributes)
        if elt in self.current_dirty:
            self.current_dirty[elt].update(obj, operation, attributes)
        else:
            self.dirty.setdefault(elt, elt).update(obj, operation, attributes)
        if self.task is None:
            self.task = self.loop.create_task(self._run_sync())

    def sync_drain(self):
        "Returns a future; when this future is done, all objects synchronized before sync_drain is called have been sent.  Note that some objects synchronized after sync_drain is called may have been sent."
        if self.drain_future:
            for elt in self.dirty:
                self.current_dirty[elt] = elt
            self.dirty.clear()
            return asyncio.shield(self.drain_future)
        else:
            if self.task:
                self.drain_future = self.loop.create_future()
                self.dirty = dict()
                return asyncio.shield(self.drain_future)
            else: #We're not currently synchronizing
                fut = self.loop.create_future()
                fut.set_result(True)
                return fut

    async def _run_sync(self):
        if self.waiter: await self.waiter
        try:
            while True:
                elt = self.current_dirty.pop(next(iter(self.current_dirty.keys())))
                try:self._send_sync_message(elt)
                except:
                    logger.exception("Error sending {}".format(repr(elt.obj)))
                if self.waiter: await self.waiter
        except StopIteration: #empty set
            self.task = None
            if self.drain_future:
                self.drain_future.set_result(True)
                self.drain_future = None
                self.current_dirty = self.dirty
                if len(self.dirty) > 0:
                    self.task = self.loop.create_task(self._run_sync())

    def _send_sync_message(self, elt):
        obj = elt.obj
        sync_rep = obj.to_sync(attributes = elt.attrs)
        sync_rep['_sync_type'] = obj.sync_type
        if elt.operation != 'sync':
            sync_rep['_sync_operation'] = elt.operation
        js = bytes(json.dumps(sync_rep), 'utf-8')
        protocol_logger.debug("Sending `{js}' to {d}".format(
            js = js, d = self.dest))

        assert len(js) <= 65536
        header = struct.pack(_msg_header, len(js))
        self.transport.write(header + js)

    async def _read_task(self):
        while True:
            header = await self.reader.readexactly(_msg_header_size)
            jslen = struct.unpack(_msg_header, header)[0]
            assert jslen <= 65536
            js = await self.reader.readexactly(jslen)
            protocol_logger.debug("Receiving {js} from {d}".format(
                js = js, d = self.dest))
            try:
                sync_repr = json.loads(str(js, 'utf-8'))
                self._manager._sync_receive(sync_repr, self)
            except Exception as e:
                logger.exception("Error receiving {}".format(sync_repr))
                if isinstance(e,SyncError) and not '_sync_is_error' in sync_repr:
                    self._manager.synchronize(e,
                                              destinations = [self.dest])


    def data_received(self, data):
        self.reader.feed_data(data)

    def eof_received(self): return False

    def connection_lost(self, exc):
        if not hasattr(self, 'loop'): return
        if not self.loop.is_closed():
            self.reader.feed_eof()
            if self.task: self.task.cancel()
            if self.reader_task: self.reader_task.cancel()
            if self.waiter: self.waiter.cancel()
            if self.dest:
                self.loop.create_task(self._manager._connection_lost(self, exc))
        del self.transport
        del self.loop
        del self._manager

    def close(self):
        if not hasattr(self, 'loop'): return
        self.transport.close()
        self.connection_lost(None)

    def __del__(self):
        self.close()

    def connection_made(self, transport, bwprotocol):
        self.transport = transport
        self.bwprotocol = bwprotocol
        self.reader.set_transport(transport)
        self.reader_task = self.loop.create_task(self._read_task())
        self.reader_task._log_destroy_pending = False
        self._manager._transports.append(weakref.ref(self.transport))
        if self._incoming:
            self.loop.create_task(self._manager._incoming_connection(self))

    def pause_writing(self):
        if self.waiter: return
        self.waiter = self.loop.create_future()

    def resume_writing(self):
        assert self.waiter is not None
        self.waiter.set_result(None)
        self.waiter = None


    @property
    def cert_hash(self):
        if  not self.transport: return None
        return CertHash.from_der_cert(self.transport.get_extra_info('ssl_object').getpeercert(True))


sync_magic_attributes = ('_sync_type', '_sync_is_error',
                         '_sync_operation',
                         '_sync_owner')
