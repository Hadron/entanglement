#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, contextlib, types


def default_encoder(obj, propname):
    "Default function used when encoding a sync_property; retrieves the property from an object"
    val = getattr(obj, propname, NotPresent)
    if hasattr(val,'sync_encode_value'): val = val.sync_encode_value()
    return val


class EphemeralUnflooded:

    "A sync_owner value indicating that an object is not to be flooded and when received shall be considered belonging to the sender.  Used for protocol messages like errors, Ihave and the like"

    #Not to be constructed; use the class
    def __init__(self): raise RunTimeError

    destination = NotImplemented
    @classmethod
    def __str__(self): return "EphemeralUnflooded"

    @classmethod
    def sync_encode_value(cls): return None
    

class SynchronizableMeta(type):
    '''A metaclass for capturing Synchronizable classes.  In python3.6, no metaclass will be needed; __init__subclass will be sufficient.'''

    def __init__(cls, name, bases, _dict):
        if cls.sync_registry:
            if not isinstance(cls.sync_registry, SyncRegistry):
                raise TypeError("Class {cls} sets sync_registry to something that is not a SyncRegistry".format(cls = cls.__name__))
            cls.sync_registry.register_syncable(cls.sync_type, cls)
        super().__init__( name, bases, _dict)

    def __new__(cls, name, bases, ns, **kwargs):
        if '_sync_construct' in ns:
            #Should warn in a future version
            ns['sync_construct'] = ns['_sync_construct']
            del ns['_sync_construct']
        if 'sync_registry' in ns:
            ns['_sync_registry'] = ns['sync_registry']
            del ns['sync_registry']
        sync_meta = {}
        for k,v in list(ns.items()):
            if isinstance(v, sync_property):
                sync_meta[k] = v
                v.declaring_class = name
                if v.wraps is not None:
                    ns[k] = v.wraps
                else: del ns[k]
                del v.wraps
                if not v.encoderfn: v.encoderfn = default_encoder
                if not v.decoderfn: v.decoderfn = lambda obj, propname, val: val
            elif isinstance(v, no_sync_property):
                if v.wraps is not None:
                    ns[k] = v.wraps
                else: del ns[k]
        ns['_sync_meta'] = sync_meta
        return type.__new__(cls, name, bases, ns, **kwargs)

    sync_registry = property(doc = "A registry of classes that this Syncable belongs to.  Registries can be associated with a connection; only classes in registries associated with a connection are permitted to be synchronized over that connection")

    @sync_registry.getter
    def sync_registry(inst):
        return getattr(inst,"_sync_registry", None)

    @sync_registry.setter
    def sync_registry(inst, value):
        inst._sync_registry = value



    @property
    def _sync_properties(cls):
        '''Returns a mapping of key in a sync representation to a sync property.  Unlike the _sync_meta key, this mapping combines entries from base classes.
        '''
        if '_sync_properties_cache' in cls.__dict__:
            return cls._sync_properties_cache
        d = {}
        for c in cls.__mro__:
            if hasattr(c,'_sync_meta'):
                for k,v in c._sync_meta.items():
                    if k not in d: d[k] = v
        cls._sync_properties_cache = types.MappingProxyType(d)
        return cls._sync_properties_cache

class sync_property:

    '''Represents a property that can be synchronized.
    Simplest usage:
        color = sync_property()

    Color will be read and written in the synchronization of the
    object using its default JSON representation

        manager =sync_property()
        @manager.encoder
        def manager(obj, propname):
            return obj.manager_id
        @manager.decoder
            def manager(obj, propname, value):
            return Manager.get_by_id(value)

    '''

    def __init__(self, wraps = None, doc = None, *,
                 encoder = None,
                 decoder = None,
                 constructor = False):
        self.wraps = wraps
        self.encoderfn =  encoder
        self.decoderfn = decoder
        self.constructor = constructor
        self.__doc__ = doc
        if wraps is not None and not doc:
            if hasattr(wraps, '__doc__'):
                self.__doc__ = wraps.__doc__

    def encoder(self, encoderfn):
        "If encoderfn(instance, propname) returns non-None, then the value returned will be encoded for this property"
        self.encoderfn = encoderfn
        return self

    def decoder(self, decoderfn):
        "If the property is specified, then decoderfn(obj, propname, value_from_json) will be called.  If it returns non-None, then setattr(obj, prop_name, return_value) will be called. If constructor is not False, obj may be None"
        self.decoderfn = decoderfn
        return self

class no_sync_property:

    '''Wraps a property that should not be synchronized.  Used mostly to
    wrap SQLAlchemy columns in SqlSynchronizable classes that would be
    synchronized by default.  Can also be used to mask a parent's
    sync_property for example when the value is always the same for
    some subclass.


    Examples:

    class Model(base):A
        id = Column(GUID, primary_key = True) # Would be a sync_property
        local_state no_sync_property(Column(String,)) # Not synchronized

    or:
    class Polygon(Synchronizable):

        sides = sync_property()

    class Triangle(Polygon):
        sides = no_sync_property(3)

    '''

    def __init__(self, wraps = None):
        self.wraps = wraps


class Synchronizable( metaclass = SynchronizableMeta):

    '''Represents a class that can be synchronized between two
    Entanglement SyncManagers.  Objects are synchronized by calling
    the synchronize method on a SyncManager.  Synchronizables have one
    or more sync_properties.  The sync_properties are packaged up into
    a serialized representation by the to_sync method and
    reconstituted into an object by the sync_construct, sync_receive
    and sync_receive_constructed methods.  Objects may have primary
    keys set in the sync_primary_keys attribute.  Objects with the
    same sync_primary_keys may be coalesced during transmission; only
    the latest synchronized version will be sent.

    '''
    
    def to_sync(self, attributes = None):

        '''Return a dictionary containing the attributes of self that should be synchronized.  Attributes can be passed in; if so, then the list of attributes will be limited to tohse passed in.'''
        d = {}
        if hasattr(self, 'sync_owner') and self.sync_owner is not None:
            d['_sync_owner'] = self.sync_owner.sync_encode_value()
        for k,v in self.__class__._sync_properties.items():
            if attributes and k not in attributes: continue
            try: val = v.encoderfn(self, k)
            except BaseException as e:
                raise ValueError("Failed encoding {} using encoder from class {}".format(k, v.declaring_class)) from e
            if val is not NotPresent: d[k] = val
        return d

    @classmethod
    def _sync_pkeys_dict(cls, msg):
        '''return a dictionary containing the decoded value of all of the primary keys in an incoming sync representation
        '''
        d = {}
        if not set(cls.sync_primary_keys).issubset(msg.keys()):
            raise SyncBadEncodingError("Encoding must contain primary keys: {}".format(cls.sync_primary_keys))
        for k in cls.sync_primary_keys:
            d[k] = cls._sync_properties[k].decoderfn(None, k, msg[k])
        return d

    @classmethod
    def sync_construct(cls, msg, **kwargs):
        '''Return a new object of cls consistent with msg that can be filled in with the rest of the contents of msg.  Renamed from the previously non-API _sync_construct.

        For many classes, this could simply call the class.  It could
        also be overridden to look up an existing instance of a class
        in a database.  The default implementation calls the
        constructor with sync properties where the constructor
        argument to the property is set.  If constructor is set to a
        number, that ordinal index is used.  If True, the property
        name is used as a constructor keyword.

        Subclasses such as SqlSynchronizable that associate
        Synchronizables with some persistence layer typically override
        this method and look up objects based on the primary key in
        the incoming message.

        '''
        cprops = dict(filter( lambda x: bool(x[1].constructor), cls._sync_properties.items()))
        maxord = 0
        for p in cprops.values():
            # sadly isinstance(True, int) is True
            #We assume any constructor value that is not True is an int
            if p.constructor is not True:maxord = max(maxord,p.constructor)
        args = [None] * maxord
        kwargs = {}
        for k,v in cprops.items():
            if k in msg:
                try:
                    if v.constructor is not True: #it's an int
                        args[v.constructor-1] = v.decoderfn(None, k, msg[k])
                    else: kwargs[k] = v.decoderfn(None, k, msg[k])
                    del msg[k]
                except Exception as e:
                    raise SyncBadEncodingError("Error decoding {}".format(k), msg = msg) from e
        return cls(*args, **kwargs)


    @classmethod
    def sync_receive(cls, msg, **kwargs):
        '''A convenience method for constructing an object from a json dictionary.  NOTE! Do not override this method: the SyncManager does not call it.  Instead override sync_construct and sync_receive_constructed.'''
        obj = cls.sync_construct(msg, **kwargs)
        assert obj.sync_should_listen_constructed(msg, **kwargs) is True
        return obj.sync_receive_constructed(msg, **kwargs)

    def sync_receive_constructed(self, msg, **kwargs):
        '''Given a constructed object, fill in the remaining fields from a javascript message'''
        cls = self.__class__
        for k, v in msg.items():
            if k not in cls._sync_properties:
                if k.startswith('_'): continue
                raise SyncBadEncodingError('{} unknown property in sync encoding'.format(k), msg = msg)
            try:
                setattr(self, k, cls._sync_properties[k].decoderfn(self, k, v))
            except Exception as e:
                raise SyncBadEncodingError("Failed to decode {}".format(k),
                                           msg = msg) from e
        return self

    sync_owner = EphemeralUnflooded

    @property
    def sync_is_local(self):
        return self.sync_owner is None or self.sync_owner.destination is None
    
    @classmethod
    def sync_should_listen(self, msg, **info):
        '''Return True if the incoming object should be received locally. Raise SynchronizationUnauthorized or some other exception if the incoming message should be ignored.'''
        return True

    def sync_should_listen_constructed(self, msg, **info):
        '''Return True if we should listen to this object else raise an exception.  Called after an object is constructed; several checks are easier after construction.  Checks that can be made without construct should be made there to avoid the security exposure of constructing objects.'''
        return True

    def sync_should_send(self, destination, **info):
        "Returns True if this object should be synchronized to the given destination"
        return True


    def sync_hash(self):
        '''Hash all the primary keys.  Any two instances that are sync_compatible must have the same sync_hash.'''
        if self.__class__.sync_primary_keys is Unique:
            return id(self)
        return sum(map(lambda x: getattr(self, x).__hash__(), self.__class__.sync_primary_keys))

    def sync_compatible(self, other):
        '''Return true if the primary keys of self match the primary keys of other; true if these two objects can be combined in synchronization'''
        if self.__class__.sync_primary_keys is Unique:
            return self is other
        if self.__class__ != other.__class__: return NotImplemented
        return all(map(lambda k: getattr(self,k).__eq__(getattr(other,k)), self.__class__.sync_primary_keys))

    class _sync_primary_keys:
        "A tuple of primary keys or the value entanglement.interface.Unique meaning that no instances of this class represent the same object"

        def __get__(self, obj, owner):
            raise NotImplementedError("sync_primary_keys must be set on Synchronizable classes")

    sync_primary_keys = _sync_primary_keys()
    del _sync_primary_keys


    class _Sync_type:
        "The type of object being synchronized"

        def __get__(self, instance, owner):
            return owner.__name__

    sync_type = _Sync_type()
    del _Sync_type

Unique = "Unique" #Constant indicating that a synchronizable is not combinable with any other instance
class NotPresent:

    def __repr__(self):
        return "NotPresent"

    def __str__(self):
        return "NotPresent"

NotPresent  = NotPresent()


class SyncRegistry:

    '''A registry of Synchronizable classes.  A connection may accept
    synchronization from one or more registries.  A Synchronizable typically
    belongs to one registry.  A registry can be thought of as a schema of
    related objects implementing some related synchronizable interface.

    A registry supports one or more operations on its Synchronizables.
    Most registries support the 'sync' operation, which requests the
    full attributes of an object to be flooded to the destinations.

    A SyncRegistry represents one of the common points for an
    application to override behavior or to be notified of incoming
    changes.  Overiding should_listen, should_listen_constructed and
    should_send is a good way to implement access control or
    filtering.  Overriding sync_receive or the incoming methods
    associated with an operation provide hooks for an application to
    be notified of incoming changes.  In general when methods are
    overridden, allowing the superclass method to run is important.

    '''

    def __init__(self):
        self.registry = {}
        self.operations = {}
        self.register_operation( 'sync', lambda obj, **kw: True)

    def associate_with_manager(self, manager):
        "Called by a manager when the registry is in the manager's list of registries.  Should not hold a non weak reference to the manager"
        pass

    def register_syncable(self, type_name, cls):
        "Called to add a Synchronizable to this registry"
        if type_name in self.registry:
            raise ValueError("`{} is already registered in this registry.".format(type_name))
        self.registry[type_name] = cls

    def register_operation(self, operation, op):
        "Add the <operation> operation to the set of operations this class accepts on input.  Either pass in a <handle_incoming> function passing the same arguments as sync_receive.  Note that self is not explicitly passed; pass in a bound method or use functools.partial if needed. or a SyncOperation instance"
        from . import operations
        if not isinstance(op, operations.SyncOperation):
            op = operations.MethodOperation(operation, op)
        self.operations[operation] = op

    def get_operation(self, operation):
        try: return self.operations[operation]
        except KeyError:
            raise SyncInvalidOperation("{} is not supported by this registry".format(operation)) from None

    def should_listen(self, msg, cls, **info):
        "Authorization check as well as a check on whether we want to ignore the class for some reason"
        return True

    def should_send(self, obj, destination, **info):
        return True

    def should_listen_constructed(self, obj, msg, **info):
        op = info.get('operation', self.get_operation('sync'))
        assert op.should_listen_constructed(obj, msg, **info) is True
        return True

    def sync_receive(self, object, operation, **kwargs):
        '''Responsible for registry-specific processing of received objects.
        Called after should_listen and should_listen_constructed have
        returned True, and after the class's sync_receive_constructed
        has filled in the class state.

        Typically the specific processing is dependent on the
        operation, so a method incoming_operation_name (for example
        incoming_sync) is called.  Overriding either sync_receive or
        incoming_operation_name for application-specific processing is
        a reasonable choice.


        Typically a registry represents a schema of related objects in
        some application domain.  Depending on the nature of a schema,
        the application logic may vary.  If the schema represents a
        database schema, then incoming_sync may commit the objects to
        a database while incoming_delete requests deletion.  If the
        schema represents graphical objects to be displayed in a GUI,
        incoming_sync may draw/redraw an object.  In such a case it
        would be better to have a draw method on all objects in the
        registry called by incoming_sync than to have the draw
        operation be triggered by the class's sync_receive method.
        The class's sync_receive would need to duplicate any
        registry-global logic before deciding to draw.

'''

        return operation.incoming(object, operation = operation, **kwargs)



    @contextlib.contextmanager
    def sync_context(self, **info):

        '''Create a context in which the sync_receive call can be run.
        Permits exceptions to be trapped and isolation of objects like
        SQL sessions.  At least for now, no need to call the
        superclass method when overriding.
        '''
        yield


error_registry = SyncRegistry()

class SyncError(RuntimeError, Synchronizable):

    args = sync_property()
    network_msg = sync_property()
    context = sync_property()
    @context.encoder
    def context(obj, propname):
        if obj.__context__ and not obj.__cause__: return str(obj.__context__)
        else: return default_encoder(obj, propname)

    cause = sync_property()
    @cause.encoder
    def cause(obj, propname):
        if obj.__cause__: return str(obj.__cause__)
        else: return default_encoder(obj, propname)

    sync_registry = error_registry
    sync_primary_keys = Unique

    def __init__(self, *args, network_msg = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.network_msg = network_msg

    def __str__(self):
        s = super().__str__()
        if self.network_msg is not None:
            s = s + "(in response to )"+str(self.network_msg)+")"
        try:
            if self.cause is not None:
                s = s + ":"+ self.cause
        except AttributeError: pass
        return s

    def to_sync(selff, **kwargs):
        d = super().to_sync(**kwargs)
        d['_sync_is_error'] = True
        return d

class SyncUnauthorized(SyncError): pass

class UnregisteredSyncClass(SyncError): pass

class WrongSyncDestination(SyncError):

    def __init__(msg = None, *args, dest = None, got_hash = None,
                 **kwargs):
        if not msg and dest:
            msg = "Incorrect certificate hash received from connection to {dest}".format(dest)
            if got_hash: msg = msg + " (got {got})".format(got = got_hash)
        super().__init__(msg, *args, **kwargs)

class SyncBadEncodingError(SyncError):



    def __init__(self, *args, msg = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.network_msg = msg

class SyncInvalidOperation(SyncError): pass

class SyncBadOwner(SyncError): pass

class SyncNotFound(SyncError): pass


class SyncNotConnected(SyncError):

    dest = sync_property( constructor = True)

    def __init__(self, msg = None, dest = None):
        if dest and not msg:
            msg = "Not currently connected to {}".format(dest)
            super().__init__( msg, dest)

from . import operations
error_registry.register_operation('error', operations.error_operation)
