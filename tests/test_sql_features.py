# Copyright (C) 2018, 2020, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

# The test_sql and test_gateway modules test the overall system,
# particularly focused on things where specifics of the environment
# are likely to affect test results.  As a consequence, the entire
# environment is deleted and recreated on every test for those
# modules.  In contrast, test_sql_features creates one environment for
# the entire module.
import logging, pytest, uuid

from sqlalchemy.orm import relationship, Session
from sqlalchemy import Column, Float, Integer, ForeignKey, inspect
from entanglement.sql import sql_sync_declarative_base, SyncOwner
from entanglement.util import GUID
from copy import deepcopy
from entanglement import SyncRegistry, Synchronizable, sync_property, operations
from .utils import *
from .conftest import layout_fn

Base = sql_sync_declarative_base()

class T1(Base):
    __tablename__ = 't1'
    id = Column(Integer, primary_key = True)
    x2 = Column(GUID, unique = True)
    f1 = Column(Float)

    # we override sync_receive_constructed for test_float_set_as_string
    def sync_receive_constructed(self, msg, **kwargs):
        if 'f1' in msg:
            assert isinstance(msg['f1'], (float, type(None)))
        return super().sync_receive_constructed(msg, **kwargs)
    
    def __eq__(self, other):
        if type(other) is not type(self): return False
        return (other.id == self.id) and (other.x2 == self.x2)

    def __hash__(self):
        h = 0
        if self.id: h+= hash(self.id)
        if self.x2: h += hash(self.x2)
        return h
    

class T2(Base):
    __tablename__ = 't2'
    id = Column(Integer, primary_key = True)
    t1_x2 = Column(GUID, ForeignKey(T1.x2))
    t1 = relationship(T1)

class T3(Base):
    __tablename__ = 't3'
    id = Column(Integer, primary_key = True)
    t2_id = Column(Integer, ForeignKey(T2.id))
    x = Column(Integer)
    t2 = relationship(T2, lazy = 'joined')

order_registry = SyncRegistry()
order_registry.register_operation('sync', operations.sync_operation)


class SyncOrder(Synchronizable):

    sync_registry = order_registry

    id = sync_property()
    sync_primary_keys = ('id',)

    @classmethod
    def sync_construct(cls, msg, **info):
        obj = super().sync_construct(msg, **info)
        obj.sync_owner = FloodableSyncOwner()
        obj.sync_owner.destination = info['sender']
        return obj
        

class Sync1(SyncOrder): pass

class Sync2(SyncOrder): pass

    

@pytest.fixture(scope = 'module')
def registries():
    return [Base, order_registry]

@pytest.fixture(scope = 'module')
def requested_layout(requested_layout):
    requested_layout['client2'] = {
        'connections': ['server']}
    return requested_layout

@pytest.fixture(scope = 'module')
def layout(layout_module):
    return layout_module

def order_100(monkeypatch):
    for o in (T1, T2, T3):
        monkeypatch.setattr(o, 'sync_priority', 100)

def order_explicit(monkeypatch):
    order = 100
    for o  in (T1, T2, T3):
        monkeypatch.setattr(o, 'sync_priority', order)
        order += 1

def order_natural(monkeypatch):
    #Let entanglement choose the order
    pass

@pytest.mark.parametrize('order_fn', [
    order_100,
    order_explicit,
    order_natural])
def test_foreign_key_sync(layout_module, order_fn, monkeypatch):
    layout = layout_module
    order_fn(monkeypatch)
    for le in layout.layout_entries:
        le.engine.execute('pragma foreign_keys = on')
        layout.disconnect_all()
    session = Base.registry.sessionmaker(bind = layout.server.engine)
    t1 = T1()
    t1.x2 = uuid.uuid4()
    t3_ids = []
    session.add(t1)
    for i in range(3):
        t2 = T2(t1 = t1)
        session.add(t2)
        for j in range(4):
            t3 = T3(t2 = t2)
            session.add(t3)
            session.flush()
            assert t3.id is not None
            t3_ids.append(t3.id)
    session.commit()
    layout.connect_all()
    csession = layout.client.session
    found_missing = False
    for id in t3_ids:
        t3 = csession.get(T3, id)
        if order_fn == order_100:
            if t3 is None:
                found_missing = True
                continue
            else: assert t3 is not None
        assert t3.t2.t1 is not t1
        assert t3.t2.t1 == t1
    if order_fn is order_100: assert found_missing



@pytest.mark.xfail(reason = "priorities over wire not implemented yet")
def test_priority_over_wire(layout_module, monkeypatch):
    "Confirm that non-standard priorities are sent over the wire"
    layout = layout_module
    def should_listen_wrap(obj, msg, **info):
        if isinstance(obj, SyncOrder) and not future.done():
            future.set_result(msg['id'])
        return orig_should_listen(obj, msg, **info)
    orig_should_listen = layout.client2.manager.should_listen_constructed
    monkeypatch.setattr(layout.client2.manager, 'should_listen_constructed', should_listen_wrap)
    future = layout.loop.create_future()
    o1 = Sync1()
    o1.id = 20
    o2 = Sync2()
    o2.id = 40
# Make the class lower priority and the object higher priority If
# priorities are sent over the wire, then the object should win and
# the o2 should be sent first.  If priorities are not sent over the
# wire, then o2 should be sent after o1
    o2.sync_priority = 90
    Sync2.sync_priority = 101
    layout.client.manager.synchronize(o1)
    layout.client.manager.synchronize(o2)
    assert len(layout.server.manager.connections) == 2
    settle_loop(layout.loop)
    assert future.result() == o2.id


def test_delete_unknown_sync_owner(layout_module):
    layout = layout_module
    from entanglement.sql.internal import sql_meta_messages
    so = SyncOwner()
    so.id = uuid.uuid4()
    o1 = T1()
    o1.id = 39
    o1.sync_owner = so
    layout.client.session.add(o1)
    layout.client.session.commit()
    settle_loop(layout.loop)
    so.sync_serial = 20
    with wait_for_call(layout.loop, sql_meta_messages, 'incoming_delete', trap_exceptions = True):
        layout.client.manager.synchronize(so, operation ='delete')

def test_float_set_as_string(layout_module):
    layout = layout_module
    session = layout.client.session
    t1 = T1()
    t1.f1 = "30.9"
    session.add(t1)
    session.commit()
    settle_loop(layout.loop)
    t1_server = layout.server.session.query(T1).filter_by(id = t1.id).first()
    assert isinstance(t1_server.f1, float)

def test_no_force_resync_always(layout, monkeypatch):
    "Confirm that we do not do forced resyncs when unnecessary)"
    assert_tripped = False
    def raise_assert(*args, **kwargs):
        nonlocal assert_tripped
        assert_tripped = True
        raise AssertionError("Should not be called")
    t1 = T1()
    t1.x2 = uuid.uuid4()
    layout.disconnect_all()
    monkeypatch.setattr(SyncOwner, "clear_all_objects", raise_assert)
    settle_loop(layout.loop)
    layout.server.session.add(t1)
    assert layout.server.session.manager is layout.server.manager
    layout.server.session.commit()
    layout.connect_all()
    settle_loop(layout.loop)
    assert not assert_tripped, "Unexpected forced resync"
    # But let's also make sure the new object got synchronized.
    t1_client = layout.client.session.query(T1).filter_by(id = t1.id).one()
    assert t1_client == t1

    
    
logging.getLogger('entanglement.protocol').setLevel(10)
logging.basicConfig(level = 10)
