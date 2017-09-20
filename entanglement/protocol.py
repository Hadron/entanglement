# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, json, logging, struct, weakref
from .util import CertHash
from .interface import SyncError, SyncBadEncodingError

logger = logging.getLogger("entanglement")
protocol_logger = logging.getLogger('entanglement.protocol')
#protocol_logger.setLevel('DEBUG')
protocol_logger.setLevel('ERROR')

_msg_header = ">II" # A 4-byte big-endien size and four byte flags
_msg_header_size = struct.calcsize(_msg_header)
assert _msg_header_size == 8
_MSG_FLAG_RESPONSE_NEEDED = 1
_MSG_FLAGS_CRITICAL = 0xffff
_MSG_FLAGS_UNDERSTOOD = _MSG_FLAG_RESPONSE_NEEDED
# If a message is received where flags&(_MSG_FLAGS_CRITICAL & (~_MSG_FLAGS_UNDERSTOOD)) != 0, then we throw away the connection because we don't understand critical extensions

class ResponseReceiver:
    '''A class representing the responses that should be sent in responce
    to a synchronized object.  Passed into SyncManager.synchronize as
    the response_for parameter, generally in the flood rule of an
    operation.

    There are two assymetric response handling paths.  On receive,
    sync_receive needs to look up the incoming message and see if it
    is a response.  If so, it should be set as the result for futures
    waiting on that response.  However, when we generate a response
    message to forward, we do that in the middle of the flood method
    of the operation, rather than by returning from some handler.  So,
    a response context needs to be passed along to the flood rule so
    that outgoing messages can be marked as needing a response and
    tied back to the triggering message.

    '''

    __slots__ = ('futures', 'forwards', 'no_response_yet')

    def __init__(self):
        self.futures = []
        self.forwards = weakref.WeakKeyDictionary()
        self.no_response_yet = True


    def __call__(self, result):
        '''Result is the response received from the message; should be called in the message receive loop'''
        # If an incoming operation method adds a future, we don't want
        # the sync_receive that called that incoming method to count
        # as a local response.  We need to have either sent towards a
        # destination in our forwards, or to have hit the resp_for
        # handler before a future should be called.
        if self.no_response_yet: return

        for fut in self.futures:
            if fut.cancelled() or fut.done(): continue
            if isinstance(result, Exception):
                fut.set_exception(result)
            else: fut.set_result(result)
        self.futures.clear()

    def __del__(self):
        self.no_response()

    def sending_to(self, dests):
        "Indicate what destinations we are sending to; if we are sending to a destination already waiting for a response, then we are the response and none of these sends should request a future response."
        for p in self.forwards.keys():
            try:
                if p.dest in dests:
                    self.no_response_yet = False
                    return
            except KeyError: pass

    def no_response(self):
        self.no_response_yet = False
        self(None)
        for p, l in self.forwards.items():
            p._no_response(l)
        self.forwards.clear()

    def add_forward(self, protocol, msgnum):
        l = self.forwards.setdefault(protocol, [])
        l.append(msgnum)

    def add_future(self, fut):
        self.futures.append(fut)

    def merge(self, other):
        if other is None: return
        if other is self: return
        self.futures.extend(other.futures)
        for k, v in other.forwards.items():
            l = self.forwards.setdefault(k, [])
            l.extend(v)
        other.forwards = {}
        other.futures = []
    def responses_to(self, protocol):
        "Returns the set of messages that are being responded to when responding out the given protocol"
        try:
            l = self.forwards[protocol]
            del self.forwards[protocol]
            return l
        except KeyError: return None




class DirtyMember:

    __slots__ = ('obj', 'operation', 'attrs', 'response_for')

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

    def update(self, obj, operation, attrs, response_for):
        assert self.obj.sync_compatible(obj)
        if (attrs or self.attrs) and (self.attrs - set(attrs)): #Removing attributes
            raise NotImplementedError("Would need merge to handle removing outgoing attributes")
        self.obj = obj
        self.operation = operation
        self.attrs = frozenset(attrs) if attrs else None
        if self.response_for:
            self.response_for.merge(response_for)
        else: self.response_for = response_for

    def __init__(self, obj, operation, attrs, response_for):
        self.obj = obj
        self.operation = operation
        if attrs:
            self.attrs = frozenset(attrs)
        else: self.attrs = None
        self.response_for = response_for

class SyncProtocolBase:

    def __init__(self, manager, incoming = False,
                 dest = None,
                 **kwargs):
        super().__init__()
        self._manager = manager
        self._out_counter = 0
        self._in_counter = 0
        self._expected = {}
        self._no_resp_for = []
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
        self.dest = dest
        self._incoming = incoming

    def is_closed(self):
        return self.loop is None
    
    def _synchronize_object(self,obj,
                            operation, attributes, response_for):
        """Send obj out to be synchronized; this is an internal interface that should only be called by SyncManager.synchronize"""
        elt = DirtyMember(obj, operation, attributes, response_for)
        if elt in self.current_dirty:
            self.current_dirty[elt].update(obj, operation, attributes, response_for)
        else:
            self.dirty.setdefault(elt, elt).update(obj, operation, attributes, response_for)
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

    def _no_response(self, msgnums):
        self._no_resp_for.extend(msgnums)
        self._schedule_meta()

    def _schedule_meta(self):
        if self.task is not None: return
        if self.is_closed(): return
        self.task = self.loop.create_task(self._run_sync())

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
            self._send_sync_message(None) #Send metadata only message if useful
            if self.drain_future:
                self.drain_future.set_result(True)
                self.drain_future = None
                self.current_dirty = self.dirty
                if len(self.dirty) > 0:
                    self.task = self.loop.create_task(self._run_sync())

    def _send_sync_message(self, elt):
        flags = 0
        responses_to = None
        if elt and elt.response_for:
            if elt.response_for.no_response_yet:
                flags |= _MSG_FLAG_RESPONSE_NEEDED
                self._expected[self._out_counter] = elt.response_for
            responses_to = elt.response_for.responses_to(self)
        if elt:
            obj = elt.obj
            sync_rep = obj.to_sync(attributes = elt.attrs)
            if responses_to:
                sync_rep['_resp_for'] = responses_to
            sync_rep['_sync_type'] = obj.sync_type
            if elt.operation != 'sync':
                sync_rep['_sync_operation'] = elt.operation
        else:
            sync_rep = {}
        new_flags = self._handle_meta_out(flags, sync_rep)
        if len(sync_rep) == 0: return
        self._send_json(sync_rep, new_flags)
        self._out_counter += 1



    def _handle_receive(self, sync_repr, flags):
        try:
            self._handle_meta(sync_repr, flags)
            if '_sync_type' not in sync_repr: # metadata only
                return
            response_for = None
            if flags&_MSG_FLAG_RESPONSE_NEEDED:
                response_for = ResponseReceiver()
                response_for.add_forward(self, self._in_counter)
            if '_resp_for' in sync_repr:
                if response_for is not None:
                    raise SyncBadEncodingError('A message cannot both be a response and require a response')
                for msgnum in sync_repr['_resp_for']:
                    msgnum = int(msgnum)
                    new_resp = self._expected.pop(msgnum, None)
                    if not response_for:
                        response_for = new_resp
                        response_for.no_response_yet = False
                    else:
                        response_for.merge(new_resp)
                        response_for.no_response_yet = False
                del sync_repr['_resp_for']
            self._manager._sync_receive(sync_repr, self, response_for = response_for)
        except Exception as e:
            logger.exception("Error receiving {}".format(sync_repr))
            if isinstance(e,SyncError) and not '_sync_is_error' in sync_repr:
                self._manager.synchronize(e,
                                          destinations = [self.dest],
                                          response_for = response_for,
                                          operation = 'error')
        finally:
            self._in_counter += 1
            response_for = None

    def _handle_meta(self, sync_repr, flags):
        if '_no_resp_for' in sync_repr:
            for msgnum in sync_repr['_no_resp_for']:
                msgnum = int(msgnum)
                try:
                    r = self._expected.pop(msgnum)
                    r.no_response()
                except KeyError: pass
            del sync_repr['_no_resp_for']

    def _handle_meta_out(self, flags, sync_repr):
        if self._no_resp_for:
            sync_repr['_no_resp_for'] = list(self._no_resp_for)
            self._no_resp_for.clear()
        return flags

    def data_received(self, data):
        self.reader.feed_data(data)

    def eof_received(self): return False


    def connection_lost(self, exc):
        if getattr(self, 'loop', None) is None: return
        if self.loop.is_closed(): return
        if self.task: self.task.cancel()
        if self.waiter: self.waiter.cancel()
        if self.dest:
            self._manager._connection_lost(self, exc)
        self.loop = None
        self._manager = None

    def __del__(self):
        self.close()


    def pause_writing(self):
        if self.waiter: return
        self.waiter = self.loop.create_future()

    def resume_writing(self):
        assert self.waiter is not None
        self.waiter.set_result(None)
        self.waiter = None




sync_magic_attributes = ('_sync_type', '_sync_is_error',
                         '_resp_for', '_no_resp',
                         '_sync_operation',
                         '_sync_owner')


class SyncProtocol(SyncProtocolBase, asyncio.Protocol):

    def __init__(self, manager, incoming = False,  dest = None, **kwargs):
        super().__init__(manager, incoming, dest, **kwargs)
        self.transport = None
        self.reader = asyncio.StreamReader(loop = self.loop)

    def _send_json(self, sync_rep, flags):
        js = bytes(json.dumps(sync_rep), 'utf-8')
        protocol_logger.debug("#{c}: Sending `{js}' to {d} (flags {f})".format(
            js = js, d = self.dest,
            c = self._out_counter, f = flags))
        assert len(js) <= 65536
        header = struct.pack(_msg_header, len(js), flags)
        self.transport.write(header + js)

    async def _read_task(self):
        while True:
            header = await self.reader.readexactly(_msg_header_size)
            jslen, flags = struct.unpack(_msg_header, header)
            assert jslen <= 65536
            if flags&(_MSG_FLAGS_CRITICAL&(~_MSG_FLAGS_UNDERSTOOD)) != 0:
                self.close()
                raise ValueError("Flags contained unknown critical option")

            js = await self.reader.readexactly(jslen)
            protocol_logger.debug("#{c}: Receiving {js} from {d} (flags {f})".format(
                f = flags, c = self._in_counter,
                js = js, d = self.dest))
            sync_repr = json.loads(str(js, 'utf-8'))
            self._handle_receive(sync_repr, flags)

    def connection_lost(self, exc):
        if getattr(self, 'loop', None) is None: return
        if not self.loop.is_closed():
            self.reader.feed_eof()
            if self.reader_task: self.reader_task.cancel()
            super().connection_lost(exc)
        del self.transport
        del self._manager
        del self._expected

    def close(self):
        if not (getattr(self, 'loop', None) and hasattr(self, 'transport')): return
        self.transport.close()
        self.connection_lost(None)

    def connection_made(self, transport, bwprotocol):
        self.transport = transport
        self.bwprotocol = bwprotocol
        self.reader.set_transport(transport)
        self._manager._transports.append(weakref.ref(self.transport))
        if self._incoming:
            self.loop.create_task(self._manager._incoming_connection(self))

    def _enable_reading(self):
        "Callback from manager to enable reading after any authentication"
        self.reader_task = self.loop.create_task(self._read_task())
        self.reader_task._log_destroy_pending = False

    @property
    def dest_hash(self):
        if  not self.transport: return None
        return CertHash.from_der_cert(self.transport.get_extra_info('ssl_object').getpeercert(True))
