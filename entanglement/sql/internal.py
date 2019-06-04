#!/usr/bin/python3
# Copyright (C) 2017, 2018, 2019, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, datetime, iso8601, uuid

from ..interface import Synchronizable, SyncRegistry, SyncError, sync_property, SyncBadEncodingError, SyncUnauthorized
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
        self.register_operation('delete', operations.delete_operation)

    def sync_context(self, manager, **info):
        class context:

            def __enter__(self, *args):
                return self

            def __exit__(self, *args):
                self.session.close()


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
                sender.my_owners = obj.owners
                manager.loop.create_task(self.handle_my_owners(obj, manager, sender))
            else: raise ValueError("Unexpected message")
        finally:
            manager.session.rollback()

    def incoming_delete(self, obj, sender, context, manager, **info):
        assert isinstance(obj,base.SyncOwner)
        assert sender.dest_hash == obj.dest_hash
        session = context.session
        obj.clear_all_objects(session = session, manager = manager)
        session.delete(obj)
        session.commit()
        
        
        
    def handle_sync_owner_sync(self, obj, manager, sender, context):
        session = context.session
        assert obj in session
        session.commit()
        session.refresh(obj)
        try: sender.my_owners.append(obj.id)
        except AttributeError: pass
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
            if sender.dest_hash not in manager._connections: return
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
            owner_condition = base.SyncOwner.id == owner.id
            if owner.id == sender.first_local_owner:
                owner_condition = owner_condition | (base.SyncOwner.id == None)
            if obj.serial > 0:
                for d in session.query(SyncDeleted).outerjoin(base.SyncOwner).filter(SyncDeleted.sync_serial > max_serial, owner_condition):
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
                                        attributes_to_sync = (set(d.sync_primary_keys) | {'sync_serial'}))
                    max_serial = max(max_serial, d.sync_serial)

            for c, r in classes_in_registries(manager.registries):
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
            sender.received_i_have.add(owner.id)
            if not owner.sync_is_local:
                max_serial = owner.incoming_serial
            if max_serial <= obj.serial: return
            you_have = YouHave()
            you_have.serial =max_serial
            you_have.epoch = owner.outgoing_epoch
            you_have._sync_owner = owner.id
            you_have.sync_owner = owner
            manager.synchronize(you_have,
                                destinations = [sender])
            owner.outgoing_serial = max_serial
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
        for c in manager.connections:
            if c.dest == sender: continue
            if owner.id in getattr(c.dest, 'received_i_have', []):
                c.dest.send_you_have.add(owner)
                schedule_you_have(c.dest, manager)
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
        sender.first_owner = obj.owners[0]
        first_owner = session.query(base.SyncOwner).get(sender.first_owner)
        if first_owner.dest_hash != sender.dest_hash:
            raise SyncUnauthorized("{} is not one of {}'s owners".format(
                first_owner, sender))
        old_owners = session.query(base.SyncOwner).filter(base.SyncOwner.dest_hash == sender.dest_hash, base.SyncOwner.id.notin_(obj.owners))
        try: del sender.my_owners
        except AttributeError: pass
        for o in old_owners:
            o.clear_all_objects(manager = manager, session = session)
            manager.synchronize(o, operation = 'delete', exclude = [sender])
            session.delete(o)
            session.commit()
            yield

    def should_listen(self, msg, cls, operation, **info):
        if operation == 'delete':
            if not issubclass(cls, base.SyncOwner):
                raise SyncUnauthorized('Only SyncOwner supports delete')
            # SyncOwner.sync_construct confirms it is from the right sender already
        return super().should_listen(msg, cls, operation = operation, **info)
    




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


    @classmethod
    def sync_construct(cls, msg, context, **info):
        obj = cls()
        obj.generated_locally = False
        populate_owner_from_msg(msg, obj, context.session)
        return obj

class YouHave(IHave):
    "Same structure as IHave message; sent to update someone's idea of their serial number"




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

def schedule_you_have(dest, manager):
                    if not dest.you_have_task:
                        dest.you_have_task = manager.loop.create_task(gen_you_have_task(dest, manager))
                        dest.you_have_task._log_destroy_pending = False

async def gen_you_have_task(sender, manager):
    await asyncio.sleep(you_have_timeout)
    if (not hasattr(sender,'protocol') ) or sender.protocol.is_closed(): return
    you_haves = []
    for o in sender.send_you_have:
        you_have = YouHave()
        you_have.epoch = o.outgoing_epoch
        you_have.serial = o.outgoing_serial if o.sync_is_local else o.incoming_serial
        you_have._sync_owner = o.id
        you_have.sync_owner = o
        you_haves.append(you_have)
    sender.send_you_have.clear()
    await sender.protocol.sync_drain()
    for you_have in you_haves:
        manager.synchronize(you_have,
                        destinations = [sender])

    sender.you_have_task = None
    if len(sender.send_you_have) > 0:
        schedule_you_have(sender, manager)
        

def process_column(name, col, wraps = True):
    d = {}
    if wraps: d['wraps'] = col
    if col.type.__class__ in encoders.type_map:
        entry = encoders.type_map[col.type.__class__]
        d.update(encoder = entry['encoder'],
                 decoder = entry['decoder'])
    return sync_property(**d)

def classes_in_registries(registries):
    "yield  tuples of ( of SqlSynchronizabl, registry) that cover the classes in s that cover a set of registries.  In particular, for joined (and single-table) inheritance, use the most base mapped class with with_polymorphic(*) rather than visiting objects multiple times."
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
                c = chase_down_inheritance(c)
                if c not in classes:
                    yield c, reg
                    classes.add(c)



async def handle_connected(destination, manager, session):
    from .base import SyncOwner
    get_or_create(session, SyncOwner, {'dest_hash': None})
    session.commit()
    my_owners = session.query(SyncOwner).filter((SyncOwner.dest_hash == None)|(SyncOwner.dest_hash != destination.dest_hash)).all()
    my_owners = list(filter( lambda o: manager.should_send(o, registry = o.__class__.sync_registry, destination = destination, sync_type = o.__class__, manager = manager), my_owners))
    for o in my_owners:
        manager.synchronize(o, destinations = [destination])
    my_owner = MyOwners()
    my_owner.owners = [o.id for o in my_owners]
    if len(my_owner.owners) == 0:
        destination.first_local_owner = None
    else:
        destination.first_local_owner = my_owner.owners[0]
        # Drain the SyncOwners before sending MyOwners, because if the
        # owner is not received by MyOwner reception it will be ignored
        # for IHave handling.
        await destination.protocol.sync_drain()
        manager.synchronize(my_owner, destinations = [destination])

def trigger_you_haves(manager, serial):
    if serial == 0: return
    for c in manager.connections:
        for o in manager.session.query(base.SyncOwner).filter((base.SyncOwner.dest_hash == None)):
            o.outgoing_serial = max(o.outgoing_serial, serial)
            if o.id in getattr(c.dest, 'received_i_have', []):
                c.dest.send_you_have.add(o)
                schedule_you_have(c.dest, manager)

class SyncPriorityProperty:
    "memoize topological sort of  a given metadata's tables"
    def __get__(self, instance, cls):
        try: return cls.metadata.sync_metadata_priorities[cls.__table__]
        except (AttributeError, LookupError): pass
        assert issubclass(cls, base.SqlSynchronizable)
        order = 100
        priorities = {}
        for t in cls.metadata.sorted_tables:
            priorities[t] = order
            if t.foreign_keys: order += 1
        cls.metadata.sync_metadata_priorities = priorities
        return priorities[cls.__table__]
    
from . import base
