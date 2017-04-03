#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


from sqlalchemy import DDL, MetaData, inspect
from sqlalchemy.ext import automap
from .. import sql, interface

__all__ = ['sync_map_base',
           'reflect_and_prepare']

def _instrument_table_sqlite3(engine, tab):
    with engine.begin() as con:
        ctx = {'tab': tab}
        add_sync_owner = DDL('alter table %(tab)s add sync_owner_id integer references sync_owners(id)', context = ctx)
        add_sync_serial = DDL('alter table %(tab)s add sync_serial integer not null default 1', context = ctx)
        trigger = '''
            create trigger %(tab)s_%(op)s_serial after %(op)s on %(tab)s when new.sync_owner_id is null begin
            insert into sync_serial(timestamp) values(current_timestamp);
            update %(tab)s set sync_serial = (select max(serial) from sync_serial) where rowid = new.rowid;
            end;'''
        
        if 'sync_owner_id' not in tab.c:
            con.execute(add_sync_owner)
        if 'sync_serial' not in tab.c:
            con.execute(add_sync_serial)
            for op in ('update', 'insert'):
                con.execute(DDL(trigger, context = {
                    'tab': tab,
                    'op': op}))
                
    
def sync_map_base(*args, **kwargs):
    db = sql.sql_sync_declarative_base(*args, **kwargs)
    return automap.automap_base(db)

def reflect_and_prepare(base, bind = None):
    only_cb = lambda t, m: t not in ('sync_serial', 'sync_destinations', 'sync_owners')
    bind = bind or base.metadata.bind
    metadata = MetaData(bind = bind)
    base.registry.create_bookkeeping(bind)
    metadata.reflect(only = only_cb)
    for t in metadata.tables.values():
        _instrument_table_sqlite3(bind, t)
    base.metadata.reflect(bind = bind, only = only_cb)
    base.prepare(reflect = False)
    del base.registry.registry['Base']
    if 'sync_destinations' in base.registry.registry:
        del base.registry.registry['sync_destinations']
        del base.registry.registry['sync_owners']
    base.registry.sessionmaker.configure(bind = bind)
    for c in base.classes:
        for col in c.__table__.columns:
            prop = sql.internal.process_column(col.name,col, wraps = False)
            if not prop.encoderfn:
                prop.encoderfn = interface.default_encoder(col.name)
            if not prop.decoderfn:
                prop.decoderfn = lambda obj, val: val
            prop.declaring_class = c.__name__
            c._sync_meta[col.name] = prop
            c.sync_primary_keys = tuple(map(
                lambda x: x.name, inspect(c).primary_key))

