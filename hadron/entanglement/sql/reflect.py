#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


from sqlalchemy.ext import automap
from .. import sql, interface

def sync_map_base(*args, **kwargs):
    db = sql.sql_sync_declarative_base(*args, **kwargs)
    return automap.automap_base(db)

def reflect_and_prepare(base, bind = None):
    base.prepare(reflect = True)
    #del base.registry.registry['base']
    for c in base.classes:
        for col in c.__table__.columns:
            prop = sql.internal.process_column(col, wraps = False)
            if not prop.encoderfn:
                prop.encoderfn = interface.default_encoder(col.name)
            if not prop.decoderfn:
                prop.decoderfn = lambda obj, val: val
            prop.declaring_class = c.__name__
            c._sync_meta[col.name] = prop
            
