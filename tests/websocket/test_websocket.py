# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import sys, os.path
sys.path = list(filter(lambda p: p != os.path.abspath(os.path.dirname(__file__)), sys.path))
import asyncio, concurrent.futures, glob, json, threading, subprocess, unittest, uuid
from tornado.platform.asyncio import AsyncIOMainLoop

try: AsyncIOMainLoop().install()
except: pass
import tornado.web, tornado.websocket, tornado.ioloop, tornado.testing, tornado.httpserver
import entanglement.protocol
from entanglement import SyncServer, SyncDestination, operations
from entanglement.util import entanglement_logs_disabled
from entanglement.sql import sql_sync_declarative_base, SqlSyncRegistry, SyncOwner
from entanglement.sql.transition import SqlTransitionTrackerMixin
from entanglement.websocket import SyncWsHandler
from sqlalchemy import Column, String, ForeignKey
from entanglement.util import GUID
from tests.utils import *
ioloop = tornado.ioloop.IOLoop.current()
# SQL declaration
Base = sql_sync_declarative_base()

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
            output = subprocess.check_output(['nodejs', self.testname,
                                                   self.uri, self.owner],
                                                  timeout = 0.7,
                                                  cwd = os.path.dirname(self.testname))
            self.future.set_result(output)
        except subprocess.CalledProcessError:
            self.future.set_exception(AssertionError())
        except Exception as e:
            self.future.set_exception(e)


def javascriptTest(test_name, method_name):
    def testMethod(self):
        uri = "ws://localhost:{}/ws".format(test_port+2)
        sess = Base.registry.sessionmaker()
        q = sess.query(SyncOwner).all()
        owner = str(q[0].id)
        t = JsTest(test_name, uri, owner)
        t.start()
        helper_method = getattr(self, 'helper_'+method_name, None)
        if helper_method:
            ioloop.add_callback(helper_method)
        
        self.loop.run_until_complete(asyncio.futures.wrap_future(t.future))
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
        t.sync_owner = self.session.query(SyncOwner).filter_by(destination_id = None).one()
        self.session.add(t)
        self.session.commit()

    async def  helper_testBrokenTransition(self):
        tp = transitions_partitioned()
        tp.__enter__()
        self.addCleanup(tp.__exit__, None, None, None)
        Base.registry.register_operation('transition', operations.transition_operation)
        manager_registry.register_operation('transition', operations.transition_operation)


    js_test_path = os.path.abspath(os.path.dirname(__file__))
    for t in glob.glob(js_test_path+"/test*.js"):
        test_method_name = t[len(js_test_path)+1:-3]
        locals()[test_method_name] = javascriptTest(t, test_method_name)
