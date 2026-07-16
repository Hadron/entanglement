#!/usr/bin/python3
# Copyright (C) 2026, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

'''Pydantic integration for entanglement.

This module provides SynchronizableBaseModel, a class that inherits from both
pydantic.BaseModel and entanglement.Synchronizable, allowing Pydantic models
to be synchronized over Entanglement.

Pydantic is an optional dependency. If not installed, importing this module
will raise a helpful ImportError.
'''

from __future__ import annotations

import typing
import types
import weakref
from typing import Optional, get_type_hints
import uuid
# Import pydantic - let ImportError propagate if not available
from pydantic import BaseModel, Field, ConfigDict, model_validator, model_serializer, PrivateAttr
from pydantic.fields import FieldInfo
from typing import ClassVar

from . import memory
from .memory  import SyncStoreRegistry

# ModelMetaclass is the metaclass of BaseModel
ModelMetaclass = type(BaseModel)

from .interface import Synchronizable, SynchronizableMeta, sync_property, no_sync_property, NotPresent, SyncBadEncodingError, SyncRegistry, EphemeralUnflooded
from .util import get_annotations

# BaseModel is imported from pydantic above - it will raise ImportError if not available

# Module-level cache for optional models (to avoid Pydantic treating it as a field)
_optional_model_cache: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


class SynchronizableModelMeta(SynchronizableMeta, ModelMetaclass):
    '''Metaclass combining SynchronizableMeta and Pydantic's ModelMetaclass.
    
    This metaclass merges the logic from both parent metaclasses. The key
    responsibility is auto-promotion: any annotation that is not already a
    sync_property or no_sync_property, and whose value (if any) is not a
    no_sync_property, should be wrapped in a bare sync_property().
    '''

    def __new__(cls, name, bases, ns, **kwargs):
        # Collect annotations before we start modifying ns
        annotations = get_annotations(ns)
        # Explicitly set __annotations__ to ensure Python 3.14's annotation algorithm finds our changes.
        # The __annotations__ descriptor prefers annotations in the class dict to calling __annotate__.  
        ns['__annotations__'] = annotations
        
        # Handle sync_registry specially - Pydantic treats _ prefixed attributes as private
        # If sync_registry is set, we need to also add _sync_registry with ClassVar annotation
        # so Pydantic treats it as a class variable instead of a private attribute
        if 'sync_registry' in ns:
            ns['__annotations__']['_sync_registry'] = typing.ClassVar[SyncRegistry]
        
        # Mark _sync_meta as ClassVar so Pydantic doesn't treat it as a private attribute
        # and will properly preserve it instead of clearing it
        ns['__annotations__']['_sync_meta'] = typing.ClassVar[types.MappingProxyType]
        
        # Auto-promote plain annotations.
        # Any annotation not already wrapped in sync_property or no_sync_property
        # should be wrapped in a bare sync_property() so SynchronizableMeta picks it up.
        for k, annotation in annotations.items():
            if k.startswith('_'):
                continue
            if hasattr(annotation, '__origin__') and annotation.__origin__ is typing.ClassVar:
                continue
            if isinstance(ns.get(k), (sync_property, no_sync_property)):
                continue
            # If the annotation has no entry in ns, use bare sync_property()
            # Otherwise wrap the existing value (e.g., FieldInfo)
            ns[k] = sync_property(ns[k]) if k in ns else sync_property()

        # Now delegate to super().__new__() which will chain through the MRO
        return super().__new__(cls, name, bases, ns, **kwargs)

    def __init__(cls, name, bases, ns, **kwargs):
        # Call super().__init__() which will chain through the MRO
        super().__init__(name, bases, ns)
        
        # Validate that sync_primary_keys is set (but skip the base class)
        if name == 'SynchronizableBaseModel':
            # The base class doesn't need sync_primary_keys
            return

        
        # Check if sync_primary_keys is available (defined on this class or inherited)
        try:
            cls.sync_primary_keys
        except NotImplementedError:
            raise TypeError(
                f"Class {name} must set sync_primary_keys. "
                "Example: sync_primary_keys = ('id',)"
            ) from None


class SynchronizableBaseModel(Synchronizable, BaseModel, metaclass=SynchronizableModelMeta):
    '''Base class for Pydantic models that can be synchronized via Entanglement.

    This class combines pydantic.BaseModel with entanglement.Synchronizable,
    allowing Pydantic models to be synchronized over Entanglement.

    Note: This is a base class and should not be used directly. Subclasses
    must set sync_primary_keys to specify which fields are primary keys.

    **Constraints:**

    - No Pydantic aliases on sync'd fields. `to_sync` and `sync_receive_constructed`
      use the Python attribute name as the wire key.
    - No `sync_property` encoders/decoders on SynchronizableBaseModel subclasses.
      Use Pydantic field serializers instead.
    - Cross-field `@model_validator(mode='after')` will run on partial state during
      `sync_receive_constructed`. Subclass authors must not rely on these for
      invariants that must hold on partial updates.

    **Example:**

        class MyModel(SynchronizableBaseModel):
            sync_registry = my_registry

            id: str = Field(primary_key=True)
            color: str = Field(default="red")

            sync_primary_keys = ('id',)

            # Pydantic validators work normally
        @field_validator('color')
            def validate_color(cls, v):
                if v not in ('red', 'blue', 'green'):
                    raise ValueError('Invalid color')
                return v
    '''

    model_config = ConfigDict(
        validate_assignment=False,  # Default; can be overridden by subclasses
    )

    sync_store_with: ClassVar[Optional[type]] = None
    sync_owner_id: typing.Optional[uuid.UUID] = Field(exclude=True, default=None)
    sync_owner_object: typing.Any = Field(exclude=True, default=Synchronizable.sync_owner)

    @property
    def sync_owner(self):
        return self.sync_owner_object

    @sync_owner.setter
    def sync_owner(self, val):
        self.sync_owner_object = val
        return val

    @property
    def _sync_owner(self):
        return self.sync_owner_id

    @_sync_owner.setter
    def _sync_owner(self, val):
        self.sync_owner_id = val
        return val

            
    def __init__(self, **data):
        # Call BaseModel's __init__ which will handle Field validation
        BaseModel.__init__(self, **data)
        # Synchronizable doesn't need its own __init__ but we ensure
        # the instance is properly initialized
        Synchronizable.__init__(self)

    @classmethod
    def sync_construct(cls, msg, **kwargs):
        '''Called only for new objects (sync and create operations).

        Implementation:
        1. Extract primary key values from msg using _sync_pkeys_dict.
        2. Call model_validate(msg) on the real (non-optional) model class.
        3. Return the validated instance.

        Note: msg keys for constructor-argument properties are NOT deleted here,
        unlike the base Synchronizable.sync_construct. Since model_validate
        handles all fields at once, and sync_receive_constructed will setattr
        from a validated temp object (not from msg directly), double-processing
        is harmless.
        '''
        # Validate the full message against the model
        instance = cls.model_validate(msg)
        return instance

    def sync_receive_constructed(self, msg, **kwargs):
        '''Given a constructed object, fill in the remaining fields from a message.

        Implementation:
        1. Record incoming_keys = set(msg.keys()) - keys_starting_with_underscore.
        2. Get (or build) the optional model via _get_optional_model(cls).
        3. Call optional_model.model_validate(msg) to produce a validated temp object.
        4. For each key in incoming_keys that is in cls._sync_properties:
           setattr(self, key, getattr(temp_obj, key))
        5. Return self.

        This approach:
        - Runs Pydantic validators on received values.
        - Transfers Python-typed values (post-validation), not raw wire values.
        - Does NOT run a final whole-object model_validate; cross-field
          @model_validator(mode='after') may see partial state. This is
          accepted risk; document it.
        '''
        # Skip underscore-prefixed keys (internal entanglement keys)
        incoming_keys = set()
        for k in msg.keys():
            if not k.startswith('_'):
                incoming_keys.add(k)

        # Get optional model
        optional_model = _get_optional_model(self.__class__)

        # Validate against optional model to get a temp object
        temp_obj = optional_model.model_validate(msg)

        # Transfer values for sync'd fields only
        for key in incoming_keys:
            if key in self.__class__._sync_properties:
                setattr(self, key, getattr(temp_obj, key))

        return self

    def to_sync(self, attributes=None):
        '''Return a dictionary containing the attributes of self that should be synchronized.

        Delegate to model_dump() for serialization, but only for fields that
        are in _sync_properties. Pydantic's @field_serializer decorators and
        type coercion are used for encoding. sync_property.encoderfn is ignored.
        '''
        dumped = self.model_dump()
        result = {}

        for k in self.__class__._sync_properties.keys():
            if attributes and k not in attributes and k != '_sync_owner':
                continue
            if k in dumped:
                result[k] = dumped[k]
            try:
                if self._sync_owner and self._sync_owner is not EphemeralUnflooded:
                    result['_sync_owner'] = str(self._sync_owner)
            except AttributeError: pass
        return result

    @classmethod
    def _sync_pkeys_dict(cls, msg):
        '''Return a dictionary containing the decoded value of all of the primary keys.

        This is a default implementation that works with Pydantic models.
        Subclasses may need to override if they use custom encoders/decoders.
        '''
        d = {}
        if not set(cls.sync_primary_keys).issubset(msg.keys()):
            raise SyncBadEncodingError(
                "Encoding must contain primary keys: {}".format(cls.sync_primary_keys)
            )
        for k in cls.sync_primary_keys:
            # For Pydantic models, we use model_dump() which already handles
            # field serialization, so we get the already-serialized value
            if k in msg:
                d[k] = msg[k]
        return d


# Import at module end to handle circular dependency with StoreInSyncStoreMixin
from .memory import StoreInSyncStoreMixin


# Re-export for convenience
__all__ = [
    'SynchronizableModelMeta',
    'SynchronizableBaseModel',
    'sync_property',
    'no_sync_property',
    '_get_optional_model',
    'PydanticSyncStoreRegistry',
    'class_store_property',
]


def _get_optional_model(cls):
    """Get or build the optional model (all fields Optional with default None).

    The optional model is built lazily on first use and cached.
    Using __base__=cls means the optional model inherits all field-level
    validators, model validators, and FieldInfo metadata from cls.
    """
    if cls in _optional_model_cache:
        return _optional_model_cache[cls]

    # Build overrides: every field becomes Optional[T] with default None
    overrides = {}
    for name, fi in cls.model_fields.items():
        annotation = fi.annotation
        # Convert annotation to Optional[annotation]
        if hasattr(typing, 'get_args'):
            # Check if it's already Optional
            args = typing.get_args(annotation)
            if args and args[0] is type(None):
                # Already Optional or Union with None
                optional_annotation = annotation
            else:
                optional_annotation = Optional[annotation]
        else:
            optional_annotation = Optional[annotation]

        # Create a new FieldInfo that preserves metadata from the original
        # while making it Optional with default None
        new_fi = FieldInfo.from_annotated_attribute(optional_annotation, Field(default=None))
        new_fi = FieldInfo.merge_field_infos(fi, new_fi)
        overrides[name] = (optional_annotation, new_fi)

    # Add sync_primary_keys to the overrides with ClassVar annotation
    # so the metaclass doesn't complain
    if hasattr(cls, 'sync_primary_keys'):
        overrides['sync_primary_keys'] = (
            typing.ClassVar[tuple],
            cls.sync_primary_keys
        )

    # Create the optional model using create_model
    from pydantic import create_model
    optional_cls = typing.cast(
        type[BaseModel],
        create_model(
            cls.__name__ + "_Optional",
            __base__=cls,
            **overrides,
        )
    )

    _optional_model_cache[cls] = optional_cls
    return optional_cls


class class_store_property:
    '''
    A descriptor that exposes a per-class sync store for a given Synchronizable type.

    Typical usage::

        class registry(PydanticSyncStoreRegistry):
            devices = class_store_property(Device)

    Accessing ``registry.devices`` returns the store holding all registered
    ``Device`` objects.  These properties are read-only; assigning to them
    raises ``TypeError``.
    '''

    def __init__(self, target):
        self.target = target
        if not isinstance(target, str):
            if not isinstance(target, type) or not issubclass(target, Synchronizable):
                raise TypeError(
                    f"class_store_property target must be a Synchronizable, got {target!r}"
                )


    def _resolve_target(self, owner_or_instance):
        if isinstance(self.target, str):
            match owner_or_instance:
                case SyncRegistry() as instance:
                    target = instance.registry[self.target]
                case type() as owner:
                    registry = {}
                    for c in reversed(owner.__mro__):
                        if getattr(c, 'class_registry', None):
                            registry.update(c.class_registry)
                    target = registry[self.target]
            if not isinstance(target, type) or not issubclass(target, Synchronizable):
                raise TypeError(
                    f"class_store_property target must be a Synchronizable, got {target!r}"
                )
            self.target = target

    def __set_name__(self, owner, name):
        props = owner.__dict__.get('_class_store_properties')
        if props is None:
            props = {}
            owner._class_store_properties = props
        props[name] = self

    def __get__(self, instance, owner):
        self._resolve_target(owner)
        if instance is None:
            return self
        return instance.store_for_class(self.target)

    def __set__(self, instance, value):
        raise TypeError("class store properties are readonly")


class PydanticSyncStoreRegistry(SyncStoreRegistry, BaseModel):
    '''
    A SyncStoreRegistry that also supports dumping a subset of stored models as a json object.
    '''

    model_config = ConfigDict(ignored_types=(class_store_property,))

    stores_by_class: dict = Field(default_factory=lambda: {}, exclude=True)
    manager: typing.Any = Field(None, exclude=True)
    _class_store_properties: ClassVar[dict[str, class_store_property]] = {}

    def __init__(self, **kwargs):
        # Initialize Pydantic first so __pydantic_fields_set__ exists
        BaseModel.__init__(self, **kwargs)
        stores_by_class = self.stores_by_class
        SyncStoreRegistry.__init__(self)
        self.stores_by_class = stores_by_class

    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        '''
        Equality is identity for registries
        '''
        return other is self

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        merged = {}
        for base in cls.__mro__[:0:-1]:
            merged.update(base.__dict__.get('_class_store_properties', {}))
        if '_class_store_properties' in cls.__dict__:
            merged.update(cls._class_store_properties)
        cls._class_store_properties = merged

    @model_validator(mode='wrap')
    @classmethod
    def _class_store_property_validator(cls, data, handler):
        validated = {}
        if isinstance(data, dict):
            for name, prop in cls._class_store_properties.items():
                if name not in data:
                    continue
                prop._resolve_target(cls)
                values = data.pop(name)
                if not isinstance(values, dict):
                    raise TypeError(
                        f"{cls.__name__}.{name} must be a dict, got {type(values).__name__}"
                    )
                validated[name] = {}
                for key, value in values.items():
                    if not isinstance(key, str):
                        raise TypeError(
                            f"{cls.__name__}.{name} keys must be strings, got {type(key).__name__}"
                        )
                    validated[name][key] = prop.target.model_validate(value)

        instance = handler(data)

        for objs in validated.values():
            for obj in objs.values():
                instance.add_to_store(obj)

        return instance

    @model_serializer(mode='wrap')
    def _class_store_property_serializer(self, handler):
        output_data = handler(self)
        for name in self.__class__._class_store_properties.keys():
            store = getattr(self, name)
            output_data[name] = {k: v.model_dump() for k, v in store.items()}
        # Exclude parent SyncStoreRegistry fields that are inferred as Pydantic fields
        output_data.pop('registry', None)
        output_data.pop('operations', None)
        output_data.pop('sync_store_factory', None)
        return output_data
