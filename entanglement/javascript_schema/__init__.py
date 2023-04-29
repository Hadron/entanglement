#!/usr/bin/python3
# Copyright (C) 2017, 2018, 2019, 2020, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import dataclasses, json, os, os.path
from ..interface import SyncRegistry

from enum import Enum, auto

class ModuleFormat(Enum):
    ESM = auto()
    CJS = auto()

def generate_schema_for(registry: SyncRegistry, f, format: ModuleFormat=ModuleFormat.ESM):
    ''':param registry:  A :class:`.SyncRegistry` to output javascript schema for.

    :param f: A file-like object to write the schema to.

    Outputs a Common JS module exporting a function of one parameter
    (a Javascript *SyncRegistry*) against which schema information
    will be imported from the Python *SyncRegistry* class.  The schema
    information includes each class registered with the Python
    registry, class :class:`~interface.SyncProperty` and primary key
    information.  Javascript registries and Python registries do not
    share inheritance information.

    '''

    js = json.dumps

    match format:
        case ModuleFormat.ESM:
            f.write('\nexport default function register_schema(registry) {\n')
        case ModuleFormat.CJS:
            f.write('\nfunction register_schema(registry) {\n')
        case _:
            raise ValueError(f'unsupported module format {format}')

    for name, c in registry.registry.items():
        attributes = list(c._sync_properties.keys())
        keys = c.sync_primary_keys
        f.write( f'\
        registry._schemaItem( {js(name)}, {js(keys)}, {js(attributes)})\n')
    f.write('}\n')

    match format:
        case ModuleFormat.ESM:
            pass
        case ModuleFormat.CJS:
            f.write(f'\nmodule.exports = register_schema;\n')
        case _:
            raise ValueError(f'unsupported module format {format}')


_js_regmap = {}

@dataclasses.dataclass
class JsRegEntry:
    registry: SyncRegistry
    file: str

    
def javascript_registry(registry, file):
    if not file.endswith('.js'):
        file = file+'.js'

    entry = JsRegEntry(registry = registry, file = file)
    _js_regmap[registry] = entry



def output_js_schemas(directory, format: ModuleFormat=ModuleFormat.ESM):
    j = os.path.join
    os.makedirs(directory, exist_ok = True)
    for reg, entry in _js_regmap.items():
        os.makedirs(os.path.dirname(j(directory,entry.file)), exist_ok = True)
        with open(j(directory, entry.file), 'wt') as out:
            generate_schema_for(reg, out, format=format)

    

__all__ = 'output_js_schemas javascript_registry generate_schema_for'.split()
