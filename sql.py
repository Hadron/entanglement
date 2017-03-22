#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import datetime

from sqlalchemy import Column, Table, String, Integer, DateTime, ForeignKey
import sqlalchemy.orm, sqlalchemy.ext.declarative, sqlalchemy.ext.declarative.api
from util import CertHash, SqlCertHash
from interface import SynchronizableMeta

class SyncSqlSession(sqlalchemy.orm.Session):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        @sqlalchemy.events.event.listens_for(self, "before_flush")
        def before_flush(session, internal, instances):
            serial_insert = Serial.__table__.insert().values(timestamp = datetime.datetime.now())
            serial_flushed = False
            for inst in session.new | session.dirty:
                if isinstance(inst, SqlSynchronizable):
                    if not serial_flushed:
                        new_serial = session.execute(serial_insert).lastrowid
                        serial_flushed = True
                    inst.sync_serial = new_serial

def sync_session_maker(*args, **kwargs):
    return sqlalchemy.orm.sessionmaker(class_ = SyncSqlSession, *args, **kwargs)

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
    


class SqlSynchronizable:
    sync_serial =Column(Integer, nullable=False, index = True)

    @sqlalchemy.ext.declarative.api.declared_attr
    def sync_owner(self):
        return Column(Integer, ForeignKey(SyncOwner.id))

        @sqlalchemy.ext.declarative.api.declared_attr
        def sync_serial_rel(self):
            return sqlalchemy.orm.relationship(Serial)
        
    @property
    def sync_is_local(self):
        return self.sync_owner is None
    
