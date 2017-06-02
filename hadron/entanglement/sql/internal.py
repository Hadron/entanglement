#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, datetime, iso8601, uuid

from ..interface import Synchronizable, SyncRegistry, SyncError, sync_property, SyncBadEncodingError
from . import encoders
from .. import interface, operations
from ..network import logger
from ..util import get_or_create
from sqlalchemy import inspect

class _SqlMetaRegistry(SyncRegistry):

    # If true, then yield between each class while handling an IHave.  In general it is better to  let the event loop have an opportunity to run, but this makes testing much more complicated so it can be disabled.
    yield_between_classes = True

    def __init__(self):
        super().__init__()
        self.register_operation('sync', operations.sync_operation)
        self.register_operation('forward', operations.forward_operation)

    def sync_context(self, manager, **info):
        class context:

            def __enter__(self, *args):
                return self

            def __exit__(self, *args):
                self.session.rollback()

        ctx = context()
        if hasattr(manager, 'session'):
            ctx.session = base.SqlSyncSession(bind = manager.session.bind)

        return ctx

    def incoming_forward(self, obj, **info):
        return self.incoming_sync(obj, **info)

    def incoming_sync(self, obj, sender, manager, context, **info):
        try:
            # remember to handle subclasses first
            if isinstance(obj, base.SyncOwner):
                self.handle_sync_owner_sync(obj, manager, sender, context)
            elif isinstance(obj,YouHave):
                self.handle_you_have(obj, sender, manager)
            elif isinstance(obj,IHave):
                sender.i_have_task = manager.loop.create_task(self.handle_i_have(obj, sender, manager))
            elif isinstance(obj, WrongEpoch):
                self.handle_wrong_epoch(obj, sender, manager)
            elif isinstance(obj, MyOwners):
                manager.loop.create_task(self.handle_my_owners(obj, manager, sender))
            else: raise ValueError("Unexpected message")
        finally:
            manager.session.rollback()

    def handle_sync_owner_sync(self, obj, manager, sender, context):
        session = context.session
        assert obj in session
        session.commit()
        i_have = IHave()
        i_have.serial = obj.incoming_serial
        i_have.epoch = obj.incoming_epoch
        i_have._sync_owner = obj.id
        manager.synchronize(i_have, destinations = [sender],
                            operation = 'forward')


    @asyncio.coroutine
    def handle_i_have(self, obj, sender, manager):
        from .base import SqlSynchronizable, SyncDeleted
        try:
            session = manager.session
            if sender.cert_hash not in manager._connections: return
            owner = obj.sync_owner
            if owner.outgoing_epoch.tzinfo is None:
                outgoing_epoch = owner.outgoing_epoch.replace(tzinfo = datetime.timezone.utc)
            else: outgoing_epoch = owner.outgoing_epoch
            if outgoing_epoch != obj.epoch:
                logger.info("{} had wrong epoch for {}; asking to delete all objects and perform full synchronization".format( sender, owner))
                return manager.synchronize( WrongEpoch(owner, owner.outgoing_epoch),
                                            destinations = [sender])
            max_serial = obj.serial
            logger.info("{s} has serial {n} for {o}; synchronizing changes since then".format( o = owner,
                                                                                                  n = max_serial,
                                                                                                  s = sender))
            if obj.serial > 0:
                for d in session.query(SyncDeleted).filter(SyncDeleted.sync_serial > max_serial):
                    try: cls, registry  = manager._find_registered_class(d.sync_type)
                    except UnregisteredSyncClass:
                        logger.error("{} is not a registered sync class for this manager, but deletes are recorded for it; forcing full resync of {}".format(
                            d.sync_type, owner))
                        owner.outgoing_epoch = datetime.datetime.now(datetime.timezone.utc)
                        session.commit()
                        yield from self.handle_i_have(self, obj, sender, manager)
                    d.registry = registry
                    manager.synchronize(d, operation = 'delete',
                                        destinations = [sender],
                                        attributes_to_sync = d.sync_primary_keys)
                    max_serial = max(max_serial, d.sync_serial)

            for c in classes_in_registries(manager.registries):
                owner_condition = base.SyncOwner.id == owner.id
                if owner.id == sender.first_local_owner:
                    owner_condition = owner_condition | (base.SyncOwner.id == None)
                try:
                    if c is base.SyncOwner or issubclass(c, base.SyncOwner): continue
                    if self.yield_between_classes: yield
                    if not session.is_active: session.rollback()
                    to_sync = session.query(c).with_polymorphic('*') \
                                              .outerjoin(base.SyncOwner).filter(c.sync_serial > obj.serial, owner_condition).all()
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
            you_have.epoch = owner.outgoing_epoch
            you_have._sync_owner = owner.id
            you_have.sync_owner = owner
            manager.synchronize(you_have,
                                destinations = [sender])
            sender.outgoing_serial = max_serial
        finally: sender.i_have_task = None

    def handle_you_have(self, obj, sender, manager):
        manager.session.rollback()
        owner = obj.sync_owner
        owner = manager.session.merge(owner)
        if owner.incoming_serial > obj.serial:
            logger.error("{d} claims we have serial {obj} but we already have {ours}".format(
                ours = owner.incoming_serial,
                d = owner,
                obj = obj.serial))
        owner.incoming_serial = max(owner.incoming_serial, obj.serial)
        owner.incoming_epoch = obj.epoch
        logger.debug("We have serial {s} from {d}".format(
            s = obj.serial,
            d = owner))
        manager.session.commit()

    def handle_wrong_epoch(self, obj,  sender, manager):
        manager.session.rollback()
        if sender not in manager.session: manager.session.add(sender)
        owner = manager.session.merge(obj.owner)
        owner.clear_all_objects(manager)
        owner.incoming_epoch = obj.new_epoch
        owner.incoming_serial = 0
        manager.session.commit()
        i_have = IHave()
        i_have.serial = 0
        i_have.epoch = owner.incoming_epoch
        i_have._sync_owner = owner.id
        manager.synchronize( i_have,
                             operation = 'forward',
                             destinations = [sender])

    @asyncio.coroutine
    def handle_my_owners(self, obj, manager, sender):
        session = manager.session
        session.rollback()
        old_owners = session.query(base.SyncOwner).filter(base.SyncOwner.destination == sender, base.SyncOwner.id.notin_(obj.owners))
        for o in old_owners:
            o.clear_all_objects(manager = manager, session = session)
            session.delete(o)
            session.commit()
            yield





sql_meta_messages = _SqlMetaRegistry()


def populate_owner_from_msg(msg, obj, session):
    owner = msg.get('_sync_owner', None)
    if not owner: owner = getattr(obj, '_sync_owner', None)
    if owner is None:
        raise interface.SyncBadEncodingError("Owner not specified")
    obj.sync_owner = session.query(base.SyncOwner).get(owner)
    if not obj.sync_owner:
        raise SyncBadEncodingError("You must synchronize the sync_owner, then drain before synchronizing IHave", msg = msg)
    session.expunge(obj.sync_owner)


class IHave(Synchronizable):
    sync_primary_keys = ('serial','epoch', '_sync_owner')
    sync_registry = sql_meta_messages
    serial = sync_property()
    epoch = sync_property(encoder = encoders.datetime_encoder,
                          decoder = encoders.datetime_decoder)
    _sync_owner = sync_property(
        encoder = encoders.uuid_encoder,
        decoder = encoders.uuid_decoder,)

    generated_locally = True
    def sync_should_send(self, destination, operation, **info):
        # An IHave is forwarded towards the owner.  We don't want it flooded past the first hop.
        return self.generated_locally
        # that because this is only for the owner.  We also want to stop flooding at the first hop.


    @classmethod
    def sync_construct(cls, msg, context, **info):
        obj = cls()
        obj.generated_locally = False
        populate_owner_from_msg(msg, obj, context.session)
        return obj

class YouHave(IHave):
    "Same structure as IHave message; sent to update someone's idea of their serial number"

    def sync_should_send(self, destination, **info):
        # Do not permit flooding.  That is, only send in if our owner is local
        return self.sync_is_local



class WrongEpoch(SyncError):
    sync_registry = sql_meta_messages
    new_epoch = sync_property(constructor = 2,
                              encoder = encoders.datetime_encoder,
                              decoder = encoders.datetime_decoder)
    _sync_owner = sync_property(
        constructor = 1, encoder = encoders.uuid_encoder,
        decoder = encoders.uuid_decoder)


    def __init__(self, owner, newepoch, **args):
        self.new_epoch = newepoch
        if isinstance(owner, base.SyncOwner):
            self._sync_owner = owner.id
        else: self._sync_owner = owner
        super().__init__(*args)

    def sync_receive_constructed(self, msg, manager, **info):
        super().sync_receive_constructed(msg, manager = manager, **info)
        populate_owner_from_msg(msg, self, manager.session)
        # It's not really true that we're owned by that SyncOwner;
        # it's more that we're about that sync_owner.  We still use
        # populate_owner_from_msg, but we'd like the owner in a
        # different attribute
        self.owner = self.sync_owner
        self.sync_owner = interface.EphemeralUnflooded
        if self.owner.sync_is_local:
            raise interface.SyncBadOwner("{} flooded a WrongEpoch for our own owner to us".format(info['sender']))
        return self

class MyOwners(Synchronizable):

    sync_registry = sql_meta_messages
    sync_primary_keys = interface.Unique

    owners = sync_property()
    @owners.encoder
    def owners(instance, propname):
        return [str(x) for x in instance.owners]

    @owners.decoder
    def owners(instance, propname, value):
        return [uuid.UUID(x) for x in value]


you_have_timeout = 0.5

async def gen_you_have_task(sender, manager):
    await asyncio.sleep(you_have_timeout)
    try:
        if not sender.protocol and not sender.protocol.loop: return
    except: return #loop or connection closed
    you_haves = []
    for o in sender.send_you_have:
        you_have = YouHave()
        you_have.epoch = o.outgoing_epoch
        you_have.serial = o.outgoing_serial
        you_have._sync_owner = o.id
        you_have.sync_owner = o
        you_haves.append(you_have)
    sender.send_you_have.clear()
    await sender.protocol.sync_drain()
    for you_have in you_haves:
        manager.synchronize(you_have,
                        destinations = [sender])

    sender.you_have_task = None

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


async def handle_connected(destination, manager, session):
    from .base import SyncOwner, SqlSyncDestination
    get_or_create(session, SyncOwner, {'destination': None})
    session.commit()
    my_owners = session.query(SyncOwner).filter((SyncOwner.destination == None)|(SyncOwner.destination != destination)).all()
    for o in my_owners:
        manager.synchronize(o, destinations = [destination])
    my_owner = MyOwners()
    my_owner.owners = [o.id for o in my_owners]
    destination.first_local_owner = my_owner.owners[0]
    # Drain the SyncOwners before sending MyOwners, because if the
    # owner is not received by MyOwner reception it will be ignored
    # for IHave handling.
    await destination.protocol.sync_drain()
    manager.synchronize(my_owner, destinations = [destination])


from . import base
