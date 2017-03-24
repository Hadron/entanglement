#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import datetime, sqlalchemy

from sqlalchemy import Column, Table, String, Integer, DateTime, ForeignKey, inspect
import sqlalchemy.exc
import sqlalchemy.orm, sqlalchemy.ext.declarative, sqlalchemy.ext.declarative.api
from ..util import CertHash, SqlCertHash, get_or_create
from .. import interface, network

from . import internal as _internal

class SqlSyncSession(sqlalchemy.orm.Session):

    def __init__(self, *args, manager = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.manager = manager
        self.sync_dirty = set()
        @sqlalchemy.events.event.listens_for(self, "before_flush")
        def before_flush(session, internal, instances):
            serial_insert = Serial.__table__.insert().values(timestamp = datetime.datetime.now())
            serial_flushed = False
            for inst in session.new | session.dirty:
                if isinstance(inst, SqlSynchronizable):
                    self.sync_dirty.add(inst)
                    if not serial_flushed:
                        new_serial = session.execute(serial_insert).lastrowid
                        serial_flushed = True
                    if inst.sync_owner_id is None: inst.sync_serial = new_serial
        @sqlalchemy.events.event.listens_for(self, "after_commit")
        def receive_after_commit(session):
            if self.manager:
                self.manager.synchronize(list(map( lambda x: self.manager.session.merge(x), self.sync_dirty)))
            self.sync_dirty.clear()
        @sqlalchemy.events.event.listens_for(self, 'after_rollback')
        def after_rollback(session):
            session.sync_dirty.clear()
            
                

def sync_session_maker(*args, **kwargs):
    return sqlalchemy.orm.sessionmaker(class_ = SqlSyncSession, *args, **kwargs)

class SqlSyncMeta(interface.SynchronizableMeta, sqlalchemy.ext.declarative.api.DeclarativeMeta):

    def __new__(cls, name, bases, ns):
        registry = None
        for c in bases:
            registry = getattr(c,'registry', None)
            if registry is not None: break
        if not 'sync_registry' in ns: ns['sync_registry'] = registry
        for k,v in ns.items():
            if isinstance(v, (Column, )):
                ns[k] = interface.sync_property(wraps = v)
        return interface.SynchronizableMeta.__new__(cls, name, bases, ns)

    def __init__(cls, name, bases, ns):
        super().__init__(name, bases, ns)
        if not hasattr(cls,'registry'):
            setattr(cls,'registry', SqlSyncRegistry())
        if isinstance(cls.sync_primary_keys, property):
            try:
                cls.sync_primary_keys = tuple(map(
                        lambda x: x.name, inspect(cls).primary_key))
            except sqlalchemy.exc.NoInspectionAvailable: pass


class SqlSyncRegistry(interface.SyncRegistry):

    def __init__(self, *args,
                 sessionmaker = sync_session_maker(),
                 bind = None,
                 **kwargs):
        if bind is not None: sessionmaker.configure(bind = bind)
        self.sessionmaker = sessionmaker
        super().__init__(*args, **kwargs)

    def create_bookkeeping(self, bind):
        _internal_base.metadata.create_all(bind = bind)
    def ensure_session(self, manager):
        if not hasattr(manager, 'session'):
            manager.session = self.sessionmaker()
            if not manager.session.is_active: manager.session.rollback()

    def associate_with_manager(self, manager):
        self.ensure_session(manager)
        

    def sync_receive(self, object, manager, **info):
        # By this point the owner check has already been done
        assert object.sync_owner is not None
        assert object in manager.session
        manager.session.commit()
        

    
_internal_base = sqlalchemy.ext.declarative.declarative_base()

class Serial(_internal_base):
    "Keep track of serial numbers.  A sequence would be better, but sqlite can't do them"

    __tablename__ = 'sync_serial'
    serial = Column(Integer, primary_key = True)
    timestamp = Column(DateTime, default = datetime.datetime.now)

class  SqlSyncDestination(_internal_base, network.SyncDestination):
    __tablename__ = "sync_destinations"
    id = Column(Integer, primary_key = True)
    cert_hash = Column(SqlCertHash, unique = True, nullable = False)
    name = Column(String(64), nullable = False)
    host = Column(String(128))

    incoming_serial = Column(Integer, default = 0, nullable = False)
    incoming_epoch = Column(sqlalchemy.types.DateTime,
                          default = datetime.datetime.utcnow(), nullable = False)
    outgoing_epoch = Column(sqlalchemy.types.DateTime,
                          default = datetime.datetime.utcnow(), nullable = False)

    def __init__(self, *args, **kwargs):
        network.SyncDestination.__init__(self, *args, **kwargs)
    def clear_all_objects(self, manager = None,
                          *, registries = None, session = None):
        if manager:
            session = manager.session
            registries = manager.registries
        assert session and registries
        subquery = session.Query(base.SyncOwner).filter(base.SyncOwner.destination == self)
        for reg in registries:
            for c in reg.registry:
                if isinstance(c, SqlSynchronizable):
                    session.query(c).filter(c.sync_owner.in_(subquery)).delete(False)

    async def connected(self, manager, *args, **kwargs):
        res = await super().connected(manager, *args, **kwargs)
        if not self in manager.session: manager.session.add(self)
        manager.session.flush()
        i_have = _internal.IHave()
        i_have.serial = self.incoming_serial
        i_have.epoch = self.incoming_epoch
        self.protocol.synchronize_object(i_have)
        return res


    

class SyncOwner(_internal_base):
    __tablename__ = "sync_owners"
    id = Column(Integer, primary_key = True)
    destination_id = Column(Integer, ForeignKey(SqlSyncDestination.id),
                            index = True, nullable = False)
    destination = sqlalchemy.orm.relationship(SqlSyncDestination)

    @classmethod
    def find_or_create(self, session, dest, msg):
        return get_or_create(session, SyncOwner,
                             {'destination':
                              get_or_create(session, SqlSyncDestination,
                                            {'cert_hash': dest.cert_hash},
                                            {'name': dest.name,
                                             'host': dest.host})})
    


class SqlSynchronizable(interface.Synchronizable):

    sync_serial =interface.sync_property(wraps = Column(Integer, nullable=False, index = True))

    @sqlalchemy.ext.declarative.api.declared_attr
    def sync_owner_id(self):
        return Column(Integer, ForeignKey(SyncOwner.id))

    @sqlalchemy.ext.declarative.api.declared_attr
    def sync_owner(self):
        return sqlalchemy.orm.relationship(SyncOwner)

        
    @property
    def sync_is_local(self):
        return self.sync_owner is None

    @classmethod
    def _sync_construct(cls, msg, manager = None, registry = None, **info):
        if manager and registry: registry.ensure_session(manager)
        obj = None
        if hasattr(manager,'session'):
            primary_keys = map(lambda x:x.name, inspect(cls).primary_key)
            try: primary_key_values = tuple(map(lambda k: msg[k], primary_keys))
            except KeyError as e:
                raise interface.SyncBadEncodingError("All primary keys must be present in the encoding", msg = msg) from e
            obj = manager.session.query(cls).get(primary_key_values)
            owner = SyncOwner.find_or_create(manager.session, info['sender'], msg)
        if obj is not None and obj.sync_owner_id != owner.id:
            raise interface.SyncBadOwnerError("Object owned by {}, but sent by {}".format(
                obj.sync_owner.destination, owner.destination))
        if obj is not None:
            for k in primary_keys: del msg[k]
            return obj
        obj =  super()._sync_construct(msg,**info)
        obj.sync_owner = owner
        if hasattr(manager, 'session'):
            manager.session.add(obj)
        return obj
    
        
def sql_sync_declarative_base(*args, **kwargs):
    return sqlalchemy.ext.declarative.declarative_base(cls = SqlSynchronizable, metaclass = SqlSyncMeta, *args, **kwargs)
