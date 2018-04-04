# Copyright (C) 2018, Hadron Industries, Inc.
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
from sqlalchemy import Column, Integer, ForeignKey, inspect
from entanglement.sql import sql_sync_declarative_base, SyncOwner
from entanglement.util import GUID
from entanglement import SyncRegistry, Synchronizable, sync_property, operations
from .utils import *

Base = sql_sync_declarative_base()

class T1(Base):
    __tablename__ = 't1'
    id = Column(Integer, primary_key = True)
    x2 = Column(GUID, unique = True)

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
def test_foreign_key_sync(layout, order_fn, monkeypatch):
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
        t3 = csession.query(T3).get(id)
        if order_fn == order_100:
            if t3 is None:
                found_missing = True
                continue
            else: assert t3 is not None
        assert t3.t2.t1 is not t1
        assert t3.t2.t1 == t1
    if order_fn is order_100: assert found_missing



@pytest.mark.xfail(reason = "priorities over wire not implemented yet")
def test_priority_over_wire(layout, monkeypatch):
    "Confirm that non-standard priorities are sent over the wire"
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
    
    
logging.getLogger('entanglement.protocol').setLevel(10)
