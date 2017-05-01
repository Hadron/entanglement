#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import contextlib, datetime, json, sqlalchemy
from datetime import timezone

from sqlalchemy import Column, Table, String, Integer, DateTime, ForeignKey, inspect, TEXT
from sqlalchemy.orm import load_only
import sqlalchemy.exc
import sqlalchemy.orm, sqlalchemy.ext.declarative, sqlalchemy.ext.declarative.api
from ..util import CertHash, SqlCertHash, get_or_create
from .. import interface, network
from ..protocol import logger
from . import internal as _internal

class SqlSyncSession(sqlalchemy.orm.Session):

    def __init__(self, *args, manager = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.manager = manager
        self.sync_dirty = set()
        self.sync_deleted = set()
        @sqlalchemy.events.event.listens_for(self, "before_flush")
        def before_flush(session, internal, instances):
            serial_insert = Serial.__table__.insert().values(timestamp = datetime.datetime.now())
            serial_flushed = False
            for inst in session.new | session.dirty:
                if isinstance(inst, SqlSynchronizable) and session.is_modified(inst):
                    inspect_inst = inspect(inst)
                    modified_attrs = frozenset(inst.__class__._sync_properties.keys()) - frozenset(inspect_inst.unmodified)
                    self.sync_dirty.add((inst, modified_attrs))
                    if inst.sync_owner_id is None and inst.sync_owner is None:
                        if not serial_flushed:
                            new_serial = session.execute(serial_insert).lastrowid
                            serial_flushed = True
                        inst.sync_serial = new_serial
                    else:  inst.sync_owner # get while we can
            for inst in session.deleted:
                if isinstance( inst, SqlSynchronizable):
                    self.sync_deleted.add(inst)
                    if inst.sync_owner_id is None and inst.sync_owner is None:
                        if not serial_flushed:
                            new_serial = session.execute(serial_insert).lastrowid
                            serial_flushed = True
                        inst.sync_serial = new_serial
                        deleted = SyncDeleted()
                        deleted.sync_serial = new_serial
                        deleted.sync_type = inst.sync_type
                        deleted.primary_key = json.dumps(inst.to_sync())
                        session.add(deleted)
                    else: inst.sync_owner #grab while we can issue sql

        @sqlalchemy.events.event.listens_for(self, "after_commit")
        def receive_after_commit(session):
            if self.manager:
                for x , attrs in self.sync_dirty: assert not self.is_modified(x)
                self.sync_commit( detach_forwarded = False)
                
        @sqlalchemy.events.event.listens_for(self, 'after_rollback')
        def after_rollback(session):
            session.sync_dirty.clear()
            session.sync_deleted.clear()

    def sync_commit(self, detach_forwarded = True):
        if self.sync_dirty or  self.sync_deleted:
            # For updates, if we are the owner, we flood the update
            # (by including in dirty_objects in do_sync).  If not, we
            # unicast toward the owner, and we trim down the
            # attributes we're sending to those that are actually
            # changed so that updates combine more effectively
            s_new = sqlalchemy.orm.Session(bind = self.bind)
            objects = []
            forward_objects = [] #obj, dest, attrs
            for o, modified_attrs  in self.sync_dirty:
                forward_dest = None
                if o.sync_owner_id or o.sync_owner: #an update The
                    # manager may have a subclass of
                    # SqlSyncDestination that is loaded and almost
                    # certainly has a different session, so we want
                    # their destination object.
                    forward_dest = self.manager.dest_by_cert_hash(o.sync_owner.destination.cert_hash)
                    if not forward_dest:
                        logger.error("Requested forward to owner of {}, but manager doesn't recognize {} as a destination".format(
                            o, o.sync_owner.destination))
                        continue
                    # If we are going to detach forwarded, do it now
                    # before we re-alias o
                    if detach_forwarded: self.expunge(o)

                o = s_new.merge(o)
                sqlalchemy.orm.session.make_transient(o)
                sqlalchemy.orm.session.make_transient_to_detached(o)
                if forward_dest:
                    forward_objects.append((o, forward_dest, modified_attrs))
                else: objects.append(o)
            # For deleted, we break the loop in that we refuse to flood deletes for objects we don't have
            self.manager.loop.call_soon_threadsafe(self._do_sync, objects, list(self.sync_deleted), forward_objects)
        self.sync_dirty.clear()
        self.sync_deleted.clear()

    def _do_sync(self, dirty_objects, deleted_objects, forward_objects):
        #This is called in the event loop and thus in the thread of the manager
        if dirty_objects or deleted_objects:
            serial = max(map( lambda x: x.sync_serial, dirty_objects + deleted_objects))
            for o in dirty_objects:
                self.manager.synchronize(o)
            for o in deleted_objects:
                self.manager.synchronize(o, operation = 'delete',
                                     attributes_to_sync  = o.sync_primary_keys)
            for c in self.manager.connections:
                dest = c.dest
                dest.outgoing_serial = max(dest.outgoing_serial, serial)
                if not dest.you_have_task:
                    dest.you_have_task = self.manager.loop.create_task(_internal.gen_you_have_task(dest, self.manager))
                    dest.you_have_task._log_destroy_pending = False

        for o, dest, attrs in forward_objects:
            self.manager.synchronize(o, operation = 'forward',
                                     destinations = [dest],
                                     attributes_to_sync = attrs |  set(o.sync_primary_keys))


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
                ns[k] = _internal.process_column(k, v)
        return interface.SynchronizableMeta.__new__(cls, name, bases, ns)

    def __init__(cls, name, bases, ns):
        super().__init__(name, bases, ns)
        # If _sync_primary_keys is not set, it raises
        # NotImplementedError; catch that and construct primary
        # keys if we can
        try: cls.sync_primary_keys
        except NotImplementedError:
            try:
                cls.sync_primary_keys = tuple(map(
                    lambda x: x.name, inspect(cls).primary_key))
            except sqlalchemy.exc.NoInspectionAvailable: pass


class SqlSyncRegistry(interface.SyncRegistry):

    inherited_registries = [_internal.sql_meta_messages]

    def __init__(self, *args,
                 sessionmaker = None,
                 bind = None,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.register_operation('sync', self.incoming_sync)
        self.register_operation( 'delete', self.incoming_delete)
        self.register_operation('forward', self.incoming_forward) # forward to owner
        if sessionmaker is None:
            sessionmaker = sync_session_maker()
        if bind is not None: sessionmaker.configure(bind = bind)
        self.sessionmaker = sessionmaker

    @classmethod
    def create_bookkeeping(self, bind):
        _internal_base.metadata.create_all(bind = bind)
    def ensure_session(self, manager):
        if not hasattr(manager, 'session'):
            manager.session = self.sessionmaker()
            if not manager.session.is_active: manager.session.rollback()

    def associate_with_manager(self, manager):
        self.ensure_session(manager)

    @contextlib.contextmanager
    def sync_context(self, **info):
        session = self.sessionmaker()
        class Context: pass
        ctx = Context()
        ctx.session = session
        try: yield ctx
        finally:
            session.rollback()
            session.close()

    def incoming_delete( self, obj, context, manager, sender, **info):
        session = context.session
        ins = inspect(obj)
        if not ins.persistent: return #flood back on forward delete to owner
        if  (obj.sync_owner is None or sender != obj.sync_owner.destination ):
            session.manager = manager #flood it
        assert obj in session
        session.delete(obj)
        session.commit()

    def incoming_forward(self, obj, context, sender, manager, **info):
        if obj.sync_owner is not None:
            raise interface.SyncBadOwnerError("Currently you can only forward to the object's direct owner")
        assert obj in context.session
        context.session.manager = manager #Flood out the new update
        context.session.commit()

    def incoming_sync(self, object, manager, context, **info):
        # By this point the owner check has already been done
        session = context.session
        assert object.sync_owner is not None
        assert object in session
        session.commit()



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
    #outgoing_serial is managed but is transient
    incoming_epoch = Column(sqlalchemy.types.DateTime(True),
                          default = lambda: datetime.datetime.now(datetime.timezone.utc), nullable = False)
    outgoing_epoch = Column(sqlalchemy.types.DateTime(True),
                          default = lambda: datetime.datetime.now(datetime.timezone.utc), nullable = False)
    bw_per_sec = Column(Integer, default = 10000000,
                        nullable = False)

    def __hash__(self):
        return hash(self.cert_hash)

    def __eq__(self, other):
        if isinstance(other,SqlSyncDestination):
            return other.cert_hash == self.cert_hash
        else: return super().__eq__(other)
    def __init__(self, *args, **kwargs):
        network.SyncDestination.__init__(self, *args, **kwargs)
        self.outgoing_serial = 0
        self.you_have_task = None
        self.i_have_task = None

    @sqlalchemy.orm.reconstructor
    def reconstruct(self):
        self.protocol = None
        self.outgoing_serial = 0
        self.you_have_task = None
        self.connect_at = 0
        self.server_hostname = None
        self.i_have_task = None

    def clear_all_objects(self, manager = None,
                          *, registries = None, session = None):
        if manager:
            session = manager.session
            registries = manager.registries
        assert session and registries
        logger.info("Deleting all objects from {}".format(self))
        session.rollback()
        subquery = session.query(SyncOwner.id).filter(SyncOwner.destination == self)
        for c in _internal.classes_in_registries(registries):
            for o in session.query(c).filter(c.sync_owner_id.in_(subquery)): session.delete(o)
        session.flush()

    async def connected(self, manager, *args, **kwargs):
        res = await super().connected(manager, *args, **kwargs)
        if not self in manager.session: manager.session.add(self)
        manager.session.commit()
        i_have = _internal.IHave()
        i_have.serial = self.incoming_serial
        i_have.epoch = self.incoming_epoch
        manager.synchronize(i_have,
                            destinations = [self])
        return res




class SyncOwner(_internal_base):
    __tablename__ = "sync_owners"
    id = Column(Integer, primary_key = True)
    destination_id = Column(Integer, ForeignKey(SqlSyncDestination.id, ondelete = 'cascade'),
                            index = True, nullable = False)
    destination = sqlalchemy.orm.relationship(SqlSyncDestination, lazy = 'joined')
    # Note that ihave and youhave probably need to be owner level
    # messages rather than destination level messages, but we don't
    # even pretend to deal with that complexity yet.

    @classmethod
    def find_or_create(self, session, dest, msg):
        return get_or_create(session, SyncOwner,
                             {'destination':
                              get_or_create(session, SqlSyncDestination,
                                            {'cert_hash': dest.cert_hash},
                                            {'name': dest.name,
                                             'host': dest.host})})

class SyncDeleted( _internal_base):
    __tablename__ = 'sync_deleted'
    id = Column( Integer, primary_key = True)
    sync_type = Column( String(128), nullable = False)
    primary_key = Column( TEXT, nullable = False)
    sync_serial = Column(Integer, nullable = False, index = True)

    @property
    def _obj(self):
        if hasattr(self, "_constructed_obj"): return self._constructed_obj
        if not self.registry: return None
        cls = self.registry.registry[self.sync_type]
        msg = json.loads(self.primary_key)
        msg['_sync_type'] =  self.sync_type
        msg['_sync_operation'] = 'delete'
        self._constructed_obj = cls.sync_receive(msg, context = self, operation = 'delete', sender = None)
        return self._constructed_obj

    def proxyfn(method):
        class descriptor:
            def __get__(self, instance, owner):
                return getattr(instance._obj, method)
        return descriptor()
    # This class is not Synchronizable because we don't want the
    # metaclass, but this class can be passed to manager.synchronize
    # (although cannot be the target of a receive).  That's intended
    # for the special case of the IHave handler synthesizing delete
    # operations for objects deleted since the serial number the other
    # side has.  To do this, we construct an object by "receiving"
    # only its primary keys and then proxying methods like to_sync to
    # that object.  Since we proxy sync_compatible and sync_hash, a
    # resurrection of the object will coalesce with us in an outgoing
    # sync queue.  Since IHave processes deletes before other objects,
    # the resurrection will take priority.
    for meth in ('to_sync', 'sync_should_send', 'sync_primary_keys',
                 'sync_hash', 'sync_compatible'):
        locals()[meth] = proxyfn(meth)
    del proxyfn



class SqlSynchronizable(interface.Synchronizable):

    '''
    A SQLAlchemy mapped class that can be synchronized.  By default
    every column becomes e sync_property.  You can explicitly set
    sync_property around a Column if you need to override the encoder
    or decoder, although see hadron.entanglement.sql.encoder for a
    type map that can be used in most cases.  The sync_should_send
    method on this class and should_send on any containing registries
    must be able to make a decision about whether to send a deleted
    object when presented with an object containing only the primary
    keys and no relationships.

    '''

    @sqlalchemy.ext.declarative.api.declared_attr
    def    sync_serial(self):
        if hasattr(self,'__table__'): return
        return    Column(Integer, nullable=False, index = True)

    sync_serial = interface.sync_property(wraps = sync_serial)

    @sqlalchemy.ext.declarative.api.declared_attr
    def sync_owner_id(self):
        if hasattr(self, '__table__'): return
        return Column(Integer, ForeignKey(SyncOwner.id, ondelete = 'cascade'), index = True)

    @sqlalchemy.ext.declarative.api.declared_attr
    def sync_owner(self):
        if hasattr(self, '__table__') and not 'sync_owner_id' in self.__table__.columns: return
        if hasattr(self,'__table__'): return sqlalchemy.orm.relationship(SyncOwner, foreign_keys = [self.__table__.c.sync_owner_id],
                                                                         primaryjoin = SyncOwner.id == self.__table__.c.sync_owner_id)
        return sqlalchemy.orm.relationship(SyncOwner, foreign_keys = [self.sync_owner_id])


    @property
    def sync_is_local(self):
        return self.sync_owner is None

    @classmethod
    def _sync_construct(cls, msg, context, operation = None, manager = None, registry = None,
                        **info):
        # post condition: in the sync operation, either the object is
        # new or it is already owned by the same owner.  For other
        # operations, either the object is new, we own it, or it is
        # owned by its existing owner.
        if manager and registry: registry.ensure_session(manager)
        session = None
        obj = None
        owner = None
        if hasattr(context,'session'):
            primary_keys = map(lambda x:x.name, inspect(cls).primary_key)
            try: primary_key_values = tuple(map(lambda k: msg[k], primary_keys))
            except KeyError as e:
                raise interface.SyncBadEncodingError("All primary keys must be present in the encoding", msg = msg) from e
            sender = info['sender']
            session = context.session
            sender_inspect = inspect(sender)
            load_required = (not sender_inspect.persistent) or  sender_inspect.modified
            sender = session.merge(sender, load = load_required)
            obj = session.query(cls).get(primary_key_values)
            owner = SyncOwner.find_or_create(session, sender, msg)
            if obj is not None:
                if obj.sync_owner_id is None and operation == 'sync':
                    raise interface.SyncBadOwnerError("{} tried to synchronize our object to us".format(
                        sender))
                elif (obj.sync_owner_id is not None) and (obj.sync_owner_id != owner.id):
                    raise interface.SyncBadOwnerError("Object owned by {}, but sent by {}".format(
                        obj.sync_owner.destination, owner.destination))
        if obj is not None:
            for k in primary_keys: del msg[k]
            return obj
        obj =  super()._sync_construct(msg,**info)
        obj.sync_owner = owner
        if session:
            assert owner is not None
            session.add(obj)
        return obj


def sql_sync_declarative_base(*args, registry = None,
                              registry_class = SqlSyncRegistry,
                              **kwargs):
    base =  sqlalchemy.ext.declarative.declarative_base(cls = SqlSynchronizable, metaclass = SqlSyncMeta, *args, **kwargs)
    base.registry = registry or registry_class()
    return base

def sync_manager_destinations(manager, session = None,
                              cls = SqlSyncDestination,
                              force_resync = False):
    '''Query for :class SqlSyncDestination objects in the :param session.  Add any not currently in the manager; remove any present in the manager but no longer in the database.  If force_resync is True, then for any added destination, a resynchronization will be forced in both directions.
'''
    if session is None: session = manager.session #probably won't work initially
    if not session.is_active: session.rollback()
    database_destinations = set(session.query(cls).all())
    manager_destinations = set(filter(lambda x: isinstance(x, cls), manager.destinations))
    for d in manager_destinations - database_destinations:
        manager.remove_destination(d)
    for d in database_destinations - manager_destinations:
        session.expunge(d)
        if force_resync:
            d.incoming_epoch = datetime.datetime.now(datetime.timezone.utc)
            d.outgoing_epoch = d.incoming_epoch
            manager.session.add(d)
        manager.add_destination(d)
    if hasattr(manager, 'session'):
        manager.session.commit()
