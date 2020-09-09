# Copyright (C) 2018, 2019, 2020, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import sys, os.path
sys.path = list(filter(lambda p: p != os.path.abspath(os.path.dirname(__file__)), sys.path))
import pytest
import asyncio, concurrent.futures, glob, json, threading, subprocess, unittest, uuid
from tornado.platform.asyncio import AsyncIOMainLoop
import sqlalchemy.exc

try: AsyncIOMainLoop().install()
except: pass
import tornado.web, tornado.websocket, tornado.ioloop, tornado.testing, tornado.httpserver
import entanglement.protocol
from entanglement import SyncServer, SyncDestination, operations
import entanglement.javascript_schema
from entanglement.util import entanglement_logs_disabled
from entanglement.sql import sql_sync_declarative_base, SqlSyncRegistry, SyncOwner, SqlSyncDestination
from entanglement.sql.transition import SqlTransitionTrackerMixin
from entanglement.websocket import SyncWsHandler
from sqlalchemy import Column, String, Integer, ForeignKey
from entanglement.util import GUID
from tests.utils import *
ioloop = tornado.ioloop.IOLoop.current()

@pytest.fixture(scope = 'module')
def requested_layout(requested_layout):
    # We'll take this opportunity to output schemas as well.
    entanglement.javascript_schema.output_js_schemas(js_test_path+"/schemas")
    requested_layout['server']['websocket'] = True
    return requested_layout

@pytest.fixture(scope = 'module')
def registries():
    return [Base]

js_test_path = os.path.abspath(os.path.dirname(__file__))

# SQL declaration
Base = sql_sync_declarative_base()
entanglement.javascript_schema.javascript_registry(Base.registry, "websocket_test")

class TableBase(Base):
    __tablename__ = "base_table"

    id = Column(GUID, primary_key = True,
                default = lambda: uuid.uuid4())
    type = Column(String, nullable = False)
    __mapper_args__ = {
        'polymorphic_on': 'type',
        'polymorphic_identity': 'base'}

class TableInherits(TableBase):
    __tablename__ = "inherits_table"
    id = Column(GUID,
                ForeignKey(TableBase.id, ondelete = "cascade"),
                primary_key = True)
    info = Column(String(30))
    info2 = Column(String(30))
    __mapper_args__ = {'polymorphic_identity': "inherits"}

class TableTransition(TableInherits, SqlTransitionTrackerMixin):
    __mapper_args__ = {
        'polymorphic_identity': 'transition'
        }

class TestPhase(Base):

    __tablename__ = "test_phase"

    id = Column(GUID, primary_key = True,
                default = lambda: uuid.uuid4())

    phase = Column(Integer, nullable = False)

    def sync_receive_constructed(self, *args, **kwargs):
        super().sync_receive_constructed(*args, **kwargs)
            
        
manager_registry = SqlSyncRegistry()
manager_registry.registry = Base.registry.registry

class JsTest(threading.Thread):

    def __init__(self, testname, uri, owner):
        super().__init__()
        self.testname = testname
        self.uri = uri
        self.owner = owner
        self.future = concurrent.futures.Future()
        

    def run(self):
        try:
            output = subprocess.check_call(['nodejs', self.testname,
                                                   self.uri, self.owner],
                                                  timeout = 3.0,
                                                  cwd = os.path.dirname(self.testname))
            self.future.set_result(output)
        except subprocess.CalledProcessError:
            self.future.set_exception(AssertionError())
        except Exception as e:
            self.future.set_exception(e)

def run_js_test(test, session_maker= None):
    # This code is shared between unittest and pytest tests.
    uri = "ws://localhost:{}/ws".format(test_port+2)
    test = os.path.join(js_test_path, test)
    if session_maker is None:
        session_maker = Base.registry.sessionmaker
    sess = session_maker()
    try:
        q = sess.query(SyncOwner).all()
        owner = str(q[0].id)
    except sqlalchemy.exc.UnboundExecutionError:
        owner = ""
    t = JsTest(test, uri, owner)
    t.start()
    return asyncio.futures.wrap_future(t.future)


def javascriptTest(test_name, method_name):
    # This is the unittest only parts of run_js_test
    def testMethod(self):
        future = run_js_test(test_name)
        helper_method = getattr(self, 'helper_'+method_name, None)
        if helper_method:
            ioloop.add_callback(helper_method)
        
        self.loop.run_until_complete(future)
    return testMethod
    

class TestWebsockets(SqlFixture, unittest.TestCase):

    def __init__(self, *args, **kwargs):
        self.base = Base
        self.manager_registry = manager_registry
        super().__init__(*args, **kwargs)

    def setUp(self):
        def find_sync_destination( request, *args, **kwargs):
            return self.client_destination
        self.client_destination = SyncDestination(b'n' * 32, 'websocket')
        super().setUp()
        self.client_destination.connected_future = self.loop.create_future()
        self.client_destination.on_connect(lambda: self.client_destination.connected_future.set_result(True))
        self.app = tornado.web.Application([(r'/ws', SyncWsHandler)])
        self.http_server = tornado.httpserver.HTTPServer(self.app)
        self.http_server.listen(test_port+2)
        self.app.sync_manager = self.manager
        self.app.find_sync_destination = find_sync_destination
        self.io_loop = ioloop
        settle_loop(self.loop)

    async def wait_for_client(self):
        self.client = tornado.websocket.websocket_connect(
            'ws://localhost:{}/ws'.format(test_port+2))
        self.client = await self.client

    def tearDown(self):
        with entanglement_logs_disabled():
            try:
                self.client.close()
            except AttributeError: pass
            self.http_server.stop()
            settle_loop(self.loop)
        super().tearDown()

    def testInit(self):
        "Confirm setUp at least works"
        ioloop.run_sync(self.wait_for_client)


    @tornado.testing.gen_test
    async def testSendMessage(self):
        await self.wait_for_client()
        self.client.write_message(json.dumps(
            {'_sync_type': 'TableInherits',
             'info': 'foobaz',
             '_sync_operation': 'create',
             '_flags': 1}))
        m = await self.client.read_message()
        js = json.loads(m)
        self.assertEqual(js['_sync_type'], 'SyncBadOwner')

    @tornado.testing.gen_test
    async def testReceive(self):
        await self.wait_for_client()
        t = TableInherits(info = "baz")
        sess = Base.registry.sessionmaker()
        sess.manager = self.server
        sess.add(t)
        sess.commit()
        m =await self.client.read_message()
        m = json.loads(m)
        self.assertEqual(m['_sync_type'], 'TableInherits')
        self.assertEqual(m['id'], str(t.id))

    async def helper_testTransition(self):
        tp = transitions_partitioned()
        tp.__enter__()
        self.addCleanup(tp.__exit__, None, None, None)
        Base.registry.register_operation('transition', operations.transition_operation)
        manager_registry.register_operation('transition', operations.transition_operation)
        await self.client_destination.connected_future
        t = TableTransition(info = "test")
        self.session.manager = self.manager
        t.sync_owner = self.session.query(SyncOwner).filter_by(dest_hash = None).one()
        self.session.add(t)
        self.session.commit()

    async def  helper_testBrokenTransition(self):
        tp = transitions_partitioned()
        tp.__enter__()
        self.addCleanup(tp.__exit__, None, None, None)
        Base.registry.register_operation('transition', operations.transition_operation)
        manager_registry.register_operation('transition', operations.transition_operation)


    for t in glob.glob(js_test_path+"/wstest*.js"):
        test_method_name = t[len(js_test_path)+3:-3]
        locals()[test_method_name] = javascriptTest(t, test_method_name)

def test_sync_registry(loop):
    future =  run_js_test("testSyncRegistry.js")
    loop.run_until_complete(future)
    
def test_sync_receive_registry(layout_module):
    layout = layout_module
    future = run_js_test('testSyncReceiveRegistry.js')
    def send_obj(connected_future):
        ti = TableInherits()
        ti.info = 90
        ti.info2 = 20
        layout.server.session.add(ti)
        layout.server.session.commit()
    layout.server.websocket_destination.connected_future.add_done_callback(send_obj)
    layout.loop.run_until_complete(future)
    print(future.result())
    

def test_sync_orig(layout_module):
    layout = layout_module
    # This test also tests that syncConstruct works  correctly.
    future = run_js_test('testSyncOrig.js')
    ti = TableInherits()
    def send_obj(connected_future):
        nonlocal ti
        ti.info2 = 19
        ti.info = 99
        layout.server.session.add(ti)
        layout.server.session.commit()
    loop = layout.loop
    connected_future = layout.server.websocket_destination.connected_future = loop.create_future()
    connected_future.add_done_callback(send_obj)
    loop.run_until_complete(asyncio.wait([connected_future], timeout=1))
    ti.info = 0
    # Now we send with only some attributes to make sure _orig caches old values
    layout.server.manager.synchronize(ti, operation = 'forward',
                                          attributes_to_sync = {'id', 'info'})
    loop.run_until_complete(future)
    print(str(future.result(), 'utf-8'))
    
def test_sync_events(layout_module):
    layout = layout_module
    future = run_js_test('testSyncEvents.js')
    def send_obj(connected_future):
        ti = TableInherits()
        ti.info = 90
        ti.info2 = 20
        layout.server.session.add(ti)
        layout.server.session.commit()
    connected_future = layout.server.websocket_destination.connected_future = layout.loop.create_future()
    layout.server.websocket_destination.connected_future.add_done_callback(send_obj)
    layout.loop.run_until_complete(future)
    print(future.result())

def test_schemas(loop, layout_module):
    layout = layout_module
    future = run_js_test("testSchemas.js")
    def send_obj(connected_future):
        ti = TableInherits()
        ti.info = 90
        ti.info2 = 20
        layout.server.session.add(ti)
        layout.server.session.commit()
    connected_future = layout.server.websocket_destination.connected_future = layout.loop.create_future()
    layout.server.websocket_destination.connected_future.add_done_callback(send_obj)
    layout.loop.run_until_complete(future)
    print(future.result())
    
def test_persistence(loop, layout_module, monkeypatch):
    entanglement.protocol.protocol_logger.setLevel(10)

    layout = layout_module
    future = run_js_test("testPersistence.js")
    phase = TestPhase()
    phase.phase = 1
    layout.server.session.add(phase)
    layout.server.session.commit()
    websocket_destination = SqlSyncDestination(b'Q'*32, "sql websocket")
    monkeypatch.setattr(layout.server, "websocket_destination", websocket_destination)
    layout.loop.run_until_complete(future)
    print(future.result())
    
