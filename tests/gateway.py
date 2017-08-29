# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, copy, datetime, gc, json, logging, ssl, unittest, uuid, warnings
from contextlib import contextmanager
from unittest import mock

from hadron.entanglement.interface import Synchronizable, sync_property, SyncRegistry, SyncUnauthorized, SyncError
from hadron.entanglement.network import  SyncServer,  SyncManager
from hadron.entanglement.util import certhash_from_file, CertHash, SqlCertHash, get_or_create, entanglement_logs_disabled, GUID
from sqlalchemy import create_engine, Column, Integer, inspect, String, ForeignKey
from sqlalchemy.orm import sessionmaker
from hadron.entanglement.sql import SqlSynchronizable,  sync_session_maker, sql_sync_declarative_base, SqlSyncDestination, SqlSyncRegistry, sync_manager_destinations, SyncOwner, SqlSyncError
import hadron.entanglement.sql as sql
from .utils import *
import hadron.entanglement.protocol, hadron.entanglement.operations
from hadron.entanglement.sql.transition import SqlTransitionTrackerMixin
from hadron.entanglement.transition import BrokenTransition

# SQL declaration
Base = sql_sync_declarative_base()

class NoResponseRegistry(SyncRegistry):

    def __init__(self):
        super().__init__()
        self.register_operation('sync',self.incoming)

    def incoming(self, obj, manager, sender, response_for, **info):
#We want to be able to trigger a non-responseful synchronization so we
#can confirm no_resp will piggyback on other messages.  So, if we
#receive an object with flood as its string, we generate a new object
#that floods but that is not a response
        if obj.str == "flood":
            manager.synchronize(NoResponseHelper("grumble"))
        manager.synchronize(obj,
                            response_for = response_for,
                            exclude = [sender])
no_response_registry = NoResponseRegistry()

class NoResponseHelper(Synchronizable):

    sync_registry = no_response_registry

    str = sync_property()

    sync_primary_keys = ('str'),

    def __init__(self, str = ""):
        self.str = str
    


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

class TableError(Base):
    __tablename__ = 'error'
    id = Column(String, primary_key = True)
    other = Column(String, nullable = False)
    
manager_registry = SqlSyncRegistry()
manager_registry.registry = Base.registry.registry

client_registry = SqlSyncRegistry()
client_registry.registry = Base.registry.registry

class TableTransition(Base, SqlTransitionTrackerMixin):
    __tablename__ = 'transition'
    id = Column(Integer, primary_key = True)
    x = Column(Integer, nullable = False)
    y = Column(Integer)
    
class TestGateway(SqlFixture, unittest.TestCase):

    def __init__(self, *args, **kwargs):
        self.base = Base
        self.manager_registry = manager_registry
        self.other_registries = [no_response_registry]
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
                                 registries = [client_registry] + self.other_registries,
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


    def setup_one_obj_layout(self):
        count = 0
        managers = ['client', 'server', 'manager']
        l = [{'name': m} for m in managers]
        for i in l:
            i['manager'] = getattr(self, i['name'])
            session = i['session'] = Base.registry.sessionmaker(bind = i['manager'].session.bind)
            count += 1
            i['obj'] = TableInherits(info = "object {}".format(count))
            session.add(i['obj'])
            session.manager = i['manager']
            session.commit()
            session.refresh(i['obj'])
            i['owner'] = session.query(SyncOwner).filter_by(destination_id = None).one()
        settle_loop(self.loop)
        settle_loop(self.loop) # you_have has a 0 second delay
        return l

    def testFullFloodingAndBookkeeping(self):
        "Test that all nodes can flood and that object states synchronize"
        def msg(m):
            return "Error between {} and {}: {}".format(
                a['name'],b['name'], m)
        l = self.setup_one_obj_layout()
        for a in l:
            for b in l:
                if a is b: continue
                session_b = b['session']
                obj_b = session_b.query(TableInherits).get(a['obj'].id)
                self.assertIsNotNone(obj_b, msg('Failed to propagate object'))
                self.assertEqual(a['owner'].id, obj_b.sync_owner.id,
                                 msg("sync owner id"))
                self.assertEqual(obj_b.sync_serial, a['obj'].sync_serial,
                                 msg("Sync serial not correct"))
                self.assertEqual(obj_b.sync_owner.incoming_serial, a['obj'].sync_serial,
                                 msg('owner incoming serial'))

    def testDeleteConnected(self):
        "Test that object deletion works when nodes are connected"
        def msg(s):
            return "Examining {a} object viewed at {b}: {s}".format(
                a = a['name'],
                b = b['name'],
                s = s)
        l = self.setup_one_obj_layout()
        for e in l:
            e['session'].delete(e['obj'])
            e['session'].commit()
        settle_loop(self.loop)
        for a in l:
            for b in l:
                session_b = b['session']
                self.assertIsNone(
                    session_b.query(TableInherits).get(a['obj'].id),
                    msg("Object not deleted"))

    def testDeleteDisconnected(self):
        "Test that object deletion works when nodes are disconnected"
        def msg(s):
            return "Examining {a} object viewed at {b}: {s}".format(
                a = a['name'],
                b = b['name'],
                s = s)
        l = self.setup_one_obj_layout()
        with entanglement_logs_disabled():
            self.client.remove_destination(self.client_to_server)
            self.manager.remove_destination(self.to_server)
            settle_loop(self.loop)
        for e in l:
            e['session'].delete(e['obj'])
            e['session'].commit()
        settle_loop(self.loop)
        self.client_to_server.connect_at = 0
        self.client.add_destination(self.client_to_server)
        self.loop.run_until_complete(asyncio.wait(self.client._connecting.values()))
        settle_loop(self.loop)
        self.to_server.connect_at = 0
        self.manager.add_destination(self.to_server)
        self.loop.run_until_complete(asyncio.wait(self.manager._connecting.values()))
        settle_loop(self.loop) # and again for you_have
        settle_loop(self.loop)
        for a in l:
            for b in l:
                session_b = b['session']
                self.assertIsNone(
                    session_b.query(TableInherits).get(a['obj'].id),
                    msg("Object not deleted"))

    def testForwardUpdate(self):
        "Test that forward operation works across middle nodes"
        t = TableInherits(info2 = "blah")
        self.client_session.add(t)
        self.client_session.commit()
        settle_loop(self.loop)
        manager_session = manager_registry.sessionmaker()
        manager_session.manager = self.manager
        t2 = manager_session.query(TableInherits).get(t.id)
        self.assertEqual(t2.id, t.id)
        self.assertEqual(t2.info2, t.info2)
        t2.info2 = "Force a forward update"
        manager_session.sync_commit()
        manager_session.commit()
        self.client_session.expire(t)
        settle_loop(self.loop)
        self.assertEqual(t.info2, t2.info2)

    def testForwardResponse(self):
        "Test that forwards response futures work"
        t = TableInherits(info2 = "blah")
        self.client_session.add(t)
        self.client_session.commit()
        settle_loop(self.loop)
        manager_session = manager_registry.sessionmaker()
        manager_session.manager = self.manager
        t2 = manager_session.query(TableInherits).get(t.id)
        self.assertEqual(t2.id, t.id)
        self.assertEqual(t2.info2, t.info2)
        t2.info2 = "Force a forward update"
        manager_session.sync_commit()
        manager_session.commit()
        assert hasattr(t2,'sync_future')
        c = next(iter(self.manager.connections))
        found = False
        for v in c.dirty.values():
            if v.obj.sync_compatible(t2):
                found = True
                self.assertIsNotNone(v.response_for)
                self.assertIn(t2.sync_future, v.response_for.futures)
        assert found
        self.loop.run_until_complete(asyncio.wait([t2.sync_future], timeout = 0.5))
        self.assertEqual(t2.sync_future.result().info2, t2.info2)
        settle_loop(self.loop)
        
    def testDeleteResponse(self):
        "Test that delete response futures work"
        t = TableInherits(info2 = "blah")
        self.client_session.add(t)
        self.client_session.commit()
        settle_loop(self.loop)
        manager_session = manager_registry.sessionmaker()
        manager_session.manager = self.manager
        t2 = manager_session.query(TableInherits).get(t.id)
        self.assertEqual(t2.id, t.id)
        self.assertEqual(t2.info2, t.info2)
        manager_session.delete(t2)
        manager_session.sync_commit()
        manager_session.commit()
        assert hasattr(t2,'sync_future')
        c = next(iter(self.manager.connections))
        found = False
        for v in c.dirty.values():
            if v.obj.sync_compatible(t2):
                found = True
                self.assertIsNotNone(v.response_for)
                self.assertIn(t2.sync_future, v.response_for.futures)
        assert found
        self.loop.run_until_complete(asyncio.wait([t2.sync_future], timeout = 0.5))
        self.assertEqual(t2.sync_future.result().id, t2.id)
        self.assertEqual(
            self.client_session.query(TableInherits).filter_by(id = t.id).all(),
            [])
        settle_loop(self.loop)

    def testNoResponse(self):
        "Test the full no response logic.  This test confirms that no responses can be piggybacked; entanglement.py:TestSynchronization.testNoResponseMetaOnly tests the other path."
        handle_meta = hadron.entanglement.protocol.SyncProtocol._handle_meta
        mock_called = False
        def mock_handle_meta(protocol,sync_repr, flags):
            nonlocal mock_called
            mock_called = True
            self.assertIn('_sync_type', sync_repr)
            return handle_meta(protocol, sync_repr, flags)
        with mock.patch.object(hadron.entanglement.protocol.SyncProtocol,
                               '_handle_meta', new = mock_handle_meta):
            nrh = NoResponseHelper("flood")
            fut = self.client.synchronize(nrh, response = True)
            self.assertIsInstance(fut, asyncio.Future)
            settle_loop(self.loop)
            gc.collect()
            settle_loop(self.loop)
            self.loop.run_until_complete(asyncio.wait([fut], timeout = 0.5))
            self.assertTrue(mock_called)
            self.assertTrue(fut.done())
            self.assertIsNone(fut.result())

    def testError(self):
        "Test that errors flood to response"
        t = TableError(id = "123", other = "456")
        self.client_session.add(t)
        self.client_session.commit()
        settle_loop(self.loop)
        manager_session = manager_registry.sessionmaker()
        manager_session.manager = self.manager
        t2 = manager_session.query(TableError).all()[0]
        t2.other = None
        manager_session.sync_commit()
        fut = t2.sync_future
        with entanglement_logs_disabled(): 
            self.loop.run_until_complete(asyncio.wait([fut], timeout = 0.6))
        self.assertRaises(SqlSyncError, fut.result)

    def  testTransition(self):
        " Test TransitionTrackerMixin"
        for r in (Base.registry, manager_registry, client_registry):
            r.register_operation('transition', hadron.entanglement.operations.transition_operation)
        t = TableTransition(x = 10, id = 20, y = -30)
        self.client_session.add(t)
        self.client_session.commit()
        settle_loop(self.loop)
        t.x = 99
        with transitions_tracked_as(self.client):
            t.perform_transition(self.client)
            #logging.getLogger('hadron.entanglement.protocol').setLevel(10)
            self.assertIsNone(inspect(t).session)
        with transitions_partitioned():
            settle_loop(self.loop)
            with transitions_tracked_as(self.manager):
                t2 = TableTransition.get_from_transition(t.id)
                self.assertIsInstance(t2, TableTransition)
                self.assertIsNone(inspect(t2).session)
            manager_session = manager_registry.sessionmaker()
            manager_session.manager = self.manager
            with transitions_tracked_as(self.manager):
                t2.remove_from_transition()
            manager_session.add(t2)
            manager_session.sync_commit()
            settle_loop(self.loop)
            t2 = t2.sync_future.result()
            t =self.client_session.merge(t)
            self.client_session.refresh(t)
            with transitions_tracked_as(self.manager):
                self.assertIsNone(TableTransition.get_from_transition(t.transition_key()))
            self.assertEqual(t.sync_serial,t2.sync_serial)
            # Now make sure that if we transition one column and then
            # commit a change to another column the first change is
            # not folded in.  That is make sure that parties discard
            # state when an object exits transition
            t2.y = -20
            with transitions_tracked_as(self.manager):
                t2.perform_transition(self.manager)
            settle_loop(self.loop)
            with transitions_tracked_as(self.client):
                t_client = TableTransition.get_from_transition(t2.transition_key())
            self.assertIsInstance(t_client, TableTransition)
            self.assertEqual(t2.y, t_client.y)
            with transitions_tracked_as(self.manager):
                t2.remove_from_transition()
            t2 = manager_session.merge(t2)
            manager_session.refresh(t2)
            self.assertEqual(t2.y, -30)
            t2.x = 8192
            manager_session.sync_commit()
            settle_loop(self.loop)
            self.client_session.expire(t)
            self.assertEqual(t.x, 8192)
            self.assertEqual(t.y, -30, msg = "Transition updates were incorrectly folded into other changes")

    def testTransitionResponses(self):
        "Test transition updates and responses"
        for r in (Base.registry, manager_registry, client_registry):
            r.register_operation('transition', hadron.entanglement.operations.transition_operation)
        server_session = Base.registry.sessionmaker()
        server_session.manager = self.server
        manager_session = manager_registry.sessionmaker()
        manager_session.manager = self.manager
        t = TableTransition(id = 30)
        owner = SyncOwner(destination_id = None)
        server_session.add(owner)
        server_session.commit()
        settle_loop(self.loop)
        t.sync_owner = owner
        t.x = 9218
        server_session.add(t)
        server_session.commit()
        settle_loop(self.loop)
        t.x = 30
        with transitions_partitioned():
            with transitions_tracked_as(self.server): fut = t.perform_transition(self.server)
            self.assertIsNotNone(t.transition_id)
            t.y = 20
            with transitions_tracked_as(self.server): t.perform_transition(self.server)
            self.assertIsNotNone(t.transition_id)
            settle_loop(self.loop)
            with transitions_tracked_as(self.client):
                t_client = TableTransition.get_from_transition(t.id)
            self.assertIsInstance(t_client, TableTransition)
            self.assertEqual(t.to_sync(), t_client.to_sync())
            t_client = self.client_session.merge(t)
            self.client_session.refresh(t_client)
            t_client.x = -2095
            # Break the transition with an update
            self.client_session.sync_commit()
            self.loop.run_until_complete(asyncio.wait([t_client.sync_future], timeout = 0.6))
            self.assertIsInstance(fut.exception(), BrokenTransition)
            self.assertIsInstance(t_client.sync_future.result(), TableTransition)
            t_client = self.client_session.merge(t_client.sync_future.result())
            t.x = 9219
            with transitions_tracked_as(self.client):
                fut = t_client.perform_transition(self.client)
            settle_loop(self.loop)
            self.assertFalse(fut.done())
            t = server_session.merge(t)
            server_session.refresh(t)
            t.y = 919
            #break transition with transition
            with transitions_tracked_as(self.server):
                fut_server = t.perform_transition(self.server)
                #logging.getLogger('hadron.entanglement.protocol').setLevel(10)
            self.loop.run_until_complete(asyncio.wait(
                map(lambda c: c.sync_drain(), self.server.connections), timeout = 0.5))
            server_session.add(t)
            # end transition with sync
            self.assertIsNotNone(t.transition_id)
            server_session.commit()
            settle_loop(self.loop)
            gc.collect()
            settle_loop(self.loop)
            self.assertIsInstance(fut.exception(), BrokenTransition)
            self.assertIsNone(fut_server.result())
                                             
                
                                                 
            

            
    def testCreate(self):
        "Confirm that the create operation works"
        t = TableInherits(info2 = "blah baz")
        manager_session = manager_registry.sessionmaker()
        manager_session.manager = self.manager
        manager_owner = manager_session.query(SyncOwner).filter_by(destination = None).one()
        t.sync_owner = self.client_session.query(SyncOwner).get(manager_owner.id)
        #logging.getLogger('hadron.entanglement.protocol').setLevel(10)
        fut = t.sync_create(self.client, t.sync_owner)
        self.loop.run_until_complete(asyncio.wait([fut], timeout = 0.6))
        self.assertTrue(fut.done())
        t = self.client_session.merge(fut.result())
        t2 = manager_session.query(TableInherits).get(t.id)
        self.assertEqual(t.to_sync(), t2.to_sync())

    def testCreateError(self):
        "Confirm we get an error for create as a response"
        t = TableInherits(info2 = "blah baz")
        manager_session = manager_registry.sessionmaker()
        manager_session.manager = self.manager
        manager_owner = manager_session.query(SyncOwner).filter_by(destination = None).one()
        t.sync_owner = self.client_session.query(SyncOwner).get(manager_owner.id)
        t.id = t.sync_owner.id # Will cause an error
        #logging.getLogger('hadron.entanglement.protocol').setLevel(10)
        fut = t.sync_create(self.client, t.sync_owner)
        with entanglement_logs_disabled():
            self.loop.run_until_complete(asyncio.wait([fut], timeout = 0.6))
        self.assertTrue(fut.done())
        self.assertRaises(SyncUnauthorized, fut.result)

    def testNoCreateForward(self):
        "Confirm that forward cannot be used to create a remote object"
        sess = self.client_session
        owners = sess.query(SyncOwner).filter(SyncOwner.destination != None).all()
        assert len(owners) >= 1
        owner = owners[0]
        obj = TableInherits()
        obj.sync_owner_id = owner.id
        sess.add(obj)
        sess.commit()
        with entanglement_logs_disabled():
            self.loop.run_until_complete(asyncio.wait([obj.sync_future], timeout = 0.5))
        with self.assertRaises(SyncError):
            obj.sync_future.result()


if __name__ == '__main__':
    import logging, unittest, unittest.main
#    logging.basicConfig(level = 'ERROR')
    logging.basicConfig(level = 10)
    hadron.entanglement.protocol.protocol_logger.setLevel(10)
    unittest.main(module = "tests.gateway")
