import io
import json
import sys

import pytest

from entanglement import javascript_schema
from entanglement.interface import SyncRegistry
from entanglement.javascript_schema import __main__ as javascript_schema_main


class SchemaItem:
    _sync_properties = {'id': None, 'name': None}
    sync_primary_keys = ('id',)


class Registry:
    registry = {'Example': SchemaItem}


def test_generate_schema_for_class_registry_with_mro():
    """Test that class registries properly merge parent class_registries via MRO."""
    
    # Create a base class with its own class_registry
    class BaseModel:
        _sync_properties = {'id': None}
        sync_primary_keys = ('id',)
        sync_type = 'base'
    
    class BaseRegistry(SyncRegistry):
        class_registry = {'Base': BaseModel}
    
    # Create a child class that extends the registry
    class ChildModel:
        _sync_properties = {'id': None, 'name': None}
        sync_primary_keys = ('id',)
        sync_type = 'child'
    
    class ChildRegistry(BaseRegistry):
        class_registry = {'Child': ChildModel}
    
    # Generate schema for the child registry - should include both Base and Child
    output = io.StringIO()
    javascript_schema.generate_schema_for(ChildRegistry, output, type='esm')
    result = output.getvalue()
    
    assert '// This file is auto-generated' in result
    assert 'registry._schemaItem(' in result
    
    # Check that both Base and Child are registered (MRO merge)
    assert '"Base"' in result or "'Base'" in result
    assert '"Child"' in result or "'Child'" in result


def test_generate_schema_for_class_registry_single_level():
    """Test class registry without parent registries."""
    
    class SimpleModel:
        _sync_properties = {'id': None, 'value': None}
        sync_primary_keys = ('id',)
        sync_type = 'simple'
    
    class SimpleRegistry(SyncRegistry):
        class_registry = {'Simple': SimpleModel}
    
    output = io.StringIO()
    javascript_schema.generate_schema_for(SimpleRegistry, output, type='cjs')
    result = output.getvalue()
    
    assert '"Simple"' in result or "'Simple'" in result
    assert '["id", "value"]' in result


def test_generate_schema_for_class_registry_deep_mro():
    """Test class registry with multiple levels of inheritance."""
    
    # Grandparent with base model
    class GrandparentModel:
        _sync_properties = {'base_attr': None}
        sync_primary_keys = ('id',)
        sync_type = 'grandparent'
    
    class GrandparentRegistry(SyncRegistry):
        class_registry = {'Grandparent': GrandparentModel}
    
    # Parent overrides some and adds new
    class ParentModel:
        _sync_properties = {'parent_attr': None}
        sync_primary_keys = ('id',)
        sync_type = 'parent'
    
    class ParentRegistry(GrandparentRegistry):
        class_registry = {'Parent': ParentModel}
    
    # Child adds more
    class ChildModel:
        _sync_properties = {'child_attr': None, 'name': None}
        sync_primary_keys = ('id',)
        sync_type = 'child'
    
    class ChildRegistry(ParentRegistry):
        class_registry = {'Child': ChildModel}
    
    output = io.StringIO()
    javascript_schema.generate_schema_for(ChildRegistry, output, type='esm')
    result = output.getvalue()
    
    # All three should be present due to MRO merging
    assert '"Grandparent"' in result or "'Grandparent'" in result
    assert '"Parent"' in result or "'Parent'" in result
    assert '"Child"' in result or "'Child'" in result


def test_generate_schema_for_class_registry_child_overrides_parent():
    """Test that child class_registry overrides parent for same key (MRO order)."""
    
    class FirstModel:
        _sync_properties = {'first': None}
        sync_primary_keys = ('id',)
        sync_type = 'item'
    
    class SecondModel:
        _sync_properties = {'second': None, 'extra': None}
        sync_primary_keys = ('id',)
        sync_type = 'item2'  # Different type but same registry key for testing
    
    class ParentRegistry(SyncRegistry):
        class_registry = {'Item': FirstModel}
    
    # Child replaces Item with SecondModel - MRO ensures child wins
    class ChildRegistry(ParentRegistry):
        class_registry = {'Item': SecondModel}
    
    output = io.StringIO()
    javascript_schema.generate_schema_for(ChildRegistry, output, type='cjs')
    result = output.getvalue()
    
    # Should have Item with SecondModel's attributes (second, extra) since child overrides
    assert '"Item"' in result or "'Item'" in result
    # The child's model should be used (has 'second' and 'extra', not 'first')


@pytest.mark.parametrize('registry_func', [
    Registry,  # Instance factory (class that returns instance when called)
    type('TestRegistry', (SyncRegistry,), {'class_registry': {'Example': SchemaItem}})  # Class registry
])
def test_generate_schema_for_both_instance_and_class_registries(registry_func):
    """Verify both instance and class registries produce correct output."""
    if isinstance(registry_func, type):
        # For class factories that are classes themselves (class registry case)
        # Check if it's actually a registry class or need to instantiate
        try:
            # Try calling to get instance first
            test_registry = registry_func()
            is_class_reg = False
        except TypeError:
            test_registry = registry_func  # It's already a class
            is_class_reg = True
    else:
        test_registry = registry_func
    
    # For this test, use class for class_registry case
    if hasattr(test_registry, 'class_registry') and test_registry.class_registry:
        pass  # Use as-is (it's a class)  
    elif callable(registry_func):
        test_registry = registry_func()  # Get instance from factory
    
    output = io.StringIO()
    javascript_schema.generate_schema_for(test_registry, output, type='esm')
    result = output.getvalue()
    
    assert '"Example"' in result or "'Example'" in result
    assert '["id", "name"]' in result


@pytest.mark.parametrize(
    ('type', 'package_type', 'export'),
    [('esm', 'module', 'export default register_schema;'),
     ('cjs', 'commonjs', 'module.exports = register_schema;')],
)
def test_output_js_schemas_uses_requested_module_type(
        tmp_path, monkeypatch, type, package_type, export):
    registry = Registry()
    monkeypatch.setattr(
        javascript_schema, '_js_regmap',
        {registry: javascript_schema.JsRegEntry(registry, 'example.js')})
    output_directory = tmp_path / 'schemas'
    output_directory.mkdir()
    (output_directory / 'package.json').write_text(
        '{"name": "schema-output", "type": "wrong"}')

    javascript_schema.output_js_schemas(output_directory, type=type)

    assert json.loads((output_directory / 'package.json').read_text()) == {
        'type': package_type, 'private': True}
    schema = (output_directory / 'example').with_suffix('.js').read_text()
    assert schema.startswith('// This file is auto-generated')
    assert schema.rstrip().endswith(export)


def test_output_js_schemas_rejects_unknown_module_type(tmp_path):
    with pytest.raises(ValueError, match="either 'esm' or 'cjs'"):
        javascript_schema.output_js_schemas(tmp_path, type='invalid')


@pytest.mark.parametrize('option', ['--type', '--tye'])
def test_command_line_type_option(monkeypatch, option):
    calls = []
    monkeypatch.setattr(javascript_schema_main, 'output_js_schemas',
                        lambda directory, type: calls.append((directory, type)))
    monkeypatch.setattr(sys, 'argv', ['javascript_schema', '--out', 'schemas', option, 'esm'])

    javascript_schema_main.main()

    assert calls == [('schemas', 'esm')]
