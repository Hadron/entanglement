# Copyright (C) 2018, 2019, 2020, 2022, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import ssl, asyncio, asyncio.log, json, os, pytest, unittest, uuid, warnings
from unittest import mock


from entanglement import bandwidth, protocol, SyncManager
from entanglement.interface import Synchronizable, sync_property, SyncRegistry, SyncError
from entanglement.network import  SyncServer, SyncDestination
from entanglement.util import certhash_from_file, DestHash, SqlDestHash, entanglement_logs_disabled

from .utils import settle_loop, test_port





class MockProto(asyncio.Protocol):

    def __init__(self, fixture ):
        self.fixture = fixture

    def data_received(self, data):
        pass

    def connection_lost(self, exc): pass

    def eof_received(self): return False
    def connection_made(self, transport):
        self.fixture.transports.append(transport)
        
        transport.write(b"blah blah blah")

reg = SyncRegistry()

class MockSyncable(Synchronizable):

    def __init__(self, id, pos):
        self.id = id
        self.pos = pos

    id = sync_property(constructor = 1)
    pos = sync_property(constructor = 2)

    sync_primary_keys = ('id',)
    sync_registry = reg

class MockSyncable2(MockSyncable):
    "This one stores itself"

    @classmethod
    def get(cls, id):
        if id not in cls.objects:
            cls.objects[id] = MockSyncable2(id, 0)
        return cls.objects[id]

    objects = {}

    @classmethod
    def _sync_construct(cls, msg, **kwargs):
#This is crude; one example is that it doesn't use id's decoder.
        #_sync_primary_keys_dict would be better
        id = msg['id']
        del msg['id']
        return cls.get(id)
    
        
    

class LoopFixture:

    def __init__(self):
        self.transports = []
        self.loop = asyncio.new_event_loop()
        self.sslctx_server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.sslctx_server.load_cert_chain('host1.pem','host1.key')
        self.sslctx_server.load_verify_locations(cafile='ca.pem')
        self.sslctx_server.check_hostname = False
        self.sslctx_client = ssl.create_default_context()
        self.sslctx_client.load_cert_chain('host1.pem','host1.key')
        self.sslctx_client.load_verify_locations(cafile='ca.pem')
        self.server= self.loop.run_until_complete(self.loop.create_server(lambda : MockProto(self), port = test_port, reuse_address = True, reuse_port = True, ssl=self.sslctx_server))
        self.client = self.loop.create_connection(lambda : bandwidth.BwLimitProtocol(
            chars_per_sec = 200, bw_quantum = 0.1,
            upper_protocol = MockProto(self), loop = self.loop)
                                             , port = test_port, host = '127.0.0.1', ssl=self.sslctx_client, server_hostname='host1')

    def close(self):
        if self.loop:
            for t in self.transports: t.close()
            self.loop.set_debug(False)
            self.server.close()
            self.loop.call_soon(self.loop.stop)
            self.loop.run_forever()
            self.loop.close()
            self.loop = None
            del self.transports
            

@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
def bwtest_protocol(loop):
    protocol = bandwidth.BwLimitProtocol(loop = loop,
                                        upper_protocol = mock.MagicMock(),
                                        chars_per_sec = 10000,
                                        bw_quantum = 0.1)
    return protocol


def testOverUse(bwtest_protocol, loop):
    "Confirm that if we use a lot, it gets refunded eventually"
    protocol = bwtest_protocol
    protocol.bw_used(2000)
    assert protocol._paused is True
    assert protocol.used == 2000
    loop.run_until_complete(asyncio.sleep(0.1))
    assert protocol.used == 1000
    loop.run_until_complete(asyncio.sleep(0.1))
    assert protocol.used == 0
    assert protocol._paused is False

def testGradual(loop, bwtest_protocol):
    protocol = bwtest_protocol
    protocol.bw_used(900)
    loop.call_soon(loop.stop)
    loop.run_forever()
    assert protocol.used == 900
    assert protocol._paused is False
    loop.run_until_complete(asyncio.sleep(0.9))
    protocol.bw_used(1)
    assert protocol.used == 1

def testTransportPause(loop, bwtest_protocol):
    "Test to confirm transport pause interacts with bandwidth pause"
    protocol = bwtest_protocol
    upper = protocol.protocol
    protocol.bw_used(1200)
    assert protocol._paused is True
    assert  upper.pause_writing.call_count == 1
    protocol.pause_writing()
    assert upper.pause_writing.call_count ==  1
    loop.run_until_complete(asyncio.sleep(0.1))
    assert protocol.used ==  200
    assert  upper.resume_writing.call_count == 0
    # Even though we're paused, the client can still write.
    #Write enough that one quantum isn't enough and confirm  that we do eventually resume once we're ready.
    protocol.bw_used(2000)
    protocol.resume_writing()
    loop.run_until_complete(asyncio.sleep(0.1))
    assert  upper.resume_writing.call_count ==  0
    loop.run_until_complete(asyncio.sleep(0.1))
    assert upper.resume_writing.call_count ==  1
        
        
            
    
        
class TestBandwidth(unittest.TestCase):

    def setUp(self):
        warnings.filterwarnings('ignore', module = 'asyncio.selector_events')
        self.fixture = LoopFixture()

    def tearDown(self):
        self.fixture.close()

    def testBasic(self):
        'Confirm that the loop can be set up.'
        fixture = self.fixture
        (transport, protocol) = fixture.loop.run_until_complete(fixture.client)
        transport.abort()
        

    def testClamping(self):
        "Confirm that pause is called"
        (transport, protocol) = self.fixture.loop.run_until_complete(self.fixture.client)
        pause_called = False
        def pause_writing_replacement():
            nonlocal pause_called
            pause_called = True
        protocol.protocol.pause_writing = pause_writing_replacement
        transport.write(b"f" * 20)
        self.assertTrue(pause_called)
        
    def testResume(self):
        "Confirm that resume  is called after pause"
        (transport, protocol) = self.fixture.loop.run_until_complete(self.fixture.client)
        pause_called = False
        resume_called = False
        def pause_writing_replacement():
            nonlocal pause_called
            pause_called = True
        def resume_writing_replacement():
            nonlocal resume_called
            resume_called = True
        protocol.protocol.pause_writing = pause_writing_replacement
        protocol.protocol.resume_writing = resume_writing_replacement
        transport.write(b"f" * 20)
        self.assertTrue(pause_called)
        self.assertFalse(resume_called)
        self.fixture.loop.run_until_complete(asyncio.sleep(0.15))
        self.assertTrue(resume_called)
            

class TestSynchronization(unittest.TestCase):

    def setUp(self):
        warnings.filterwarnings('ignore', module = 'asyncio.sslproto')
        self.manager = SyncServer(cafile = 'ca.pem',
                                  cert = "host1.pem", key = "host1.key",
                                  port = test_port,
                                  registries = [reg],
                                  )
        self.manager.listen_ssl(host = "127.0.0.1")
        self.cert_hash = certhash_from_file("host1.pem")
        client = self.manager.add_destination(SyncDestination(self.cert_hash,
                                                              "destination1", host = "127.0.0.1", server_hostname = "host1",
                                                              bw_per_sec = 2000000))
        self.transport, self.cprotocol = self.manager.run_until_complete(client)
        self.bwprotocol = self.cprotocol.dest.bwprotocol
        self.sprotocol = self.manager.incoming_self_protocol()

        self.loop = self.manager.loop
        
    def tearDown(self):
        self.manager.close()

    def testOneSync(self):
        obj = MockSyncable(1,39)
        assert self.cprotocol.task is None
        self.manager.synchronize(obj)
        assert self.cprotocol.task is not None
        task = self.cprotocol.task
        self.loop.run_until_complete(task)
        assert task.exception() is None

    def testNoSendPaused(self):
        "We do not send while paused"
        def fail_write(data):
            raise AssertionError("Write called while paused")
        self.transport.write = fail_write
        self.cprotocol.pause_writing()
        obj = MockSyncable(1,39)
        assert self.cprotocol.task is None
        self.manager.synchronize(obj)
        assert self.cprotocol.task is not None

    async def lots_of_updates(self):
        while True:
            self.obj1.pos += 10
            self.obj1.serial += 1
            self.manager.synchronize(self.obj1)
            await asyncio.sleep(0)

    def testBwLimit(self):
        "Confirm that bandwidth limits apply"
        def record_call(*args, **kwargs):
            nonlocal send_count
            send_count += 1
            return obj1_to_sync(*args, **kwargs)
        send_count = 0
        self.obj1 = MockSyncable(1, 5)
        self.obj1.serial = 1
        approx_len = 4+len(json.dumps(self.obj1.to_sync()))
        obj1_to_sync = self.obj1.to_sync
        with mock.patch.object(self.obj1, 'to_sync',
                               side_effect = record_call):
            self.bwprotocol.bw_per_quantum = 10*approx_len
            task = self.loop.create_task(self.lots_of_updates())
            self.loop.run_until_complete(asyncio.sleep(0.25))
            assert self.obj1.serial > 1000
            self.assertGreater(send_count, 10)
            assert send_count < 25
            self.assertTrue(self.bwprotocol._paused)
            assert self.cprotocol.waiter is not None
            task.cancel()
            with entanglement_logs_disabled(): self.cprotocol.connection_lost(None)

    def testReconnect(self):
        "After connection lost, reconnect"
        fut = self.loop.create_future()
        def cb(*args, **kwargs):
            fut.set_result(True)
            return orig_connected(*args, **kwargs)
        orig_connected = self.manager._destinations[self.cert_hash].connected
        with mock.patch.object(self.manager._destinations[self.cert_hash],
                               'connected',
                               side_effect = cb):
            with entanglement_logs_disabled():
                self.cprotocol.close()
                self.manager._destinations[self.cert_hash].connect_at = 0
                try:self.loop.run_until_complete(asyncio.wait_for(fut, 0.3))
                except asyncio.TimeoutError:
                    raise AssertionError("Connection failed to be made") from None


    def testReception(self):
        "Confirm that we can receive an update"
        fut = self.loop.create_future()
        MockSyncable2.objects = {}
        def cb(*args, **kwargs): fut.set_result(True)
        obj_send = MockSyncable2(1,90)
        obj_receive = MockSyncable2.get(obj_send.id)
        assert obj_send is not obj_receive # We cheat so this is true
        assert obj_send.pos != obj_receive.pos
        self.manager.synchronize(obj_send)
        with mock.patch.object(reg, 'sync_receive',
                        wraps = cb):
            self.manager.run_until_complete(self.cprotocol.task)
            self.manager.run_until_complete(asyncio.wait_for(fut,0.4))
        self.assertEqual(obj_send.id, obj_receive.id)
        self.assertEqual(obj_send.pos, obj_receive.pos)
        

    def testSyncDrain(self):
        "Confirm that by the time sync_drain is called objects synchronized before have been drained"
        MockSyncable2.objects = {}
        obj_send = MockSyncable2(1,90)
        obj_receive = MockSyncable2.get(obj_send.id)
        assert obj_send is not obj_receive # We cheat so this is true
        obj_send.to_sync = mock.MagicMock( wraps = obj_send.to_sync)
        self.manager.synchronize(obj_send)
        fut = self.cprotocol.sync_drain()
        obj_send2 = MockSyncable2(2, 20)
        obj_send2.to_sync = mock.MagicMock(wraps = obj_send2.to_sync)
        self.assertFalse(obj_send.to_sync.called)
        self.manager.synchronize(obj_send2)
        def set_results_wrap(res):
            self.assertFalse(obj_send2.to_sync.called)
            return orig_set_result(res)
        orig_set_result = fut.set_result
        self.loop.run_until_complete(fut)
        self.assertEqual(obj_send.to_sync.call_count, 1)

    def testNoResponseMetaOnly(self):
        "Confirm that if there is nothing to send, no_responses are still sent."
        count = self.cprotocol._out_counter
        self.cprotocol._out_counter += 1
        r = protocol.ResponseReceiver()
        fut = self.loop.create_future()
        r.add_future(fut)
        self.assertEqual(len(self.sprotocol.dirty), 0,
                         msg = "Server protocol has messages to send already")
        self.sprotocol._no_response([count])
        self.cprotocol._expected[count] = r
        self.loop.run_until_complete(asyncio.wait([fut], timeout = 0.3))
        self.assertTrue(fut.done())
        self.assertIsNone(fut.result())

    def testtestUnknownDestination(self):
        "Confirm unknown destination logic"
        async def unknown_destination(protocol):
            return SyncDestination(other_manager.cert_hash,
                                   "our manager")
        
        other_manager = SyncManager(cert = "host2.pem",
                                    key = "host2.key",
                                    cafile = "ca.pem",
                                    port = test_port,
                                    registries = [reg],
                                    loop = self.manager.loop)
        other_manager.add_destination(SyncDestination(dest_hash = self.cert_hash,
                                                      name = "localhost",
                                                      host = "localhost",
                                                      server_hostname = "host1"))
        self.manager.unknown_destination = unknown_destination
        settle_loop(self.loop)
        self.assertIn(other_manager.cert_hash, self.manager._connections.keys())

def test_desthash_equality():
    d1 = DestHash(b'o'*32)
    d1s = str(d1)
    assert d1 == d1s
    assert d1s == d1
    assert (d1s != d1) == False
    assert (d1 != d1s) == False
    

@pytest.fixture(scope = 'module')
def registries():
    # Registry that is empty.
    return [SyncRegistry()]

def test_unregistered_error_handling(layout, monkeypatch):
    exc_raised = None
    def sr_wrap(*args, **kwargs):
        nonlocal exc_raised
        try:
            return old_sr(*args, **kwargs)
        except Exception as e:
            exc_raised = e
            raise
    old_sr = SyncManager._sync_receive
    monkeypatch.setattr(SyncManager, '_sync_receive', sr_wrap)
    monkeypatch.setattr(layout.client.manager, '_find_registered_class', lambda a: (MockSyncable, reg))
    res = layout.client.manager.synchronize(MockSyncable(9, 10.3), response = True)
    # Now that synchronize is called we want to clear the find_registered_class hack so we can receive the syncerror
    del layout.client.manager._find_registered_class
    settle_loop(layout.loop)
    assert exc_raised is None
    assert isinstance(res.exception(), SyncError)
    




def test_entanglement_type():
    class c(Synchronizable):
        id:uuid.UUID = sync_property()
    from entanglement.types import uuid_encoder, uuid_decoder
    sp = c._sync_meta['id']
    assert sp.encoderfn == uuid_encoder
    assert sp.decoderfn == uuid_decoder
    
