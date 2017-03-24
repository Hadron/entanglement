#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, iso8601

from ..interface import Synchronizable, SyncRegistry, SyncError, sync_property
from . import encoders
from .. import interface

class _SqlMetaRegistry(SyncRegistry):

    def sync_receive(self, obj, sender, manager, **info):
        if isinstance(obj,IHave):
            manager.loop.create_task(self.handle_i_have(obj, sender, manager))
        elif isinstance(obj,YouHave):
            self.handle_you_have(obj, sender, manager)
        elif isinstance(obj, WrongEpoch):
            self.handle_wrong_epoch(obj, sender, manager)
        else: raise ValueError("Unexpected message")

    async def handle_i_have(self, obj, sender, manager):
        from .base import SqlSynchronizable
        if sender.cert_hash not in manager._connections: return
        if sender.outgoing_epoch != obj.epoch:
            return sender.protocol.synchronize_object( WrongEpoch(sender.outgoing_epoch))
        session = manager.session
        max_serial = 0
        for reg in manager.registries:
            for c in reg.registry.values(): #enumerate all classes
                if isinstance(c, SqlSynchronizable):
                    to_sync = session.query(c).filter(c.sync_serial > obj.serial, c.sync_owner == None).all()
                    for o in to_sync:
                        max_serial = max(o.sync_serial, max_serial)
                        sender.protocol.synchronize_object(o)
        await  sender.protocol.sync_drain()
        you_have = YouHave()
        you_have.serial =max_serial
        you_have.epoch = sender.outgoing_epoch
        sender.protocol.synchronize_object(you_have)

    def handle_you_have(self, obj, sender, manager):
        sender.incoming_serial = obj.serial
        sender.incoming_epoch = obj.epoch
        if not sender in manager.session: manager.session.add(sender)
        manager.session.commit()

    def handle_wrong_epoch(self, obj,  sender, manager):
        if sender not in manager.session: manager.session.add(sender)
        sender.clear_all_objects(manager)
        sender.incoming_epoch = obj.new_epoch
        sender.incoming_serial = 0
        manager.session.commit()
        i_have = IHave()
        i_have.serial = 0
        i_have.epoch = sender.incoming_epoch
        sender.protocol.synchronize_object(i_have)
        

sql_meta_messages = _SqlMetaRegistry()


class IHave(Synchronizable):
    sync_primary_keys = ('serial','epoch')
    sync_registry = sql_meta_messages
    serial = sync_property()
    epoch = sync_property(encoder = encoders.datetime_encoder('epoch'),
                          decoder = encoders.datetime_decoder('epoch'))

class YouHave(IHave):
    "Same structure as IHave message; sent to update someone's idea of their serial number"
    pass

    

class WrongEpoch(SyncError):
    sync_registry = sql_meta_messages
    new_epoch = sync_property(constructor = 1,
                              encoder = encoders.datetime_encoder('new_epoch'),
                              decoder = encoders.datetime_decoder('new_epoch'))

    def __init__(self, newepoch, **args):
        self.new_epoch = newepoch
        super().__init__(*args)
        

you_have_timeout = 0.5

async def gen_you_have_task(sender):
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
    sender.protocol.synchronize_object(you_have)
