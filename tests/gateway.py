# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, copy, datetime, gc, json, ssl, unittest, uuid, warnings
from contextlib import contextmanager
from unittest import mock

from hadron.entanglement.interface import Synchronizable, sync_property, SyncRegistry
from hadron.entanglement.network import  SyncServer,  SyncManager
from hadron.entanglement.util import certhash_from_file, CertHash, SqlCertHash, get_or_create, entanglement_logs_disabled
from sqlalchemy import create_engine, Column, Integer, inspect, String, ForeignKey
from sqlalchemy.orm import sessionmaker
from hadron.entanglement.sql import SqlSynchronizable,  sync_session_maker, sql_sync_declarative_base, SqlSyncDestination, SqlSyncRegistry, sync_manager_destinations
import hadron.entanglement.sql as sql
from .utils import wait_for_call, SqlFixture

# SQL declaration
Base = sql_sync_declarative_base()



class TableBase(Base):
    __tablename__ = "base_table"

    id = Column(String(128), primary_key = True,
                default = lambda: str(uuid.uuid4()))
    type = Column(String, nullable = False)
    __mapper_args__ = {
        'polymorphic_on': 'type',
        'polymorphic_identity': 'base'}

class TableInherits(TableBase):
    __tablename__ = "inherits_table"
    id = Column(String(128),
                ForeignKey(TableBase.id, ondelete = "cascade"),
                primary_key = True)
    info = Column(String(30))
    info2 = Column(String(30))
    __mapper_args__ = {'polymorphic_identity': "inherits"}

manager_registry = SqlSyncRegistry()
manager_registry.registry = Base.registry.registry

client_registry = SqlSyncRegistry()
client_registry.registry = Base.registry.registry

class TestGateway(SqlFixture, unittest.TestCase):

    def __init__(self, *args, **kwargs):
        self.base = Base
        self.manager_registry = manager_registry
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.client_engine = create_engine('sqlite:///:memory:', echo = False)
        client_registry.sessionmaker.configure(bind = self.client_engine)
        client_registry.create_bookkeeping(self.client_engine)
        Base.metadata.create_all(self.client_engine)
        self.client = SyncManager(cafile = "ca.pem",
                                 cert = "host3.pem", key = "host3.key",
                                 port = 9120,
                                 registries = [client_registry],
                                 loop = self.loop)
        self.to_client = SqlSyncDestination(certhash_from_file("host3.pem"), 'client',
                                            server_hostname = 'host3')
        self.client_to_server = self.client.session.merge(self.to_server)
        self.client_to_server.server_hostname = 'host1'
        self.server.add_destination(self.to_client)
        self.client.add_destination(self.client_to_server)
        with wait_for_call(self.loop,
                           sql.internal.sql_meta_messages,
                           'handle_i_have', 6):
            self.client.run_until_complete(asyncio.wait(self.client._connecting.values()))
        self.client_session = client_registry.sessionmaker()
        self.client_session.manager = self.client

    def tearDown(self):
        self.client.close()
        del self.client
        super().tearDown()
        


    def testGatewayFlood(self):
        "When a client creates an object it floods across to another client"
        session = self.client_session
        t = TableBase()
        session.add(t)
        with wait_for_call(self.loop, self.base.registry, 'incoming_sync'):
            session.commit()
        t2 = self.manager.session.query(TableBase).all()
        self.assertEqual(t2[0].id, t.id)

    def testRightOwners(self):
        "Test that owners are flooded correctly"
        owner_uuids = set()
        for o in (self.client, self.server, self.manager):
            owners = o.session.query(sql.SyncOwner).filter(sql.SyncOwner.destination == None).all()
            self.assertEqual(len(owners), 1)
            owner_uuids.add(owners[0].id)
        self.assertEqual(len(owner_uuids), 3)
        for m in (self.client, self.manager, self.server):
            owners = m.session.query(sql.SyncOwner).all()
            self.assertEqual(len(owners), 3)
            for o in owners:
                self.assertIn(o.id, owner_uuids)
                

if __name__ == '__main__':
    import logging, unittest, unittest.main
#    logging.basicConfig(level = 'ERROR')
    logging.basicConfig(level = 10)
    #logging.getLogger('hadron.entanglement.protocol').setLevel(10)
    unittest.main(module = "tests.gateway")
