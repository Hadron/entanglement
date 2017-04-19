#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, datetime, iso8601

from ..interface import Synchronizable, SyncRegistry, SyncError, sync_property
from . import encoders
from .. import interface
from ..network import logger
from sqlalchemy import inspect
class _SqlMetaRegistry(SyncRegistry):

    # If true, then yield between each class while handling an IHave.  In general it is better to  let the event loop have an opportunity to run, but this makes testing much more complicated so it can be disabled.
    yield_between_classes = True
    
    def sync_receive(self, obj, sender, manager, **info):
        try:
            # remember to handle subclasses first
            if isinstance(obj,YouHave):
                self.handle_you_have(obj, sender, manager)
            elif isinstance(obj,IHave):
                sender.i_have_task = manager.loop.create_task(self.handle_i_have(obj, sender, manager))
            elif isinstance(obj, WrongEpoch):
                self.handle_wrong_epoch(obj, sender, manager)
            else: raise ValueError("Unexpected message")
        finally:
            manager.session.rollback()

    @asyncio.coroutine
    def handle_i_have(self, obj, sender, manager):
        from .base import SqlSynchronizable, SyncDeleted
        try:
            if sender.cert_hash not in manager._connections: return
            if sender.outgoing_epoch.tzinfo is None:
                outgoing_epoch = sender.outgoing_epoch.replace(tzinfo = datetime.timezone.utc)
            else: outgoing_epoch = sender.outgoing_epoch
            if outgoing_epoch != obj.epoch:
                logger.info("{} had wrong epoch; asking to delete all objects and perform full synchronization".format( sender))
                return manager.synchronize( WrongEpoch(sender.outgoing_epoch),
                                            destinations = [sender])
            session = manager.session
            max_serial = obj.serial
            logger.info("{} has serial {}; synchronizing changes since then".format( sender, max_serial))
            if obj.serial > 0:
                for d in session.query(SyncDeleted).filter(SyncDeleted.sync_serial > max_serial):
                    try: cls, registry  = manager._find_registered_class(d.sync_type)
                    except UnregisteredSyncClass:
                        logger.error("{} is not a registered sync class for this manager, but deletes are recorded for it; forcing full resync of {}".format(
                            d.sync_type, sender))
                        sender.outgoing_epoch = datetime.datetime.now(datetime.timezone.utc)
                        session.commit()
                        yield from self.handle_i_have(self, obj, sender, manager)
                    d.registry = registry
                    manager.synchronize(d, operation = 'delete',
                                        destinations = [sender],
                                        attributes_to_sync = d.sync_primary_keys)
                    max_serial = max(max_serial, d.sync_serial)

            for c in classes_in_registries(manager.registries):
                try:
                    if self.yield_between_classes: yield
                    if not session.is_active: session.rollback()
                    to_sync = session.query(c).with_polymorphic('*').filter(c.sync_serial > obj.serial, c.sync_owner == None).all()
                except:
                    logger.exception("Failed finding objects to send {} from  {}".format(sender, c.__name__))
                    raise
                for o in to_sync:
                    max_serial = max(o.sync_serial, max_serial)
                    manager.synchronize(o,
                                        destinations = [sender])
            yield from sender.protocol.sync_drain()
            if max_serial <= obj.serial: return
            you_have = YouHave()
            you_have.serial =max_serial
            you_have.epoch = sender.outgoing_epoch
            manager.synchronize(you_have,
                                destinations = [sender])
            sender.outgoing_serial = max_serial
        finally: sender.i_have_task = None

    def handle_you_have(self, obj, sender, manager):
        manager.session.rollback()
        if not sender in manager.session: manager.session.add(sender)
        if sender.incoming_serial > obj.serial:
            logger.error("{d} claims we have serial {obj} but we already have {ours}".format(
                ours = sender.incoming_serial,
                d = sender,
                obj = obj.serial))
        sender.incoming_serial = max(sender.incoming_serial, obj.serial)
        sender.incoming_epoch = obj.epoch
        logger.debug("We have serial {s} from {d}".format(
            s = obj.serial,
            d = sender))
        manager.session.commit()

    def handle_wrong_epoch(self, obj,  sender, manager):
        if not manager.session.is_active: manager.session.rollback()
        if sender not in manager.session: manager.session.add(sender)
        sender.clear_all_objects(manager)
        sender.incoming_epoch = obj.new_epoch
        sender.incoming_serial = 0
        manager.session.commit()
        i_have = IHave()
        i_have.serial = 0
        i_have.epoch = sender.incoming_epoch
        manager.synchronize( i_have,
                             destinations = [sender])
        

sql_meta_messages = _SqlMetaRegistry()


class IHave(Synchronizable):
    sync_primary_keys = ('serial','epoch')
    sync_registry = sql_meta_messages
    serial = sync_property()
    epoch = sync_property(encoder = encoders.datetime_encoder,
                          decoder = encoders.datetime_decoder)

class YouHave(IHave):
    "Same structure as IHave message; sent to update someone's idea of their serial number"
    pass

    

class WrongEpoch(SyncError):
    sync_registry = sql_meta_messages
    new_epoch = sync_property(constructor = 1,
                              encoder = encoders.datetime_encoder,
                              decoder = encoders.datetime_decoder)

    def __init__(self, newepoch, **args):
        self.new_epoch = newepoch
        super().__init__(*args)
        

you_have_timeout = 0.5

async def gen_you_have_task(sender, manager):
    await asyncio.sleep(you_have_timeout)
    try:
        if not sender.protocol and not sender.protocol.loop: return
    except: return #loop or connection closed
    serial = sender.outgoing_serial
    await sender.protocol.sync_drain()
    sender.you_have_task = None
    you_have = YouHave()
    you_have.epoch = sender.outgoing_epoch
    you_have.serial = sender.outgoing_serial
    manager.synchronize(you_have,
                        destinations = [sender])

def process_column(name, col, wraps = True):
    d = {}
    if wraps: d['wraps'] = col
    if col.type.__class__ in encoders.type_map:
        entry = encoders.type_map[col.type.__class__]
        d.update(encoder = entry['encoder'],
                 decoder = entry['decoder'])
    return sync_property(**d)

def classes_in_registries(registries):
    "Return the set of SqlSynchronizables that cover a set of registries.  In particular, for joined (and single-table) inheritance, use the most base mapped class with with_polymorphic(*) rather than visiting objects multiple times."
    from .base import SqlSynchronizable
    def chase_down_inheritance(c):
        while True:
            m = inspect(c) # c's mapper
            if m.concrete or (not m.inherits) :
                return c
            c = m.inherits.class_
            
    classes = set()
    for reg in registries:
        for c in reg.registry.values(): #enumerate all classes
            if issubclass(c, SqlSynchronizable):
                classes.add(chase_down_inheritance(c))
    return classes

