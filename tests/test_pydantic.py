#!/usr/bin/python3
# Copyright (C) 2026, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import typing
import pytest

try:
    from pydantic import BaseModel, Field, ValidationError
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    BaseModel = None
    Field = None
    ValidationError = None

from entanglement import SyncRegistry
from entanglement.pydantic import SynchronizableBaseModel, SynchronizableModelMeta, _get_optional_model
from entanglement.memory import StoreInSyncStoreMixin
from entanglement.interface import sync_property
from .conftest import layout_fn


# Skip tests if pydantic is not available
pytestmark = pytest.mark.skipif(not PYDANTIC_AVAILABLE, reason="pydantic not installed")


class MyRegistry(SyncRegistry):
    def __init__(self):
        super().__init__()
        self._registry = {}

    def register_syncable(self, type_name, cls):
        self._registry[type_name] = cls


@pytest.fixture
def my_registry():
    return MyRegistry()


class TestSynchronizableBaseModel:
    """Tests for SynchronizableBaseModel - the Pydantic integration."""

    def test_basic_model_creation(self, my_registry):
        """Test creating a basic Pydantic syncable model."""
        class TestModel(SynchronizableBaseModel):
            sync_registry: typing.ClassVar[SyncRegistry] = my_registry

            id: str = Field(default_factory=lambda: "test-id")
            color: str = Field(default="red")

            sync_primary_keys: typing.ClassVar[tuple] = ('id',)

        assert TestModel is not None
        assert hasattr(TestModel, 'model_fields')
        assert 'id' in TestModel.model_fields
        assert 'color' in TestModel.model_fields
        assert TestModel.sync_primary_keys == ('id',)
        assert TestModel._sync_properties is not None
        assert '_sync_owner' in TestModel._sync_properties

    def test_auto_promotion_of_plain_annotations(self, my_registry):
        """Test that plain annotations are auto-promoted to sync properties."""
        class TestModel(SynchronizableBaseModel):
            sync_registry: typing.ClassVar[SyncRegistry] = my_registry

            id: str
            color: str
            count: int = 5

            sync_primary_keys: typing.ClassVar[tuple] = ('id',)

        # All annotated fields should be in _sync_meta
        assert 'id' in TestModel._sync_meta
        assert 'color' in TestModel._sync_meta
        assert 'count' in TestModel._sync_meta

    def test_field_info_preserved(self, my_registry):
        """Test that FieldInfo metadata is preserved."""
        class TestModel(SynchronizableBaseModel):
            sync_registry: typing.ClassVar[SyncRegistry] = my_registry

            id: str = Field(default="default-id", description="The ID")
            color: str = Field(default="red")

            sync_primary_keys: typing.ClassVar[tuple] = ('id',)

        # Pydantic should preserve FieldInfo
        id_field = TestModel.model_fields['id']
        color_field = TestModel.model_fields['color']

        assert id_field.description == "The ID"
        # Pattern is stored in metadata, not as a direct attribute
        assert id_field.default == "default-id"
        assert color_field.default == "red"

    def test_to_sync_method(self, my_registry):
        """Test the to_sync method."""
        class TestModel(SynchronizableBaseModel):
            sync_registry: typing.ClassVar[SyncRegistry] = my_registry

            id: str = Field(default="test-id")
            color: str = Field(default="red")

            sync_primary_keys: typing.ClassVar[tuple] = ('id',)

        instance = TestModel()
        sync_dict = instance.to_sync()

        assert 'id' in sync_dict
        assert 'color' in sync_dict
        assert '_sync_owner' not in sync_dict  # Not in _sync_properties

    def test_sync_construct(self, my_registry):
        """Test sync_construct method."""
        class TestModel(SynchronizableBaseModel):
            sync_registry: typing.ClassVar[SyncRegistry] = my_registry

            id: str = Field(default="test-id")
            color: str = Field(default="red")

            sync_primary_keys: typing.ClassVar[tuple] = ('id',)

        msg = {'id': 'msg-id', 'color': 'blue'}
        instance = TestModel.sync_construct(msg)

        assert instance.id == 'msg-id'
        assert instance.color == 'blue'

    def test_sync_receive_constructed(self, my_registry):
        """Test sync_receive_constructed method."""
        class TestModel(SynchronizableBaseModel):
            sync_registry: typing.ClassVar[SyncRegistry] = my_registry

            id: str = Field(default="test-id")
            color: str = Field(default="red")
            count: int = Field(default=0)

            sync_primary_keys: typing.ClassVar[tuple] = ('id',)

        # Create instance with sync_construct
        msg1 = {'id': 'msg-id', 'color': 'blue'}
        instance = TestModel.sync_construct(msg1)

        # Now receive additional fields
        msg2 = {'color': 'green', 'count': 42}
        result = instance.sync_receive_constructed(msg2)

        assert result is instance
        assert instance.color == 'green'
        assert instance.count == 42

    def test_sync_primary_keys_validation(self, my_registry):
        """Test that sync_primary_keys is required."""
        # Note: The test checks that a subclass WITHOUT sync_primary_keys fails validation
        # We need to test this by creating a model that does NOT set sync_primary_keys
        with pytest.raises(TypeError, match="must set sync_primary_keys"):
            class MissingKeysModel(SynchronizableBaseModel):
                sync_registry: typing.ClassVar[SyncRegistry] = my_registry
                id: str = Field(default="test")
                # No sync_primary_keys defined

    def test_underscore_fields_not_synced(self, my_registry):
        """Test that underscore-prefixed fields are not auto-promoted."""
        class TestModel(SynchronizableBaseModel):
            sync_registry: typing.ClassVar[SyncRegistry] = my_registry

            id: str = Field(default="test-id")
            _private: str = "private"

            sync_primary_keys: typing.ClassVar[tuple] = ('id',)

        # _private should NOT be in _sync_meta
        assert '_private' not in TestModel._sync_meta

    def test_inheritance_from_store_in_sync_store_mixin(self, my_registry):
        """Test StoreInSyncStoreMixin integration."""
        class SyncableModel(StoreInSyncStoreMixin, SynchronizableBaseModel):
            sync_registry: typing.ClassVar[SyncRegistry] = my_registry

            id: str = Field(default_factory=lambda: "test-id")
            color: str = Field(default="red")

            sync_primary_keys: typing.ClassVar[tuple] = ('id',)
        # Should have both Pydantic and StoreInSyncStoreMixin functionality
        assert hasattr(SyncableModel, 'model_fields')
        assert 'id' in SyncableModel.model_fields
        assert SyncableModel.sync_primary_keys == ('id',)

    # Tests for design divergences (do not fix the code, just verify the divergences)

    def test_optional_model_is_a_class(self, my_registry):
        """Test for divergence 1: _get_optional_model produces a class, not an instance."""
        class Foo(SynchronizableBaseModel):
            sync_registry: typing.ClassVar[SyncRegistry] = my_registry

            id: str
            color: str = 'red'
            age: int = Field(gt=18)

            sync_primary_keys: typing.ClassVar[tuple] = ('id',)

        opt = _get_optional_model(Foo)
        assert isinstance(opt, type), "optional model must be a class, not an instance"
        # Verify it is usable for model_validate
        obj = opt.model_validate({'id': 'x'})
        assert obj.color is None
        with pytest.raises(ValidationError):
            obj = opt.model_validate({'age': 17})

    def test_optional_model_is_classmethod_not_module_function(self, my_registry):
        """Test for divergence 2: _get_optional_model is a classmethod (bug), not a module-level function (design)."""
        class Qux(SynchronizableBaseModel):
            sync_registry: typing.ClassVar[SyncRegistry] = my_registry

            id: str

            sync_primary_keys: typing.ClassVar[tuple] = ('id',)

        assert '_get_optional_model' not in Qux.model_fields

    def test_inherited_sync_primary_keys(self, my_registry):
        """Test for divergence 4: inherited sync_primary_keys does not raise TypeError."""
        class Parent(SynchronizableBaseModel):
            sync_registry: typing.ClassVar[SyncRegistry] = my_registry

            id: str

            sync_primary_keys: typing.ClassVar[tuple] = ('id',)

        # Should not raise — sync_primary_keys is inherited
        class Child(Parent):
            extra: str = 'x'

        assert Child.sync_primary_keys == ('id',)

    def test_sync_property_doc_preserved(self, my_registry):
        """Test for divergence 7: sync_property metadata is not silently replaced."""
        class Baz(SynchronizableBaseModel):
            sync_registry: typing.ClassVar[SyncRegistry] = my_registry

            id: str
            color: str = sync_property(doc="the color of the object")

            sync_primary_keys: typing.ClassVar[tuple] = ('id',)

        sp = Baz._sync_properties['color']
        assert sp.__doc__ == "the color of the object", (
            "sync_property doc was silently replaced by a bare sync_property(); "
            "Loop 1 is stealing the field before the superclass can register it"
        )
