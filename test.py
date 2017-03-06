# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import ssl, asyncio, os, unittest

import bandwidth


class TestProto(asyncio.Protocol):

    def __init__(self, fixture):
        self.fixture = fixture

    def data_received(self, data):
        print(str(data, 'utf-8')+"\n")

    def connection_lost(self, exc): pass

    def eof_received(self): return False
    def connection_made(self, transport):
        self.fixture.transports.append(transport)
        
        transport.write(b"blah blah blah")


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
        self.server= self.loop.run_until_complete(self.loop.create_server(lambda : TestProto(self), port = 9999, reuse_address = True, reuse_port = True, ssl=self.sslctx_server))
        self.client = self.loop.create_connection(lambda : bandwidth.BwLimitProtocol(
            chars_per_sec = 200, bw_quantum = 0.1,
            upper_protocol = TestProto(self), loop = self.loop)
                                             , port = 9999, host = '10.36.0.7', ssl=self.sslctx_client, server_hostname='host1')

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
            

        

class TestBandwidth(unittest.TestCase):

    def setUp(self):
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
            
