# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import sys, os.path
sys.path = list(filter(lambda p: p != os.path.abspath(os.path.dirname(__file__)), sys.path))
import asyncio, json, unittest, uuid
from tornado.platform.asyncio import AsyncIOMainLoop

try: AsyncIOMainLoop().install()
except: pass
import tornado.web, tornado.websocket, tornado.ioloop, tornado.testing, tornado.httpserver
import entanglement.protocol
from entanglement import SyncServer, SyncDestination
from entanglement.util import entanglement_logs_disabled
from entanglement.sql import sql_sync_declarative_base, SqlSyncRegistry, SyncOwner
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

manager_registry = SqlSyncRegistry()
manager_registry.registry = Base.registry.registry

class TestWebsockets(SqlFixture, unittest.TestCase):

    def __init__(self, *args, **kwargs):
        self.base = Base
        self.manager_registry = manager_registry
        super().__init__(*args, **kwargs)

    def setUp(self):
        def find_sync_destination( request, *args, **kwargs):
            return SyncDestination(b'n' * 32, 'websocket')
        super().setUp()
        self.app = tornado.web.Application([(r'/ws', SyncWsHandler)])
        self.http_server = tornado.httpserver.HTTPServer(self.app)
        self.http_server.listen(test_port+2)
        self.app.sync_manager = self.manager
        self.app.find_sync_destination = find_sync_destination
        self.client = tornado.websocket.websocket_connect(
            'ws://localhost:{}/ws'.format(test_port+2))
        ioloop.run_sync(self.wait_for_client)
        self.io_loop = ioloop
        settle_loop(self.loop)

    async def wait_for_client(self):
        self.client = await self.client

    def tearDown(self):
        with entanglement_logs_disabled():
            self.client.close()
            self.http_server.stop()
            settle_loop(self.loop)
        super().tearDown()

    def testInit(self):
        "Confirm setUp at least works"
        pass

    @tornado.testing.gen_test
    async def testSendMessage(self):
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
        t = TableInherits(info = "baz")
        sess = Base.registry.sessionmaker()
        sess.manager = self.server
        sess.add(t)
        sess.commit()
        m =await self.client.read_message()
        m = json.loads(m)
        self.assertEqual(m['_sync_type'], 'TableInherits')
        self.assertEqual(m['id'], str(t.id))

    def testJavascript(self):
        self.client.close()
        print('ws://localhost:{}/ws'.format(test_port+2))
        q = self.session.query(SyncOwner).all()
        print(str(q[0].id))
        self.loop.run_forever()

        
if __name__ == '__main__':
    import logging, unittest, unittest.main
#    logging.basicConfig(level = 'ERROR')
    logging.basicConfig(level = 10)
    entanglement.protocol.protocol_logger.setLevel(10)
    unittest.main(module = "tests.websocket")
