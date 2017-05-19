# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import ssl, asyncio, asyncio.log, json, os, unittest, warnings
from unittest import mock
from asyncio.test_utils import disable_logger, TestLoop, run_once


from hadron.entanglement import bandwidth, protocol
from hadron.entanglement.interface import Synchronizable, sync_property, SyncRegistry
from hadron.entanglement.network import  SyncServer, SyncDestination
from hadron.entanglement.util import certhash_from_file, CertHash, SqlCertHash, entanglement_logs_disabled





class TestProto(asyncio.Protocol):

    def __init__(self, fixture):
        self.fixture = fixture

    def data_received(self, data):
        pass

    def connection_lost(self, exc): pass

    def eof_received(self): return False
    def connection_made(self, transport):
        self.fixture.transports.append(transport)
        
        transport.write(b"blah blah blah")

reg = SyncRegistry()

class TestSyncable(Synchronizable):

    def __init__(self, id, pos):
        self.id = id
        self.pos = pos

    id = sync_property(constructor = 1)
    pos = sync_property(constructor = 2)

    sync_primary_keys = ('id',)
    sync_registry = reg

class TestSyncable2(TestSyncable):
    "This one stores itself"

    @classmethod
    def get(cls, id):
        if id not in cls.objects:
            cls.objects[id] = TestSyncable2(id, 0)
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
        self.sslctx_server = ssl.create_default_context()
        self.sslctx_server.load_cert_chain('host1.pem','host1.key')
        self.sslctx_server.load_verify_locations(cafile='ca.pem')
        self.sslctx_server.check_hostname = False
        self.sslctx_client = ssl.create_default_context()
        self.sslctx_client.load_cert_chain('host1.pem','host1.key')
        self.sslctx_client.load_verify_locations(cafile='ca.pem')
        self.server= self.loop.run_until_complete(self.loop.create_server(lambda : TestProto(self), port = 19284, reuse_address = True, reuse_port = True, ssl=self.sslctx_server))
        self.client = self.loop.create_connection(lambda : bandwidth.BwLimitProtocol(
            chars_per_sec = 200, bw_quantum = 0.1,
            upper_protocol = TestProto(self), loop = self.loop)
                                             , port = 19284, host = '127.0.0.1', ssl=self.sslctx_client, server_hostname='host1')

    def close(self):
        with disable_logger():
            if self.loop:
                for t in self.transports: t.close()
                self.loop.set_debug(False)
                self.server.close()
                self.loop.call_soon(self.loop.stop)
                self.loop.run_forever()
                self.loop.close()
                self.loop = None
            del self.transports
            

        

class TestBwAlgorithm( unittest.TestCase):

    "Test the timing algorithms for bandwidth"

    def start(self, gen):
        self.loop = TestLoop(gen)
        self.protocol = bandwidth.BwLimitProtocol(loop = self.loop,
                                        upper_protocol = mock.MagicMock(),
                                        chars_per_sec = 10000,
                                        bw_quantum = 0.1)

    def tearDown(self):
        self.loop.close()

    def testOverUse(self):
        "Confirm that if we use a lot, it gets refunded eventually"
        def gen():
            yield
            yield 0.1
            yield 0.1
        self.start(gen)
        self.protocol.bw_used(2000)
        self.assertTrue(self.protocol._paused)
        run_once(self.loop)
        self.assertEqual(self.protocol.used, 2000)
        run_once(self.loop)
        self.assertEqual(self.protocol.used, 1000)
        run_once(self.loop)
        self.assertEqual(self.protocol.used, 0)
        self.assertFalse(self.protocol._paused, False)

    def testGradual(self):
        def gen():
            yield
        self.start(gen)
        self.protocol.bw_used(900)
        run_once(self.loop)
        self.assertEqual( self.protocol.used, 900)
        self.assertFalse(self.protocol._paused, False)
        self.loop.advance_time(2)
        run_once(self.loop)
        self.protocol.bw_used(1)
        self.assertEqual( self.protocol.used, 1)

    def testTransportPause(self):
        "Test to confirm transport pause interacts with bandwidth pause"
        def gen():
            yield
            yield 0.1
            yield 0.1
            yield 0.1
        self.start(gen)
        upper = self.protocol.protocol
        protocol = self.protocol
        protocol.bw_used(1200)
        self.assertTrue( protocol._paused)
        self.assertEqual( upper.pause_writing.call_count,1)
        protocol.pause_writing()
        self.assertEqual(upper.pause_writing.call_count, 1)
        run_once(self.loop)
        self.assertEqual( self.loop.time(), 0.1)
        run_once(self.loop)
        self.assertEqual( protocol.used, 200)
        self.assertEqual( upper.resume_writing.call_count, 0)
        # Even though we're paused, the client can still write.
        #Write enough that one quantum isn't enough and confirm  that we do eventually resume once we're ready.
        protocol.bw_used(2000)
        protocol.resume_writing()
        # still at t=0.1; collect the timer scheduled by resume_writing
        run_once(self.loop)
        self.assertEqual( self.loop.time(), 0.2)
        self.assertEqual( upper.resume_writing.call_count, 0)
        run_once(self.loop) # at t = 0.2
        self.assertAlmostEqual(self.loop.time(), 0.3)
        run_once(self.loop) # at t = 0.3
        self.assertEqual( upper.resume_writing.call_count, 1)
        
        
            
    
        
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
                                  port = 9120,
                                  registries = [reg],
                                  host = "127.0.0.1")
        self.cert_hash = certhash_from_file("host1.pem")
        client = self.manager.add_destination(SyncDestination(self.cert_hash,
                                                              "destination1", host = "127.0.0.1", server_hostname = "host1",
                                                              bw_per_sec = 2000000))
        self.transport, self.cprotocol = self.manager.run_until_complete(client)
        self.bwprotocol = self.cprotocol.dest.bwprotocol

        self.loop = self.manager.loop
        
    def tearDown(self):
        self.manager.close()

    def testOneSync(self):
        obj = TestSyncable(1,39)
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
        obj = TestSyncable(1,39)
        assert self.cprotocol.task is None
        self.manager.synchronize(obj)
        assert self.cprotocol.task is not None

    # This has to be an asyncio.coroutine because you cannot yield
    # None (loop please just continue with someone else) from an async
    # def
    @asyncio.coroutine
    def lots_of_updates(self):
        while True:
            self.obj1.pos += 10
            self.obj1.serial += 1
            self.manager.synchronize(self.obj1)
            yield

    def testBwLimit(self):
        "Confirm that bandwidth limits apply"
        def record_call(*args, **kwargs):
            nonlocal send_count
            send_count += 1
        send_count = 0
        self.obj1 = TestSyncable(1, 5)
        self.obj1.serial = 1
        approx_len = 4+len(json.dumps(self.obj1.to_sync()))
        with mock.patch.object(self.obj1, 'to_sync',
                               wraps = self.obj1.to_sync,
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
            self.cprotocol.connection_lost(None)

    def testReconnect(self):
        "After connection lost, reconnect"
        fut = self.loop.create_future()
        def cb(*args, **kwargs): fut.set_result(True)
        with mock.patch.object(self.manager._destinations[self.cert_hash],
                               'connected',
                               wraps = self.manager._destinations[self.cert_hash].connected,
                               side_effect = cb):
            with entanglement_logs_disabled():
                self.cprotocol.close()
                self.manager._destinations[self.cert_hash].connect_at = 0
                try:self.loop.run_until_complete(asyncio.wait_for(fut, 0.3))
                except asyncio.futures.TimeoutError:
                    raise AssertionError("Connection failed to be made") from None


    def testReception(self):
        "Confirm that we can receive an update"
        fut = self.loop.create_future()
        TestSyncable2.objects = {}
        def cb(*args, **kwargs): fut.set_result(True)
        obj_send = TestSyncable2(1,90)
        obj_receive = TestSyncable2.get(obj_send.id)
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
        TestSyncable2.objects = {}
        obj_send = TestSyncable2(1,90)
        obj_receive = TestSyncable2.get(obj_send.id)
        assert obj_send is not obj_receive # We cheat so this is true
        obj_send.to_sync = mock.MagicMock( wraps = obj_send.to_sync)
        self.manager.synchronize(obj_send)
        fut = self.cprotocol.sync_drain()
        obj_send2 = TestSyncable2(2, 20)
        obj_send2.to_sync = mock.MagicMock(wraps = obj_send2.to_sync)
        self.assertFalse(obj_send.to_sync.called)
        self.manager.synchronize(obj_send2)
        fut.add_done_callback( lambda x: self.assertFalse(obj_send2.to_sync.called))
        self.loop.run_until_complete(fut)
        self.assertEqual(obj_send.to_sync.call_count, 1)
        


       



if __name__ == '__main__':
    import logging, unittest, unittest.main
    logging.basicConfig(level = 'ERROR')
#    logging.basicConfig(level = 10)
    unittest.main(module = "tests.entanglement")
    
    
