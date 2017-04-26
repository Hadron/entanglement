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
from hadron.entanglement.sql import SqlSynchronizable,  sync_session_maker, sql_sync_declarative_base, SqlSyncDestination, SqlSyncRegistry, sync_manager_destinations
import hadron.entanglement.sql as sql

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
    
    
class TestSql(unittest.TestCase):

    def setUp(self):
        sql.internal.you_have_timeout = 0 #Send YouHave serial number updates immediately for testing
        warnings.filterwarnings('ignore', module = 'asyncio.sslproto')
        warnings.filterwarnings('ignore', module = 'asyncio.selector_events')
        self.e1 = create_engine('sqlite:///:memory:', echo = False)
        self.e2 = create_engine('sqlite:///:memory:', echo = False)
        Session = sync_session_maker()
        self.session = Session(bind = self.e2)
        Base.registry.sessionmaker.configure(bind = self.e1)
        manager_registry.sessionmaker.configure( bind = self.e2)
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
                                   registries = [manager_registry],
                                   port = 9120)
        self.loop = self.server.loop
        self.d1 = SqlSyncDestination(certhash_from_file("host1.pem"),
                                  "server", host = "127.0.0.1",
                                  server_hostname = "host1")
        self.d2 = SqlSyncDestination(certhash_from_file("host2.pem"),
                                  "client")
        self.server.add_destination(self.d2)
        self.manager.add_destination(self.d1)
        with wait_for_call(self.loop,
                           sql.internal.sql_meta_messages,
                           'handle_i_have', 4):
            self.manager.run_until_complete(asyncio.wait(self.manager._connecting.values()))

        sql.internal.sql_meta_messages.yield_between_classes = False


    def tearDown(self):
        self.manager.close()
        self.server.close()
        self.session.close()
        del self.session
        del self.server
        del self.manager
        del self.d1
        del self.d2
        gc.collect()

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
        self.assertEqual(self.d2.incoming_serial, 1)

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
        def to_sync_cb(self):
            nonlocal calls
            calls += 1
            return orig_to_sync(self)
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
                with wait_for_call(self.loop, TableInherits, 'sync_receive'):
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
        current_epoch = self.d1.incoming_epoch
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
        # Confirm it is not trying to resync
        new_epoch = self.server.destinations.pop().incoming_epoch
        if current_epoch.tzinfo:
            new_epoch = new_epoch.replace(tzinfo = datetime.timezone.utc)
        self.assertEqual(current_epoch, new_epoch)

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
        s = self.manager.session
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
        with mock.patch.dict( Base.registry.operations, 
                              delete = m
        ): 
            with wait_for_call(self.loop, sql.internal.sql_meta_messages, 'handle_you_have'):
                s.commit()
        self.assertEqual( m.call_count, 1)
        t2 = self.server.session.query( Table1).filter_by(
            id = t.id).all()
        assert t2 == []
        self.assertEqual( self.d2.incoming_serial, t.sync_serial)
        deleted = s.query( sql.base.SyncDeleted).first()
        self.assertEqual( deleted.sync_serial, t.sync_serial)
        self.assertEqual( t.to_sync(), json.loads(deleted.primary_key))

    def testDeleteIHave(self):
        "Test that at connection start up, IHave handling will delete objects"
        t = Table1(ch = self.d2.cert_hash)
        s = self.manager.session
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
        t2 = self.server.session.query(TableInherits).filter_by(
            id = t.id).one()
        t2.info = "bazfutz"
        self.server.session.manager = self.server
        with wait_for_call(self.loop, sql.internal.sql_meta_messages, 'handle_you_have'):
            self.server.session.commit()
            # refetches because of expire after commit
        self.assertEqual(t.id, t2.id)

    def testRemoteDelete(self):
        "Test we can delete an object not from its owner"
        t = TableInherits(info = "blah")
        self.session.add(t)
        self.session.manager = self.manager
        with wait_for_call(self.loop, sql.internal.sql_meta_messages, 'handle_you_have'):
            self.session.commit()
        t2 = self.server.session.query(TableInherits).filter_by(
            id = t.id).one()
        self.server.session.delete(t2)
        self.server.session.manager = self.server
        with wait_for_call(self.loop, sql.internal.sql_meta_messages, 'handle_you_have'):
            self.server.session.commit()
        res =  self.session.query(TableInherits).all()
        self.assertEqual(res, [])

    def testRemoteCombinedUpdates(self):
        "Confirm updates with non-overlapping attributes coalesce"
        t = TableInherits(info = "blah")
        self.session.add(t)
        self.session.manager = self.manager
        with wait_for_call(self.loop, sql.internal.sql_meta_messages, 'handle_you_have'):
            self.session.commit()
        t2 = self.server.session.query(TableInherits).filter_by(
            id = t.id).one()
        t2.info = "bazfutz"
        self.server.session.manager = self.server
        with wait_for_call(self.loop, sql.internal.sql_meta_messages, 'handle_you_have'):
            self.server.session.commit()
            t2.info2 = "quux"
            self.server.session.commit()
            # refetches because of expire after commit
        self.assertEqual(t.id, t2.id)


#import logging
#logging.basicConfig(level = 'ERROR')

if __name__ == '__main__':
    import logging, unittest, unittest.main
    logging.basicConfig(level = 'ERROR')
#    logging.basicConfig(level = 10)
    unittest.main(module = "tests.sql")
