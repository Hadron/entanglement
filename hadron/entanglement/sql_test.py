#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, ssl, unittest
from contextlib import contextmanager
from unittest import mock

from .interface import Synchronizable, sync_property, SyncRegistry
from .network import  SyncServer,  SyncManager
from .util import certhash_from_file, CertHash, SqlCertHash, get_or_create
from sqlalchemy import create_engine, Column, Integer, inspect
from sqlalchemy.orm import sessionmaker
from .sql import SqlSynchronizable,  sync_session_maker, sql_sync_declarative_base, SqlSyncDestination
from . import sql

@contextmanager
def wait_for_call(loop, obj, method, calls = 1):
    fut = loop.create_future()
    num_calls = 0
    def cb(*args, **kwards):
        nonlocal num_calls
        if not fut.done():
            num_calls +=1
            if num_calls >= calls:
                fut.set_result(True)
    try:
        with mock.patch.object(obj, method,
                               wraps = getattr(obj, method),
                               side_effect = cb):
            yield
            loop.run_until_complete(asyncio.wait_for(fut, 0.5))
    except  asyncio.futures.TimeoutError:
        raise AssertionError("Timeout waiting for call to {} of {}".format(
            method, obj)) from None

# SQL declaration
Base = sql_sync_declarative_base()

class Table1(Base, SqlSynchronizable):
    __tablename__ = 'test_table'
    id = Column(Integer, primary_key = True)
    ch = Column(SqlCertHash)
            
class TestSql(unittest.TestCase):

    def setUp(self):
        sql.internal.you_have_timeout = 0 #Send YouHave serial number updates immediately for testing
        self.e1 = create_engine('sqlite:///:memory:', echo = False)
        self.e2 = create_engine('sqlite:///:memory:', echo = False)
        Session = sync_session_maker()
        self.session = Session(bind = self.e2)
        Base.registry.sessionmaker.configure(bind = self.e1)
        Base.metadata.create_all(bind = self.e1)
        Base.metadata.create_all(bind = self.e2)
        Base.registry.create_bookkeeping(self.e1)
        Base.registry.create_bookkeeping(self.e2)
        self.server = SyncServer(cafile = "ca.pem",
                                 cert = "host1.pem", key = "host1.key",
                                 port = 9120,
                                 registries = [Base.registry])
        self.manager = SyncManager(cafile = "ca.pem",
                                   cert = "host2.pem",
                                   key = "host2.key",
                                   loop = self.server.loop,
                                   registries = [Base.registry],
                                   port = 9120)
        self.loop = self.server.loop
        self.manager.session = Base.registry.sessionmaker(bind = self.e2)
        self.d1 = SqlSyncDestination(certhash_from_file("host1.pem"),
                                  "server", host = "127.0.0.1",
                                  server_hostname = "host1")
        self.d2 = SqlSyncDestination(certhash_from_file("host2.pem"),
                                  "client")
        self.server.add_destination(self.d2)
        self.manager.add_destination(self.d1)
        self.manager.run_until_complete(asyncio.wait(self.manager._connecting.values()))
        self.manager.run_until_complete(asyncio.wait([x.sync_drain() for x in self.manager.connections + self.server.connections]))
        

    def tearDown(self):
        self.manager.close()
        self.server.close()
        self.session.close()
        
    def testCertHash(self):
        t = Table1()
        t.ch = CertHash(b'o' *32)
        self.session.add(t)
        self.session.commit()
        #That will expire t.ch
        self.assertEqual(t.ch, CertHash(b'o' *32))
        as_sync = t.to_sync()
        self.assertEqual(set(as_sync.keys()),
                         {'id', 'ch', 'sync_serial'})

    def testSendObject(self):
        self.session.manager = self.manager
        t = Table1(ch = self.d1.cert_hash)
        with wait_for_call(self.loop, Base.registry, 'sync_receive'):
            self.session.add(t)
            self.session.commit()
        t2 = self.server.session.query(Table1).get(t.id)
        assert t2 is not None
        assert t2.sync_owner.destination.cert_hash is not None
        self.assertEqual(t2.ch, t.ch)

    def testGetOrCreate(self):
        "test our get or create method"
        session = self.session
        session.autoflush = False
        t1 = Table1(ch = self.d2.cert_hash)
        session.add(t1)
        session.commit()
        t2 = get_or_create(session, Table1, {'id' : t1.id})
        self.assertIs(t1,t2)
        dest = get_or_create(session, sql.SqlSyncDestination,
                             {'cert_hash': self.d1.cert_hash},
                             {'name' : "blah", 'host': "127.0.0.1"})
        session.commit()
        dest2 = get_or_create(session, sql.SqlSyncDestination,
                              {'cert_hash' : dest.cert_hash})
        self.assertEqual(dest.id, dest2.id)

    def testYouHave(self):
        "Test sending of you_have messages"
        with wait_for_call(self.loop, sql.internal.sql_meta_messages, 'handle_you_have'):
            self.testSendObject()
        self.assertEqual(self.d2.incoming_serial, 1)

    def testInitialSync(self):
        #disconnect our session
        self.session.manager = None
        self.manager.remove_destination(self.d1)
        self.manager.loop.call_soon(self.manager.loop.stop)
        self.manager.loop.run_forever()
        session = self.session
        t1 = Table1(ch = self.d2.cert_hash)
        session.add(t1)
        session.commit()
        self.d1.connect_at = 0
        self.manager.run_until_complete(self.manager.add_destination(self.d1))
        self.manager.loop.run_until_complete(asyncio.wait([x.sync_drain() for x in self.manager.connections + self.server.connections]))
        with wait_for_call(self.loop, Base.registry, 'sync_receive'): pass
        t2 = self.server.session.query(Table1).get(t1.id)
        assert t2 is not None
        assert t2.ch == t1.ch
        
                                    
#import logging
#logging.basicConfig(level = 'ERROR')

if __name__ == '__main__':
    import logging, unittest, unittest.main
    logging.basicConfig(level = 'ERROR')
#    logging.basicConfig(level = 10)
    unittest.main(module = "hadron.entanglement.sql_test")
    
    
