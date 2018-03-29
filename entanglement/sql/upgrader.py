# Copyright (C) 2018, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from alembic import command, config
from sqlalchemy import MetaData, Table
from sqlalchemy.exc import NoSuchTableError

def upgrade_database(engine, metadata,
                     script_location,
                     version_table,
                     user_table = None,
                     user_table_version = None):
    '''
Upgrade the database to the latest version.  If version_table is present in the engine, then an alembic upgrade to head is performed.  If version_table is not present and user_table is None or not present in the engine, then a create_all is run on the metadata and the database is stamped with head.  If user_table is specified, it should be a table present when alembic support is added.  User_table_version should be the version tthat is assumed if this table is present but version_table is not.  The database will be stamped with user_table_version and upgraded to head.
'''
    conf = config.Config()
    conf.set_main_option('script_location', script_location)
    conf.set_main_option('sqlalchemy.url', str(engine.url))
    reflection_meta = MetaData(bind = engine) # Used to check for table presence
    try:
        Table(version_table, reflection_meta, autoload = True)
        #If no exception, version_table exists.
    except NoSuchTableError:
        if user_table:
            try:
                Table(user_table, reflection_meta, autoload = True)
                #user table is present.
                command.stamp(conf, user_table_version)
            except NoSuchTableError:
                #The user table is also not present.
                metadata.create_all(engine)
                command.stamp(conf, 'head')
    command.upgrade(conf, 'head')
    
