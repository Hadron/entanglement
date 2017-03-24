#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import datetime

from sqlalchemy import Column, Table, String, Integer, DateTime, ForeignKey, inspect
import sqlalchemy.exc
import sqlalchemy.orm, sqlalchemy.ext.declarative, sqlalchemy.ext.declarative.api
from util import CertHash, SqlCertHash, get_or_create
import interface

class SyncSqlSession(sqlalchemy.orm.Session):

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
                self.manager.synchronize(self.sync_dirty)
            self.sync_dirty.clear()
        @sqlalchemy.events.event.listens_for(self, 'after_rollback')
        def after_rollback(session):
            session.sync_dirty.clear()
            
                

def sync_session_maker(*args, **kwargs):
    return sqlalchemy.orm.sessionmaker(class_ = SyncSqlSession, *args, **kwargs)

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
        d = {}
        if bind is not None: d['bind'] = bind
        super().__init__(*args, **kwargs)
        self.session = sessionmaker(**d)

    def sync_receive(self, object, **info):
        # By this point the owner check has already been done
        assert object.sync_owner is not None
        assert object in self.session
        self.session.commit()
        

    
_internal_base = sqlalchemy.ext.declarative.declarative_base()

class Serial(_internal_base):
    "Keep track of serial numbers.  A sequence would be better, but sqlite can't do them"

    __tablename__ = 'sync_serial'
    serial = Column(Integer, primary_key = True)
    timestamp = Column(DateTime, default = datetime.datetime.now)

class  SqlSyncDestination(_internal_base):
    __tablename__ = "sync_destinations"
    id = Column(Integer, primary_key = True)
    cert_hash = Column(SqlCertHash, unique = True, nullable = False)
    name = Column(String(64), nullable = False)
    host = Column(String(128))

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
    def _sync_construct(cls, msg, **info):
        obj = None
        if cls.sync_registry.session:
            if cls.sync_registry.session.is_active == False:
                cls.sync_registry.session.rollback()
            primary_keys = map(lambda x:x.name, inspect(cls).primary_key)
            try: primary_key_values = tuple(map(lambda k: msg[k], primary_keys))
            except KeyError as e:
                raise interface.SyncBadEncodingError("All primary keys must be present in the encoding") from e
            obj = cls.sync_registry.session.query(cls).get(primary_key_values)
            owner = SyncOwner.find_or_create(cls.sync_registry.session, info['sender'], msg)
        if obj is not None and obj.sync_owner_id != owner.id:
            raise interface.SyncBadOwnerError("Object owned by {}, but sent by {}".format(
                obj.sync_owner.destination, owner.destination))
        if obj is not None:
            for k in primary_keys: del msg[k]
            return obj
        obj =  super()._sync_construct(msg,**info)
        obj.sync_owner = owner
        cls.sync_registry.session.add(obj)
        return obj
    
        
def sql_sync_declarative_base(*args, **kwargs):
    return sqlalchemy.ext.declarative.declarative_base(cls = SqlSynchronizable, metaclass = SqlSyncMeta, *args, **kwargs)
