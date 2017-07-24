#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, datetime, gc, json, ssl, unittest, uuid, warnings
from contextlib import contextmanager
from unittest import mock

from hadron.entanglement.interface import Synchronizable, sync_property, SyncRegistry
from hadron.entanglement.network import  SyncServer,  SyncManager
from hadron.entanglement.util import certhash_from_file, CertHash, SqlCertHash, get_or_create, entanglement_logs_disabled
from sqlalchemy import create_engine, Column, Integer, inspect, String, ForeignKey
from sqlalchemy.orm import sessionmaker
from hadron.entanglement.sql import SqlSynchronizable,  sync_session_maker, sql_sync_declarative_base, SqlSyncDestination, SqlSyncRegistry, sync_manager_destinations, SyncOwner
import hadron.entanglement.sql as sql
from .utils import wait_for_call, SqlFixture, settle_loop


# SQL declaration
Base = sql_sync_declarative_base()

class Table1(Base, SqlSynchronizable):
    __tablename__ = 'test_table'
    id = Column(Integer, primary_key = True)
    ch = Column(SqlCertHash)


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

class AlternateSyncDestination(SqlSyncDestination):


    def new_method(self): pass

manager_registry = SqlSyncRegistry()
manager_registry.registry = Base.registry.registry
    
    
class TestSql(SqlFixture, unittest.TestCase):

    def __init__(self, *args, **kwargs):
        self.base = Base
        self.manager_registry = manager_registry
        super().__init__(*args, **kwargs)
        

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
            #While we're add it make sure that post-commit changes are not sent
            t.ch = CertHash(b'o' *32)
        t2 = self.server.session.query(Table1).get(t.id)
        assert t2 is not None
        assert t2.sync_owner.destination.cert_hash is not None
        self.session.refresh(t)
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


    def testInitialSync(self):
        #disconnect our session
        self.session.manager = None
        self.manager.remove_destination(self.d1)
        with entanglement_logs_disabled():
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

    def testJoinedTable(self):
        "Test Joined Table Inheritance"
        def to_sync_cb(self, attributes):
            nonlocal calls
            calls += 1
            return orig_to_sync(self, attributes)
        orig_to_sync = Base.to_sync
        calls = 0
        #disconnect our session
        self.session.manager = None
        self.manager.remove_destination(self.d1)
        with entanglement_logs_disabled():
            self.manager.loop.call_soon(self.manager.loop.stop)
            self.manager.loop.run_forever()
            session = self.session
            with mock.patch.object(TableBase, 'to_sync', new = to_sync_cb):
                t = TableInherits()
                t.info = "1 2 3"
                session.add(t)
                session.commit()
                self.d1.connect_at = 0
                with wait_for_call(self.loop, TableInherits, 'sync_receive_constructed'):
                    self.manager.run_until_complete(self.manager.add_destination(self.d1))
        t2 = self.server.session.query(TableInherits).get(t.id)
        assert t2.__class__ is  t.__class__
        assert t.info == t2.info
        self.assertEqual(calls, 1)


    def testAlternateRegistries(self):
        class NewRegistry(SqlSyncRegistry): pass
        base = sql_sync_declarative_base(registry_class = NewRegistry)
        class obj(base):
            __tablename__ = "foo"
            id = Column(Integer, primary_key = True)

        assert isinstance(base.registry, NewRegistry)

    def testAlternativeSyncDestination(self):
        "Confirm that sync_manager_destinations supports the cls parameter"
        self.server.remove_destination(self.d2)
        self.server.session.add(self.d2)
        self.server.session.commit()
        self.server.session.refresh(self.d2)
        self.server.session.expunge_all()
        sync_manager_destinations(manager = self.server, cls = AlternateSyncDestination)
        for c in self.server.destinations:
            self.assertIsInstance(c, AlternateSyncDestination)
        self.assertIn( self.d2, self.server.destinations)

    def testSyncManagerDestinations(self):
        "Test the sync_manager_destinations function"
        self.server.remove_destination(self.d2)
        self.d1.id = 30
        self.d2.id = 35
        self.server.session.merge(self.d1)
        self.server.session.add(self.d2)
        self.server.session.commit()
        assert self.d2.protocol is None
        self.server.add_destination(self.d2)
        sync_manager_destinations( manager = self.server)
        self.assertIn( self.d1, self.server.destinations)
        self.assertIn( self.d2, self.server.destinations)
        self.assertEqual(len(self.server.destinations), 2)
        self.server.session.delete(self.d2)
        sync_manager_destinations( manager = self.server)
        self.assertNotIn( self.d2, self.server.destinations)
        self.assertEqual(len(self.server.destinations), 1)

    def testForceResync(self):
        "Confirm that we can force resync using sync_manager_destinations"
        self.server.remove_destination(self.d2)
        self.d1.connect_at = 0
        self.server.session.add(self.d2)
        with entanglement_logs_disabled(), \
             wait_for_call(self.loop, sql.internal.sql_meta_messages, "handle_wrong_epoch"):
            sync_manager_destinations(self.server, force_resync = True)

    def testDelete(self):
        "Test Object Deletion"
        s = self.manager_registry.sessionmaker()
        s.manager = self.manager
        t = Table1()
        t.ch = self.d1.cert_hash
        s.add(t)
        with wait_for_call(self.loop, sql.internal.sql_meta_messages, 'handle_you_have'):
            s.commit()
        s.delete(t)
        t2 = self.server.session.query( Table1).filter_by(
                id = t.id).one()
        assert t.ch == t2.ch
        m = mock.MagicMock( wraps= Base.registry.incoming_delete)
        with mock.patch.object(Base.registry, 'incoming_delete', new = m): 
            with wait_for_call(self.loop, sql.internal.sql_meta_messages, 'handle_you_have'):
                s.commit()
        self.assertEqual( m.call_count, 1)
        t2 = self.server.session.query( Table1).filter_by(
            id = t.id).all()
        assert t2 == []
        self.assertEqual( self.d2.owners[0].incoming_serial, t.sync_serial)
        deleted = s.query( sql.base.SyncDeleted).first()
        self.assertEqual( deleted.sync_serial, t.sync_serial)
        self.assertEqual( t.to_sync(attributes = t.sync_primary_keys), json.loads(deleted.primary_key))

    def testDeleteIHave(self):
        "Test that at connection start up, IHave handling will delete objects"
        t = Table1(ch = self.d2.cert_hash)
        s = self.manager_registry.sessionmaker()
        s.manager = self.manager
        s.add(t)
        with wait_for_call(self.loop,
                           sql.internal.sql_meta_messages, 'handle_you_have'):
            s.commit()
        with entanglement_logs_disabled():
            self.manager.remove_destination(self.d1)
            self.loop.call_soon(self.loop.stop)
            self.loop.run_forever()
            self.loop.run_until_complete(asyncio.sleep(0.1))
        s.delete(t)
        s.commit()
        self.assertEqual(self.manager.connections, [])
        self.assertEqual(self.server.connections, [])
        self.d1.connect_at = 0
        self.manager.add_destination(self.d1)
        self.assertEqual(
            self.server.session.query(Table1).filter_by(id = t.id).count(),
                1)
        self.assertEqual(s.query(sql.base.SyncDeleted).count(), 1)
        with wait_for_call(self.loop, sql.internal.sql_meta_messages,
                               'handle_you_have'):
            pass
        self.assertEqual(
            self.server.session.query(Table1).filter_by(id = t.id).count(),
            0)

    def testRemoteUpdate(self):
        "Test that we can update an object from the remote side"
        t = TableInherits(info = "blah")
        self.session.add(t)
        self.session.manager = self.manager
        with wait_for_call(self.loop, sql.internal.sql_meta_messages, 'handle_you_have'):
            self.session.commit()
        server_session = self.base.registry.sessionmaker()
        server_session.manager = self.server
        t2 = server_session.query(TableInherits).filter_by(
            id = t.id).one()
        t2.info = "bazfutz"
        with wait_for_call(self.loop, sql.internal.sql_meta_messages, 'handle_you_have'):
            server_session.sync_commit()
            server_session.commit()
        # refetches because of expire after commit
        self.assertEqual(t.id, t2.id)

    def testRemoteDelete(self):
        "Test we can delete an object not from its owner"
        t = TableInherits(info = "blah")
        self.session.add(t)
        self.session.manager = self.manager
        with wait_for_call(self.loop, sql.internal.sql_meta_messages, 'handle_you_have'):
            self.session.commit()
        server_session = self.base.registry.sessionmaker()
        server_session.manager = self.server
        t2 = server_session.query(TableInherits).filter_by(
            id = t.id).one()
        server_session.delete(t2)
        with wait_for_call(self.loop, sql.internal.sql_meta_messages, 'handle_you_have'):
            server_session.sync_commit()
            server_session.commit()
        res =  self.session.query(TableInherits).all()
        self.assertEqual(res, [])

    @unittest.expectedFailure
    def testRemoteCombinedUpdates(self):
        "Confirm updates with non-overlapping attributes coalesce"
        t = TableInherits(info = "blah")
        self.session.add(t)
        self.session.manager = self.manager
        with wait_for_call(self.loop, sql.internal.sql_meta_messages, 'handle_you_have'):
            self.session.commit()
        server_session = self.base.registry.sessionmaker()
        server_session.manager = self.server
        t2 = server_session.query(TableInherits).filter_by(
            id = t.id).one()
        t2.info = "bazfutz"
        with wait_for_call(self.loop, sql.internal.sql_meta_messages, 'handle_you_have'):
            server_session.sync_commit()
            t2 = server_session.query(TableInherits).get(t2.id)
            t2.info2 = "quux"
            server_session.sync_commit()
            self.session.expire(t)
        self.assertEqual(t.id, t2.id)
        self.assertEqual(t.info2, t2.info2)
        self.assertEqual(t.info, t2.info)

    def testSyncDrainCancel(self):
        "Test that one party canceling a coroutine in sync_drain does not affect other parties"
        async def test_drain():
            await list(self.manager.connections)[0].sync_drain()
            
            return True
        self.session.manager = self.manager
        t = Table1(ch = self.manager.cert_hash)
        self.session.add(t)
        self.session.commit()
        t1 = self.loop.create_task(test_drain())
        t2 = self.loop.create_task(test_drain())
        # We cheat by running the loop for one step. so the routines get far enough to allocate futures
        self.loop.call_soon(self.loop.stop)
        self.loop.run_forever()
        t1.cancel()
        res = self.loop.run_until_complete(t2)
        self.assertEqual(res, True)

    def testLocalOwner(self):
        owner = SyncOwner()
        manager_session = manager_registry.sessionmaker()
        manager_session.manager = self.manager
        manager_session.add(owner)
        manager_session.commit()
        settle_loop(self.loop)
        t = TableInherits(info2 = "blah", sync_owner = owner)
        manager_session.add(t)
        manager_session.commit()
        settle_loop(self.loop)
        t2 = self.server.session.query(TableInherits).get(t.id)
        self.assertEqual(t.sync_owner_id, t2.sync_owner_id)
        
        
            
        

#import logging
#logging.basicConfig(level = 'ERROR')

if __name__ == '__main__':
    import logging, unittest, unittest.main
#    logging.basicConfig(level = 'ERROR')
    logging.basicConfig(level = 10)
    unittest.main(module = "tests.sql")
