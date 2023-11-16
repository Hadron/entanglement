#!/usr/bin/python3
# Copyright (C) 2017, 2018, 2019, 2020, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import contextlib, datetime, json, sqlalchemy, uuid, warnings
from datetime import timezone

from sqlalchemy import Column, Table, String, Integer, DateTime, ForeignKey, inspect, TEXT, Index, select
from sqlalchemy.orm import load_only
import sqlalchemy.exc
import sqlalchemy.orm, sqlalchemy.ext.declarative
from ..util import DestHash, SqlDestHash, get_or_create, GUID
from .. import interface, network, operations
from ..protocol import logger
from . import internal as _internal
from . import encoders as _encoders

warnings.filterwarnings("ignore", message = ".*normally configured.*", category = sqlalchemy.exc.SAWarning)

class SqlSyncSession(sqlalchemy.orm.Session):

    '''SqlSyncSession is an sqlalchemy.orm Session that supports
    entanglement operations.  Even if you are not using Entanglement,
    SqlSyncSession should be used with SqlSynchronizables because it
    has the logic to update the sync_serial columm.  Most
    functionality is only enabled when the manager property of the
    session is set to a SyncManager.  In that case, committing objects
    where sync_is_local returns True will synchronize those objects to
    all destinations of the associated manager.  If a manager is
    associated, it is an error to flush a non-local object.  The
    correct usage pattern is more like:

        session.sync_commit() #flush and send non-local objects
        session.commit() # commit local objects

    '''


    def __init__(self, *args, manager = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.manager = manager
        import traceback
        self._tb = traceback.extract_stack()
        self.sync_dirty = set()
        self.sync_deleted = set()
        sqlalchemy.event.listens_for(self, "before_flush")(self._handle_dirty)

        @sqlalchemy.event.listens_for(self, 'after_flush')
        def receive_after_flush(session, flush_context):
            # This is a bit of a mess.  When a new object is created,
            # we need to make sure that we have the SyncOwner
            # relation.  If we trigger in the before_commit event, we
            # trigger before the commit's flush so sync_dirty is not
            # populated (and nor would the relationships we're looking
            # for).  In the after_flush event, the object hasn't been
            # persisted in the session, so lazy loads and refreshes
            # don't work.  However, things seem to work if we get a
            # SyncOwner by hand with the right ID and associate it
            # with the object.
            for s in self.sync_dirty |self.sync_deleted:
                if isinstance(s, tuple): s = s[0]
                if s.sync_owner_id is not None and s.sync_owner is None:
                    s.sync_owner = self.query(SyncOwner).get(s.sync_owner_id)





        @sqlalchemy.event.listens_for(self, "after_commit")
        def receive_after_commit(session):
            try:
                if self.manager:
                    for x , attrs in self.sync_dirty: assert not self.is_modified(x)
                    self.sync_commit( expunge_nonlocal = False)
                else:
                    self.sync_dirty.clear()
                    self.sync_deleted.clear()
            except Exception:
                logger.exception("after_commit fails")
                raise

        @sqlalchemy.event.listens_for(self, 'after_rollback')
        def after_rollback(session):
            session.sync_dirty.clear()
            session.sync_deleted.clear()

    @staticmethod
    def _handle_dirty(session, internal = None, instances = None, expunge_nonlocal = False):
        serial_insert = Serial.__table__.insert().values(timestamp = datetime.datetime.now())
        serial_flushed = False
        owner_stmt = select([SyncOwner.id]) \
            .where(SyncOwner.dest_hash == None)
        local_owner = None
        for inst in session.new | session.dirty:
            if isinstance(inst, SqlSynchronizable) and session.is_modified(inst):
                if inst.sync_owner_id is None and not isinstance(inst, SyncOwner):
                    if local_owner is None:
                        local_owner = session.execute(owner_stmt).scalar()
                    if local_owner: inst.sync_owner_id = local_owner
                inspect_inst = inspect(inst)
                modified_attrs = frozenset(inst.__class__._sync_properties.keys()) - frozenset(inspect_inst.unmodified)
                session.sync_dirty.add((inst, modified_attrs))
                #By this point sync_owner_id can only be None if there
                #are no local SyncOwners. That's probably not going to
                #end up so good if the object is received by an
                #SqlSynchronizable attached to an SQLSyncRegistry, but
                #we don't actually catch that here.
                if (inst.sync_owner_id is None and inst.sync_owner is None) or inst.sync_is_local:
                    if not serial_flushed:
                        r = session.execute(serial_insert)
                        new_serial = r.inserted_primary_key[0]
                        serial_flushed = True
                    inst.sync_serial = new_serial
                else:
                    if inst.sync_owner: inst.sync_owner.dest_hash   # get while we can
                    if expunge_nonlocal:
                        session.expunge(inst)
                    elif session.manager:
                        logger.error('Tried to commit non-local object: {}'.format(inst))
                        raise NotImplementedError('The semantics of a non-local commit of nonlocal objects are undefined and unimplemented')

        for inst in session.deleted:
            if isinstance( inst, SqlSynchronizable):
                if inst in session.sync_deleted: continue
                session.sync_deleted.add(inst)
                if inst.sync_owner: inst.sync_owner.dest_hash   # get while we can
                if (inst.sync_owner_id is None and inst.sync_owner is None) or inst.sync_is_local:
                    if not serial_flushed:
                        r = session.execute(serial_insert)
                        new_serial = r.inserted_primary_key[0]
                        serial_flushed = True
                    inst.sync_serial = new_serial
                    deleted = SyncDeleted()
                    deleted.sync_serial = new_serial
                    deleted.sync_type = inst.sync_type
                    deleted.sync_owner = inst.sync_owner
                    deleted.primary_key = json.dumps(inst.to_sync(attributes = inst.sync_primary_keys))
                    session.add(deleted)
                else:
                    if expunge_nonlocal:
                        session.expunge(inst)
                    elif session.manager:
                        logger.error('Tried to commit deletion of non-local object: {}'.format(inst))
                        raise NotImplementedError('Semantics of committing deletion of non-local objects is undefined and unimplemented')


    def sync_commit(self, expunge_nonlocal = True, *,
                    update_responses = True):
        '''For any SqlSynchronizable modified in the session, synchronize the
        object.  If the object is sync_is_local, send a 'sync'
        operation to all destinations of the manager.  If the object
        is non-local, send a 'forward' object toward the destination
        of the object owner, requesting that they update the object.
        Deleted objects cause a 'delete' operation.  If they are
        local, the delete is sent to all destinations, otherwise it is
        a request to the owner.  If 'update_responses' is True, then
        non-local objects have a 'sync_future' set on them.  This
        future will receive either an error or the updated object when
        the owner respons to the operation.  If 'expunge_nonlocal' is
        True, then nonlocal objects are expunged from the session so
        that a later flush/commit call will not affect them.

        '''

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
                if (o.sync_owner_id or o.sync_owner) and not o.sync_is_local: #an update The
                    # manager may have a subclass of
                    # SqlSyncDestination that is loaded and almost
                    # certainly has a different session, so we want
                    # their destination object.
                    forward_dest = self.manager.dest_by_hash(o.sync_owner.dest_hash)
                    if not forward_dest:
                        logger.error("Requested forward to owner of {}, but manager doesn't recognize {} as a destination".format(
                            o, o.sync_owner.dest_hash))
                        continue

                # We want to be manipulating fully refreshed detached
                # instances.  We want fully refreshed in case
                # coalescing causes us to touch other attributes
                # besides what this commit updates.  However we want
                # detached instances that do not share with other
                # objects in the system so that later updates that are
                # not committed do not touch our objects.  The best
                # way to accomplish this is to merge into a new
                # session (forcing a refresh).  That avoids refreshing
                # the input object unintentionally, but also works
                # around the fact that you cannot cause any SQL in an
                # after_commit hook.  However merging into a new
                # session complicates response handling.  We need to
                # allocate the future here and attach it to the old
                # and new objects.
                future = None
                if update_responses and forward_dest:
                    future = self.manager.loop.create_future()
                    o.sync_future = future
                o_new = s_new.merge(o)
                if o_new.sync_owner: o_new.sync_owner.dest_hash
                # Now preserve any non-sqlalchemy attributes
                ins_new = inspect(o_new)
                attributes = set(o.__class__._sync_properties.keys())- set(ins_new.attrs.keys())
                # Long term we are likely to need a mechanism to handle other attributes that should be excluded
                try: attributes.remove('_sync_owner')
                except KeyError: pass
                if attributes:
                    sd = o.to_sync(
                        attributes = attributes)
                    try: del sd['_sync_owner']
                    except KeyError: pass
                    o_new.sync_receive_constructed(sd, operation = 'sync')
                o = o_new
                sqlalchemy.orm.session.make_transient(o)
                sqlalchemy.orm.session.make_transient_to_detached(o)
                o.sync_future = future #new object after merge
                sync_commit_hook = getattr(o, '_sync_commit_hook', None)
                if sync_commit_hook:
                    try: sync_commit_hook(self)
                    except Exception:
                        logger.exception( f'Calling sync_commit hook for {o}')
                        
                if forward_dest:
                    forward_objects.append((o, forward_dest, modified_attrs))
                else: objects.append(o)
            for o in self.sync_deleted:
                if o.sync_is_local:
                    deleted_objects.append((o, None))
                else:
                    delete_dest = self.manager.dest_by_hash(o.sync_owner.dest_hash)
                    if not delete_dest:
                        logger.error("Requested delete to owner of {}, but manager doesn't recognize {} as a destination".format(
                            o, o.sync_owner.dest_hash))
                        continue
                    deleted_objects.append((o, [delete_dest]))

            self._do_sync( update_responses, objects, deleted_objects, forward_objects)
        self.sync_dirty.clear()
        self.sync_deleted.clear()

    def _do_sync(self, update_responses, dirty_objects, deleted_objects, forward_objects):
        #This is called in the event loop and thus in the thread of the manager
        if dirty_objects or deleted_objects:
            del_objs = [x[0] for x in deleted_objects]
            serial = max(map( lambda x: x.sync_serial if x.sync_is_local else 0, dirty_objects + del_objs))
            for o in dirty_objects:
                self.manager.synchronize(o)
            for o, dest in deleted_objects:
                future = self.manager.synchronize(o, operation = 'delete',
                                     attributes_to_sync  = (set(o.sync_primary_keys)
                                                            | {'sync_serial'}),
                                         destinations = dest,
                                         response = update_responses if dest else False)
                if future is not None: o.sync_future = future
            self.manager.loop.call_soon_threadsafe(_internal.trigger_you_haves, self.manager, serial)
        for o, dest, attrs in forward_objects:
            self.manager.synchronize(o, operation = 'forward',
                                     destinations = [dest],
                                     attributes_to_sync = attrs |  set(o.sync_primary_keys),
                                     response = update_responses and o.sync_future)



def sync_session_maker(*args, **kwargs):
    "Like sql.orm.sessionmaker for SqlSyncSessions"
    return sqlalchemy.orm.sessionmaker(class_ = SqlSyncSession, *args, **kwargs)

class SqlSyncMeta(interface.SynchronizableMeta, sqlalchemy.ext.declarative.DeclarativeMeta):

    def __new__(cls, name, bases, ns):
        registry = None
        for c in bases:
            registry = getattr(c,'registry', None)
            if registry is not None: break
        if registry and not 'sync_registry' in ns: ns['sync_registry'] = registry
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
        self.register_operation('create', operations.create_operation)
        if sessionmaker is None:
            sessionmaker = sync_session_maker()
        if bind is not None: sessionmaker.configure(bind = bind)
        self.sessionmaker = sessionmaker

    @classmethod
    def create_bookkeeping(self, bind):
        "Should be called at least once per engine typically before metadata.create_all is called on any sql_sync_declarative_base using the engine."
        from .upgrader import upgrade_database
        upgrade_database(bind, _internal_base.metadata,
                         'entanglement.sql:alembic',
                         'entanglement_version',
                         'sync_serial',
                         "5370406b7505")

    def ensure_session(self, manager):
        if not hasattr(manager, 'session'):
            manager.session = self.sessionmaker()
            if not manager.session.is_active: manager.session.rollback()

    def associate_with_manager(self, manager):
        self.ensure_session(manager)

    @contextlib.contextmanager
    def sync_context(self, **info):
        "Return a context used to receive an incoming object.  This context follows the context manager protocol.  The context will have an attribute 'session' that is an SqlSyncSession into which an object can be constructed"

        session = self.sessionmaker(expire_on_commit = False)
        class Context: pass
        ctx = Context()
        ctx.session = session
        try: yield ctx
        finally:
            session.close()
            session.expunge_all()

    def incoming_delete( self, obj, context, manager, sender, **info):
        session = context.session
        ins = inspect(obj)
        if not ins.persistent: raise interface.SyncNotFound("Deleted an already deleted object")
        assert obj in session
        if obj.sync_is_local or obj.sync_owner.dest_hash == sender.dest_hash:
            session.delete(obj)
        if obj.sync_owner and obj.sync_owner.dest_hash == sender.dest_hash:
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

    def incoming_forward(self, obj, context, sender, manager, operation, **info):
        assert obj in context.session
        if obj.sync_is_local:
            if operation == 'forward' and obj in context.session.new: raise interface.SyncNotFound()
            try:
                context.session.commit()
                obj.sync_owner
            except sqlalchemy.exc.StatementError as e:
                raise SqlSyncError("Failed to update {}".format(obj.sync_type)) from e

    incoming_create = incoming_forward

    def after_flood_forward(self, obj, manager, **info):
        if obj.sync_is_local:
            _internal.trigger_you_haves(manager, obj.sync_serial)

    after_flood_create = after_flood_forward
    def incoming_sync(self, object, manager, context, **info):
        # By this point the owner check has already been done
        session = context.session
        assert not object.sync_is_local
        assert object in session
        session.commit()
        object.sync_owner



_internal_base = sqlalchemy.ext.declarative.declarative_base()

class Serial(_internal_base):
    "Keep track of serial numbers.  A sequence would be better, but sqlite can't do them"

    __tablename__ = 'sync_serial'
    serial = Column(Integer, primary_key = True)
    timestamp = Column(DateTime, default = datetime.datetime.now)

class  SqlSyncDestination(_internal_base, network.SyncDestination):
    __tablename__ = "sync_destinations"
    id = Column(Integer, primary_key = True)
    dest_hash = Column(SqlDestHash, unique = True, nullable = False)
    name = Column(String(64), nullable = False)
    host = Column(String(128))

    bw_per_sec = Column(Integer, default = 10000000,
                        nullable = False)
    type = Column(String, nullable = False)

    __mapper_args__ = {
        'polymorphic_on': 'type',
        'polymorphic_identity': 'SqlSyncDestination',
        'with_polymorphic': '*',
    }

    def __hash__(self):
        return hash(self.dest_hash)

    def __eq__(self, other):
        if isinstance(other,SqlSyncDestination):
            return other.dest_hash == self.dest_hash
        else: return super().__eq__(other)

    def __init__(self, *args, **kwargs):
        network.SyncDestination.__init__(self, *args, **kwargs)
        self.you_have_task = None
        self.i_have_task = None
        self.send_you_have = set()
        self.received_i_have = set()
        self.attach_callbacks()

    @sqlalchemy.orm.reconstructor
    def reconstruct(self):
        self.protocol = None
        self.you_have_task = None
        self.connect_at = 0
        self.server_hostname = None
        self.i_have_task = None
        self.send_you_have = set()
        self.received_i_have = set()
        self._on_connected_cbs = []
        self._on_connection_lost_cbs = []
        self.attach_callbacks()

    def attach_callbacks(self):
        '''

        This method is guaranteed to be called whenever a `SqlSyncDestination` is instantiated.  It is intended for subclasses to override and to attach any callbacks they wish to attach.  Note that overriding :meth:`__init__` is insufficient because SQLAlchemy mapped classes do not call __init__ when loading from the database.

        '''
        pass
    

    def clear_all_objects(self, manager=None, *, registries=None, session=None):
        if manager and session is None:
            session = manager.session
        if manager:
            registries = manager.registries
            assert session and registries
            assert getattr(session, 'manager', None) is None
            logger.info("Deleting all objects from {}".format(self))
            session.rollback()
        q = session.query(SyncOwner).filter_by(dest_hash=self.dest_hash)
        for o in list(q.all()):
            o.clear_all_objects(manager=manager, registries=registries, session=session)
            if manager:
                dest = manager.dest_by_hash(o.dest_hash)
                if dest: exclude = [dest]
                else: exclude = []
                manager.synchronize(o, operation='delete',  exclude = exclude)
            session.delete(o)
            session.commit()

    async def connected(self, manager, *args, **kwargs):
        self.you_have_task = None
        self.send_you_have = set()
        self.received_i_have = set()
        res = await super().connected(manager, *args, **kwargs)
        if hasattr(manager, 'session'):
            manager.session.commit()
            await _internal.handle_connected(self, manager, manager.session)
        return res





class SyncDeleted( _internal_base):
    __tablename__ = 'sync_deleted'
    id = Column( Integer, primary_key = True)
    sync_type = Column( String(128), nullable = False)
    primary_key = Column( TEXT, nullable = False)
    sync_owner_id = Column(GUID, ForeignKey('sync_owners.id', ondelete = 'cascade'),
                           nullable = True)
    sync_serial = Column(Integer, nullable = False)
    _table_args = (Index("sync_deleted_serial_idx", sync_owner_id, sync_serial))

    sync_owner = sqlalchemy.orm.relationship('SyncOwner', passive_deletes = 'all' )

    @property
    def _obj(self):
        if hasattr(self, "_constructed_obj"): return self._constructed_obj
        if not self.registry: return None
        cls = self.registry.registry[self.sync_type]
        msg = json.loads(self.primary_key)
        msg['_sync_type'] =  self.sync_type
        msg['_sync_operation'] = 'delete'
        msg['sync_serial'] = self.sync_serial
        del msg['_sync_owner']
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
                 'sync_hash', 'sync_compatible', 'sync_priority'):
        locals()[meth] = proxyfn(meth)
    del proxyfn



class SqlSynchronizable(interface.Synchronizable):

    '''
    A SQLAlchemy mapped class that can be synchronized.  By default
    every column becomes a sync_property.  You can explicitly set
    sync_property around a Column if you need to override the encoder
    or decoder, although see entanglement.sql.encoder for a
    type map that can be used in most cases.  The sync_should_send
    method on this class and should_send on any containing registries
    must be able to make a decision about whether to send a deleted
    object when presented with an object containing only the primary
    keys and no relationships.

    '''

    @sqlalchemy.ext.declarative.declared_attr
    def    sync_serial(self):
        if hasattr(self,'__table__'): return
        return    Column(Integer, nullable=False, index = True)

    sync_serial = interface.sync_property(wraps = sync_serial)

    @sqlalchemy.ext.declarative.declared_attr
    def sync_owner_id(self):
        if hasattr(self, '__table__'): return
        return Column(GUID, ForeignKey(SyncOwner.id, ondelete = 'cascade'), index = True)

    @property
    def _sync_owner(self):
        return self.sync_owner and self.sync_owner.id

    @_sync_owner.setter
    def _sync_owner(self, val):
        if val != self._sync_owner:
            raise ValueError('Cannot change sync_owner this way')
        
    _sync_owner:GUID = interface.sync_property(_sync_owner)
    
    @sqlalchemy.ext.declarative.declared_attr
    def sync_owner(self):
        if hasattr(self, '__table__') and not 'sync_owner_id' in self.__table__.columns: return
        if hasattr(self,'__table__'): return sqlalchemy.orm.relationship(SyncOwner, foreign_keys = [self.__table__.c.sync_owner_id],
                                                                         primaryjoin = SyncOwner.id == self.__table__.c.sync_owner_id, lazy = 'joined')
        return sqlalchemy.orm.relationship(SyncOwner, foreign_keys = [self.sync_owner_id], lazy = 'joined', passive_deletes = True, cascade='save-update')

    sync_priority = _internal.SyncPriorityProperty()


    @classmethod
    def sync_construct(cls, msg, context, operation = None, manager = None, registry = None,
                        **info):
        if manager and registry: registry.ensure_session(manager)
        session = None
        obj = None
        owner = None
        if hasattr(context,'session'):
            primary_keys = tuple(x.name for x in inspect(cls).primary_key)
            try: primary_key_values = tuple(msg[k] for k in primary_keys)
            except KeyError as e:
                if (not operation) or operation.primary_keys_required:
                    raise interface.SyncBadEncodingError("All primary keys must be present in the encoding", msg = msg) from e
                else: primary_key_values = None
            sender = info['sender']
            session = context.session
            if primary_key_values:
                obj = session.query(cls).get(primary_key_values)
            owner = SyncOwner.find_from_msg(session, sender, msg)
            if owner and owner.dest_hash == sender.dest_hash: owner.destination = sender
        context.owner = owner
        if obj is not None:
            for k in primary_keys: del msg[k]
            return obj
        obj =  super().sync_construct(msg,**info)
        obj.sync_owner = owner
        if session:
            assert owner is not None
            assert obj.sync_owner is owner
            session.add(obj)
        return obj

    def sync_create(self, manager, owner):
        "Send a create operation to a given owner for this object.  Returns a future whose result will either be the object synchronized by the owner or an error."
        self.sync_owner = owner
        attrs = frozenset(self.__class__._sync_properties.keys()) - inspect(self).unmodified
        return manager.synchronize(self,
                            operation = 'create',
                            attributes_to_sync = attrs,
                            destinations = [manager.dest_by_hash(owner.dest_hash)],
                            response = True)


class SyncOwner(_internal_base, SqlSynchronizable, metaclass = SqlSyncMeta):
    sync_registry = _internal.sql_meta_messages
    __tablename__ = "sync_owners"
    id = Column(GUID, primary_key = True,
                default = uuid.uuid4)
    # We want dest_hash to be a no_sync_property, but need to set up the relationship with the Column object
    dest_hash = Column(SqlDestHash, nullable= True, index = True)
    sql_destination = sqlalchemy.orm.relationship(SqlSyncDestination,
                                              primaryjoin = (dest_hash == SqlSyncDestination.dest_hash),
                                              innerjoin = True,
                                                  passive_deletes = 'all',
                                              uselist = False,
                                              foreign_keys = [dest_hash],
                                              remote_side = [SqlSyncDestination.dest_hash],
backref = sqlalchemy.orm.backref('owners',
                                 lazy = 'joined',
                                 passive_deletes = 'all',
                                 uselist = True),
    )
    dest_hash = interface.no_sync_property(dest_hash)

    type = Column(String, nullable = False)
    incoming_serial = interface.no_sync_property(Column(Integer, default = 0, nullable = False))
    #outgoing_serial is managed but is transient
    incoming_epoch = interface.no_sync_property(Column(sqlalchemy.types.DateTime(True),
                          default = lambda: datetime.datetime.now(datetime.timezone.utc), nullable = False))
    outgoing_epoch = interface.no_sync_property(Column(sqlalchemy.types.DateTime(True),
                          default = lambda: datetime.datetime.now(datetime.timezone.utc), nullable = False))
    outgoing_serial = 0
    __mapper_args__ = {
        'polymorphic_on': type,
        'polymorphic_identity': 'SyncOwner',
        'with_polymorphic': '*'
    }

    #We need to treat epoch differently for incoming and outgoing
    #synchronizations.  We don't want to actually keep epoch entirely
    #synchronized, because for example if we change which upstream we
    #get a given SyncOwner from, we want to ask all our downstreams to
    #deleate objects and resynchronize, even though the upstream may
    #not wish to do that.  Similarly we might wish to change the epoch
    #to force a downstream resync if we suspect inconsistency.  There
    #is an epoch sync property.  On write (that is in processing
    #sync_receive_constructed) this updates incoming_epoch (the epoch
    #we expect from upstream).  On read, this reads outgoing_epoch.

    @property
    def epoch(self):
        return self.outgoing_epoch

    @epoch.setter
    def epoch(self, v):
        self.incoming_epoch = v

    epoch = interface.sync_property(epoch,
                          encoder = _encoders.datetime_encoder,
                          decoder = _encoders.datetime_decoder)

    def sync_receive_constructed(self, msg, **options):
        operation = options.get('operation', 'sync')
        try:
            old_epoch = self.incoming_epoch.replace(tzinfo = datetime.timezone.utc)
        except AttributeError: old_epoch = None
        super().sync_receive_constructed(msg, **options)
        if operation == 'sync':
            if old_epoch != self.incoming_epoch:
                self.incoming_serial_number = 0
                # Actually clearing objects in sync_receive_constructed seems dangerous; the incoming function from the registry could raise an error.
                # So instead we have an interface shared with the registry
                self.request_clear = True
            
    def __repr__(self):
        try:
            destination = getattr(self,'destination',getattr(self,'sql_destination'))
            if destination is not None:
                dest_str = repr(destination)
            elif self.dest_hash:
                dest_str = "to {}".format(self.dest_hash)
            else: dest_str = "local"
        except:
            try:
                if self.dest_hash:
                    dest_str = "to {}".format(self.dest_hash)
                else: dest_str = "local"
            except:
                # This typically happens if our dest_hash attribute is expired and we cannot refresh it.
                dest_str = "unknown destination"
        return "<SyncOwner id: {} {}>".format(
            str(self.id), dest_str)

            # All SyncOwners own themselves
    sync_owner_id = sqlalchemy.orm.synonym('id')
    @property
    def sync_owner(self): return self

    @property
    def sync_is_local(self):
        return self.dest_hash is None

    @classmethod
    def sync_construct(cls, msg, context, sender, **info):
        obj = None
        if hasattr(context, 'session'):
            obj = context.session.query(SyncOwner).get(msg['id'])
            if obj and (obj.dest_hash  != sender.dest_hash):
                raise interface.SyncBadOwner("{} sent by {} but belongs to {}".format(
                    obj, sender, obj.dest_hash))
            try: del msg['sync_owner_id']
            except KeyError: pass
        try:
            del msg['_sync_owner']
        except KeyError: pass
        if not obj:
            obj = cls()
            try:
                context.session.add(obj)
                # We don't want an autoflush between the time we add the object and the time sync_serial is set.
                context.session.autoflush = False
                obj.dest_hash = sender.dest_hash
            except AttributeError:
                obj.dest_hash = sender.dest_hash

        return obj



    @classmethod
    def find_from_msg(self, session, dest, msg):
        if '_sync_owner' in msg: id = msg['_sync_owner']
        else: raise interface.SyncBadOwner("Unable to find owner for message")
        try: return session.query(SyncOwner).get(id)
        except sqlalchemy.orm.exc.NoResultFound:
            raise interface.SyncBadOwner("{} not found is an owner".format(id)) from None

    def clear_all_objects(self, manager = None,
                              *, registries = None, session = None):
        if manager and session is None:
            session = manager.session
        if manager:
            registries = manager.registries
            assert session and registries
            assert getattr(session, 'manager', None) is None
            logger.info("Deleting all objects from {}".format(self))
            session.rollback()
        for c, r  in reversed(list(_internal.classes_in_registries(registries))):
            if c is SyncOwner or issubclass(c, SyncOwner): continue
            for o in session.query(c).filter(c.sync_owner_id == self.id):
                o.sync_owner_id = None
                session.delete(o)
                session.flush()

    def sync_encode_value(self):
        return str(self.id)

class SqlSyncError(interface.SyncError): pass

def sql_sync_declarative_base(*args, registry = None,
                              registry_class = SqlSyncRegistry,
                              cls = None,
                              **kwargs):
    if cls:
        class DeclarativeBase(SqlSynchronizable, cls): pass
        cls = DeclarativeBase
    else: cls = SqlSynchronizable
    base =  sqlalchemy.ext.declarative.declarative_base(cls = cls, metaclass = SqlSyncMeta, *args, **kwargs)
    base.registry = registry or registry_class()
    return base

migration_naming_convention = {
    'fk' : '%(table_name)s_%(column_0_name)s_fkey',
    'ix' : 'ix_%(column_0_label)s'
}

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
            for o in session.query(SyncOwner).join(cls.owners).filter(cls.dest_hash == d.dest_hash):
                o.incoming_epoch = datetime.datetime.now(datetime.timezone.utc)
    if hasattr(manager, 'session'):
        manager.session.commit()
    session.commit() #Might be diffgerent than manager.session; commit for force_resync
