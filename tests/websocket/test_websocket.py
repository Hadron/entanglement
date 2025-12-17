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
import pytest_asyncio
import asyncio, concurrent.futures, glob, json, threading, subprocess, unittest, uuid, inspect
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


@pytest.fixture(scope='session')
def event_loop():
    """
    Ensure pytest-asyncio uses the same loop  so
    web server, SyncManager, and async tests share a single event loop.
    """
    loop = asyncio.get_event_loop()
    yield loop
    settle_loop(loop)


js_helpers = {}

def register_jstest_helper(js_name):
    """
    Decorator to register helper functions for specific JS tests.
    Helpers receive (layout, future) and may be sync or async.
    """
    def decorator(func):
        js_helpers[js_name] = func
        return func
    return decorator


def pytest_generate_tests(metafunc):
    if "js_file" in metafunc.fixturenames:
        files = sorted(glob.glob(os.path.join(js_test_path, "wstest*.js")))
        ids = [os.path.basename(f) for f in files]
        metafunc.parametrize("js_file", files, ids=ids)


@register_jstest_helper("wstestTransition.js")
async def helper_wstest_transition(layout, future):
    cm = transitions_partitioned()
    cm.__enter__()
    await layout.server.websocket_destination.connected_future
    session = layout.server.session
    t = TableTransition(info="test")
    t.sync_owner = list(session.query(SyncOwner).filter_by(dest_hash=None).all())[0]
    session.add(t)
    session.commit()
    return lambda: cm.__exit__(None, None, None)


@register_jstest_helper("wstestBrokenTransition.js")
def helper_wstest_broken_transition(layout, future):
    cm = transitions_partitioned()
    cm.__enter__()
    layout.server.registries[0].register_operation('transition', operations.transition_operation)
    layout.client.registries[0].register_operation('transition', operations.transition_operation)
    return lambda: cm.__exit__(None, None, None)

js_test_path = os.path.abspath(os.path.dirname(__file__))

# SQL declaration
Base = sql_sync_declarative_base()
entanglement.javascript_schema.javascript_registry(Base.registry, "websocket_test")
Base.registry.register_operation('transition', operations.transition_operation)

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
    info2 = Column(Integer)
    __mapper_args__ = {'polymorphic_identity': "inherits"}

class TableTransition(TableInherits, SqlTransitionTrackerMixin):
    __mapper_args__ = {
        'polymorphic_identity': 'transition'
        }

class TestPhase(Base):

    __test__ = False

    __tablename__ = "test_phase"

    id = Column(GUID, primary_key = True,
                default = lambda: uuid.uuid4())

    phase = Column(Integer, nullable = False)

    def sync_receive_constructed(self, *args, **kwargs):
        super().sync_receive_constructed(*args, **kwargs)
        operation = kwargs['operation']
        manager = kwargs['manager']
        context = kwargs['context']
        registry = kwargs['registry']
        if operation == 'forward' and self.phase == 3:
            referenced = Referenced(id = TestPhase.referencing.referenced)
            TestPhase.server_session.add(referenced)
            TestPhase.server_session.commit()
        if operation == 'forward' and self.phase in (4,5) and self.sync_is_local:
            session = context.session
            session.commit() # save ourselves to avoid locks
            owner = self.sync_owner
            session = registry.sessionmaker()
            session.manager = manager
            if self.phase == 4:
                obj = session.query(TableTransition).filter_by(sync_owner = owner).one()
                obj.info = "updated"
                session.commit()
            elif self.phase == 5:
                #In phase 5 we delete our owner and clear all its objects.
                owner.clear_all_objects(manager = manager)
                #new session
                owner = session.merge(owner)
                session.delete(owner)
                session.commit()
            

class Referencing(Base):
    __tablename__ = 'referencing'
    id = Column(GUID, primary_key = True,
                default = lambda: uuid.uuid4())

    referenced = Column(GUID, nullable = False)

class Referenced(Base):
    __tablename__ = "referenced"
    id = Column(GUID, primary_key = True,
                default = lambda: uuid.uuid4())

            
            
        
        
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
        except subprocess.TimeoutExpired as e:
            self.future.set_exception(AssertionError(f'Process timed out: {e.stdout}, {e.stderr}'))
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


    

@pytest.mark.asyncio
async def test_send_message(websocket_test_context):
    ctx = websocket_test_context
    await ctx.wait_for_client()
    ctx.client.write_message(json.dumps(
        {'_sync_type': 'TableInherits',
         'info': 'foobaz',
         '_sync_operation': 'create',
         '_flags': 1}))
    m = await ctx.client.read_message()
    js = json.loads(m)
    assert js['_sync_type'] == 'SyncBadOwner'

@pytest.mark.asyncio
async def test_sync_receive(websocket_test_context):
    ctx = websocket_test_context
    await ctx.wait_for_client()
    sess = ctx.server.session
    t = TableInherits(info="baz")
    sess.add(t)
    sess.commit()
    message = await ctx.client.read_message()
    js = json.loads(message)
    assert js["_sync_type"] == "TableInherits"
    assert js["id"] == str(t.id)


def test_sync_registry(loop):
    future =  run_js_test("testSyncRegistry.js")
    loop.run_until_complete(future)
    
def test_sync_receive_registry(layout_module):
    layout = layout_module
    future = run_js_test('testSyncReceiveRegistry.js')
    def send_obj(connected_future):
        ti = TableInherits()
        ti.info = '90'
        ti.info2 = 20
        layout.server.session.add(ti)
        layout.server.session.commit()
    layout.server.websocket_destination.connected_future.add_done_callback(send_obj)
    layout.loop.run_until_complete(future)
    print(future.result())
    

def test_sync_orig(layout_module):
    layout = layout_module
    # This test also tests that syncConstruct works  correctly.
    future = run_js_test('testSyncOrig.js', layout.server.registries[0].sessionmaker)
    ti = TableInherits()
    def send_obj(connected_future):
        nonlocal ti
        ti.info = '99'
        ti.info2 = 19
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

    
def test_sync_events(layout_module):
    layout = layout_module
    future = run_js_test('testSyncEvents.js')
    def send_obj(connected_future):
        ti = TableInherits()
        ti.info = '90'
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
        ti.info = '90'
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
    phase.sync_owner = SyncOwner()
    phase.phase = 1
    layout.server.session.add(phase)
    referencing = Referencing()
    #We start by referencing a non-existent object that we'll create later.
    # This allows the other side to test out missing_node support in relationships
    referencing.referenced = uuid.uuid4()
    referencing.sync_owner = phase.sync_owner
    layout.server.session.add(referencing)
    layout.server.session.commit()
    # This is gross and hackish
    # We want to be able to create a Referenced and commit it to the server session by the time we get to phase 3
    # Easiest place to do that is in  sync_receive_constructed in TestPhase
    # But we need the session and the referenceing uuid there
    TestPhase.server_session = layout.server.session
    TestPhase.referencing = referencing
    websocket_destination = SqlSyncDestination(b'Q'*32, "sql websocket")
    monkeypatch.setattr(layout.server, "websocket_destination", websocket_destination)
    with transitions_partitioned():
        layout.loop.run_until_complete(future)
    print(future.result())
    
@pytest.mark.asyncio
async def test_auto_classes(loop, layout_module, monkeypatch):
    entanglement.protocol.protocol_logger.setLevel(10)
    layout = layout_module
    layout.server.websocket_destination.connected_future = layout.loop.create_future()
    future = run_js_test("testPersistentAutoClass.js", layout.server.registries[0].sessionmaker)
    await layout.server.websocket_destination.connected_future
    ti = TableInherits()
    ti.info = 'string'
    ti.info2 = 20
    ti.sync_owner = SyncOwner()
    layout.server.session.add(ti)
    layout.server.session.commit()
    await future
    print(future.result())
    

@pytest.mark.asyncio
async def test_js_files(js_file, layout_module):
    """
    Pytest-native runner for JS websocket tests discovered dynamically.
    """
    layout = layout_module
    js_name = os.path.basename(js_file)
    future = run_js_test(js_name, layout.server.registries[0].sessionmaker)
    helper = js_helpers.get(js_name)
    cleanup = None
    if helper is not None:
        res = helper(layout, future)
        if inspect.iscoroutine(res):
            res = await res
        if callable(res):
            cleanup = res
    try:
        await future
    finally:
        if cleanup:
            cleanup()

class WebsocketTestContext:
    """
    Helper to mirror the websocket-specific setup/teardown from TestWebsockets
    using the modern layout fixture. Provides access to the tornado app,
    websocket destination, loop, and a convenience connector for clients.
    """
    def __init__(self, layout):
        self.layout = layout
        self.loop = layout.loop
        self.server = layout.server
        self.manager = layout.server.manager
        self.client_destination = layout.server.websocket_destination
        self.app = layout.server.web_app
        self.http_server = layout.server.http_server
        self.client = None

    async def wait_for_client(self):
        self.client = await tornado.websocket.websocket_connect(
            f'ws://localhost:{test_port+2}/ws')
        return self.client


@pytest.fixture()
def websocket_test_context(layout):
    """
    Fixture that exposes the websocket server/app created by the layout
    fixture and mirrors TestWebsockets' setup/teardown behavior. Does not
    modify the existing unittest-based tests; intended for pytest ports.
    """
    ctx = WebsocketTestContext(layout)
    yield ctx
    with entanglement_logs_disabled():
        if ctx.client is not None:
            try:
                ctx.client.close()
            except Exception:
                pass
        settle_loop(ctx.loop)


@pytest.fixture()
def websocket_client(websocket_test_context):
    """
    Convenience fixture to open a websocket client connection against the
    layout-provided websocket server. Closes the client and settles the loop
    on teardown via websocket_test_context.
    """
    websocket_test_context.loop.run_until_complete(
        websocket_test_context.wait_for_client())
    return websocket_test_context.client
