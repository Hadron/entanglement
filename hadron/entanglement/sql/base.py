#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import contextlib, datetime, json, sqlalchemy, uuid
from datetime import timezone

from sqlalchemy import Column, Table, String, Integer, DateTime, ForeignKey, inspect, TEXT, Index
from sqlalchemy.orm import load_only
import sqlalchemy.exc
import sqlalchemy.orm, sqlalchemy.ext.declarative, sqlalchemy.ext.declarative.api
from ..util import CertHash, SqlCertHash, get_or_create, GUID
from .. import interface, network, operations
from ..protocol import logger
from . import internal as _internal

class SqlSyncSession(sqlalchemy.orm.Session):

    def __init__(self, *args, manager = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.manager = manager
        self.sync_dirty = set()
        self.sync_deleted = set()
        sqlalchemy.events.event.listens_for(self, "before_flush")(self._handle_dirty)

        @sqlalchemy.events.event.listens_for(self, "after_commit")
        def receive_after_commit(session):
            if self.manager:
                for x , attrs in self.sync_dirty: assert not self.is_modified(x)
                self.sync_commit( expunge_nonlocal = False)

        @sqlalchemy.events.event.listens_for(self, 'after_rollback')
        def after_rollback(session):
            session.sync_dirty.clear()
            session.sync_deleted.clear()

    @staticmethod
    def _handle_dirty(session, internal = None, instances = None, expunge_nonlocal = False):
        serial_insert = Serial.__table__.insert().values(timestamp = datetime.datetime.now())
        serial_flushed = False
        for inst in session.new | session.dirty:
            if isinstance(inst, SqlSynchronizable) and session.is_modified(inst):
                inspect_inst = inspect(inst)
                modified_attrs = frozenset(inst.__class__._sync_properties.keys()) - frozenset(inspect_inst.unmodified)
                session.sync_dirty.add((inst, modified_attrs))
                if (inst.sync_owner_id is None and inst.sync_owner is None) or inst.sync_is_local:
                    if not serial_flushed:
                        new_serial = session.execute(serial_insert).lastrowid
                        serial_flushed = True
                    inst.sync_serial = new_serial
                else:
                        inst.sync_owner # get while we can
                        if expunge_nonlocal:
                            session.expunge(inst)
                        elif session.manager:
                            raise NotImplementedError('The semantics of a local commit of nonlocal objects are undefined and unimplemented')
                            
        for inst in session.deleted:
            if isinstance( inst, SqlSynchronizable):
                if inst in session.sync_deleted: continue
                session.sync_deleted.add(inst)
                if (inst.sync_owner_id is None and inst.sync_owner is None) or inst.sync_is_local:
                    if not serial_flushed:
                        new_serial = session.execute(serial_insert).lastrowid
                        serial_flushed = True
                    inst.sync_serial = new_serial
                    deleted = SyncDeleted()
                    deleted.sync_serial = new_serial
                    deleted.sync_type = inst.sync_type
                    deleted.sync_owner = inst.sync_owner
                    deleted.primary_key = json.dumps(inst.to_sync(attributes = inst.sync_primary_keys))
                    session.add(deleted)
                else:
                    inst.sync_owner #grab while we can issue sql
                    if expunge_nonlocal:
                        session.expunge(inst)
                    elif session.manager:
                        raise NotImplementedError('Semantics of committing nonlocal objects is undefined and unimplemented')
                    

    def sync_commit(self, expunge_nonlocal = True):
        self._handle_dirty(self, expunge_nonlocal = expunge_nonlocal)
        # Behavior if expunge_nonlocal is false is no not fully
        # implemented; there used to be partial behavior from prior to
        # multi-owner support, but updates and deletes were handled
        # differently, and there were significant bugs around when
        # flushes happened.  For now, if expunge_nonlocal is false, we
        # will error out if there are nonlocal objects.  If that
        # changes, this function and _handle_dirty as well as _do_sync
        # will need careful refactoring.
        if self.sync_dirty or  self.sync_deleted:
            # For updates, if we are the owner, we flood the update
            # (by including in dirty_objects in do_sync).  If not, we
            # unicast toward the owner, and we trim down the
            # attributes we're sending to those that are actually
            # changed so that updates combine more effectively
            s_new = sqlalchemy.orm.Session(bind = self.bind)
            objects = []
            forward_objects = [] #obj, dest, attrs
            deleted_objects = [] #obj, dest
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

                o = s_new.merge(o)
                sqlalchemy.orm.session.make_transient(o)
                sqlalchemy.orm.session.make_transient_to_detached(o)
                if forward_dest:
                    forward_objects.append((o, forward_dest, modified_attrs))
                else: objects.append(o)
            for o in self.sync_deleted:
                if o.sync_is_local:
                    deleted_objects.append((o, None))
                else:
                    delete_dest = self.manager.dest_by_cert_hash(o.sync_owner.destination.cert_hash)
                    if not delete_dest:
                        logger.error("Requested delete to owner of {}, but manager doesn't recognize {} as a destination".format(
                            o, o.sync_owner.destination))
                        continue
                    deleted_objects.append((o, [delete_dest]))
                    
            self.manager.loop.call_soon_threadsafe(self._do_sync, objects, deleted_objects, forward_objects)
        self.sync_dirty.clear()
        self.sync_deleted.clear()

    def _do_sync(self, dirty_objects, deleted_objects, forward_objects):
        #This is called in the event loop and thus in the thread of the manager
        if dirty_objects or deleted_objects:
            del_objs = [x[0] for x in deleted_objects]
            serial = max(map( lambda x: x.sync_serial, dirty_objects + del_objs))
            for o in dirty_objects:
                self.manager.synchronize(o)
            for o, dest in deleted_objects:
                self.manager.synchronize(o, operation = 'delete',
                                     attributes_to_sync  = (set(o.sync_primary_keys)
                                                            | {'sync_serial'}),
                                         destinations = dest)
            _internal.trigger_you_haves(self.manager, serial)
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
        self.register_operation('sync', operations.sync_operation)
        self.register_operation( 'delete', operations.delete_operation)
        self.register_operation('forward', operations.forward_operation) # forward to owner
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
        if not ins.persistent: return
        assert obj in session
        session.delete(obj)
        if obj.sync_owner and obj.sync_owner.destination == sender:
            #It's from the sender.
            sd = SyncDeleted(sync_serial = obj.sync_serial,
                             sync_owner_id = obj.sync_owner_id,
                             sync_type = obj.sync_type,
                             primary_key = json.dumps(obj.to_sync(attributes = obj.sync_primary_keys)))
            session.add(sd)
        session.commit()

    def after_flood_delete(self, obj, manager, **info):
        if obj.sync_is_local:
            _internal.trigger_you_haves(manager, obj.sync_serial)
            
    def incoming_forward(self, obj, context, sender, manager, **info):
        assert obj in context.session
        if obj.sync_is_local:
            context.session.commit()

    def after_flood_forward(self, obj, manager, **info):
        if obj.sync_is_local:
            _internal.trigger_you_haves(manager, obj.sync_serial)
            
    def incoming_sync(self, object, manager, context, **info):
        # By this point the owner check has already been done
        session = context.session
        assert not object.sync_is_local
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
        self.you_have_task = None
        self.i_have_task = None
        self.send_you_have = set()
        self.received_i_have = set()

    @sqlalchemy.orm.reconstructor
    def reconstruct(self):
        self.protocol = None
        self.you_have_task = None
        self.connect_at = 0
        self.server_hostname = None
        self.i_have_task = None
        self.send_you_have = set()
        self.received_i_have = set()


    async def connected(self, manager, *args, **kwargs):
        self.you_have_task = None
        self.send_you_have = set()
        self.received_i_have = set()
        res = await super().connected(manager, *args, **kwargs)
        if hasattr(manager, 'session'):
            if not self in manager.session: manager.session.add(self)
            manager.session.commit()
            await _internal.handle_connected(self, manager, manager.session)
        return res





class SyncDeleted( _internal_base):
    __tablename__ = 'sync_deleted'
    id = Column( Integer, primary_key = True)
    sync_type = Column( String(128), nullable = False)
    primary_key = Column( TEXT, nullable = False)
    sync_owner_id = Column(GUID, ForeignKey('sync_owners.id'),
                           nullable = True)
    sync_serial = Column(Integer, nullable = False)
    _table_args = (Index("sync_deleted_serial_idx", sync_owner_id, sync_serial))

    sync_owner = sqlalchemy.orm.relationship('SyncOwner')

    @property
    def _obj(self):
        if hasattr(self, "_constructed_obj"): return self._constructed_obj
        if not self.registry: return None
        cls = self.registry.registry[self.sync_type]
        msg = json.loads(self.primary_key)
        msg['_sync_type'] =  self.sync_type
        msg['_sync_operation'] = 'delete'
        msg['sync_serial'] = self.sync_serial
        self._constructed_obj = cls.sync_receive(msg, context = self, operation = 'delete', sender = None)
        self._constructed_obj.sync_owner = self.sync_owner
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
        return Column(GUID, ForeignKey(SyncOwner.id, ondelete = 'cascade'), index = True)

    @sqlalchemy.ext.declarative.api.declared_attr
    def sync_owner(self):
        if hasattr(self, '__table__') and not 'sync_owner_id' in self.__table__.columns: return
        if hasattr(self,'__table__'): return sqlalchemy.orm.relationship(SyncOwner, foreign_keys = [self.__table__.c.sync_owner_id],
                                                                         primaryjoin = SyncOwner.id == self.__table__.c.sync_owner_id, lazy = 'joined')
        return sqlalchemy.orm.relationship(SyncOwner, foreign_keys = [self.sync_owner_id], lazy = 'joined')



    @classmethod
    def sync_construct(cls, msg, context, operation = None, manager = None, registry = None,
                        **info):
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
            obj = session.query(cls).get(primary_key_values)
            owner = SyncOwner.find_from_msg(session, sender, msg)
        context.owner = owner
        if obj is not None:
            for k in primary_keys: del msg[k]
            return obj
        obj =  super().sync_construct(msg,**info)
        obj.sync_owner = owner
        if session:
            assert owner is not None
            session.add(obj)
        return obj

class SyncOwner(_internal_base, SqlSynchronizable, metaclass = SqlSyncMeta):
    sync_registry = _internal.sql_meta_messages
    __tablename__ = "sync_owners"
    id = Column(GUID, primary_key = True,
                default = uuid.uuid4)
    destination_id = interface.no_sync_property(Column(Integer, ForeignKey(SqlSyncDestination.id, ondelete = 'cascade'),
                            index = True, nullable = True))
    destination = sqlalchemy.orm.relationship(SqlSyncDestination, lazy = 'subquery',
                                              backref = sqlalchemy.orm.backref('owners'),
    )
    incoming_serial = interface.no_sync_property(Column(Integer, default = 0, nullable = False))
    #outgoing_serial is managed but is transient
    incoming_epoch = interface.no_sync_property(Column(sqlalchemy.types.DateTime(True),
                          default = lambda: datetime.datetime.now(datetime.timezone.utc), nullable = False))
    outgoing_epoch = interface.no_sync_property(Column(sqlalchemy.types.DateTime(True),
                          default = lambda: datetime.datetime.now(datetime.timezone.utc), nullable = False))
    outgoing_serial = 0

    def __repr__(self):
        if self.destination is not None:
            dest_str = repr(self.destination)
        else: dest_str = "local"
        return "<SyncOwner id: {} {}>".format(
            str(self.id), dest_str)

            # All SyncOwners own themselves
    sync_owner_id = sqlalchemy.orm.synonym('id')
    @property
    def sync_owner(self): return self

    @classmethod
    def sync_construct(cls, msg, context, sender, **info):
        obj = None
        if hasattr(context, 'session'):
            obj = context.session.query(SyncOwner).get(msg['id'])
            if obj and (obj.destination  != sender):
                raise interface.SyncBadOwner("{} sent by {} but belongs to {}".format(
                    obj, sender, obj.destination))
            try: del msg['sync_owner_id']
            except KeyError: pass
        if not obj:
            obj = cls()
            try:
                context.session.add(obj)
                # We don't want an autoflush between the time we add the object and the time sync_serial is set.
                context.session.autoflush = False
                obj.destination = context.session.merge(sender)
            except AttributeError:
                obj.destination = sender

        return obj



    @classmethod
    def find_from_msg(self, session, dest, msg):
        if '_sync_owner' in msg: id = msg['_sync_owner']
        elif hasattr(dest, 'first_owner'): id = dest.first_owner
        else: raise interface.SyncBadOwner("Unable to find owner for message")
        try: return session.query(SyncOwner).get(id)
        except sqlalchemy.orm.exc.NoResultFound:
            raise interface.SyncBadOwner("{} not found is an owner".format(id)) from None

    def clear_all_objects(self, manager = None,
                              *, registries = None, session = None):
        if manager:
            session = manager.session
            registries = manager.registries
            assert session and registries
            logger.info("Deleting all objects from {}".format(self))
            session.rollback()
        for c in _internal.classes_in_registries(registries):
            if c is SyncOwner or issubclass(c, SyncOwner): continue
            for o in session.query(c).filter(c.sync_owner_id == self.id):
                o.sync_owner_id = None
                session.delete(o)
            session.flush()

    def sync_encode_value(self):
        return str(self.id)
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
        try: manager.session.add(d)
        except AttributeError: pass
        manager.add_destination(d)
        if force_resync:
            for o in session.query(SyncOwner).join(cls).filter(cls.cert_hash == d.cert_hash):
                o.incoming_epoch = datetime.datetime.now(datetime.timezone.utc)
    if hasattr(manager, 'session'):
        manager.session.commit()
    session.commit() #Might be diffgerent than manager.session; commit for force_resync
