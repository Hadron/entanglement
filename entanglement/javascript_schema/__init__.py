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

def generate_schema_for(registry: SyncRegistry, f):
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
    f.write('\
export function register_schema(registry) {\n')
    for name, c in registry.registry.items():
        attributes = list(c._sync_properties.keys())
        keys = c.sync_primary_keys
        f.write( f'\
        registry._schemaItem( {js(name)}, {js(keys)}, {js(attributes)})\n')
    f.write('}\n')
    if False:
        f.write(f'''\
module.exports = register_schema;
''')

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



def output_js_schemas(directory):
    j = os.path.join
    os.makedirs(directory, exist_ok = True)
    for reg, entry in _js_regmap.items():
        os.makedirs(os.path.dirname(j(directory,entry.file)), exist_ok = True)
        with open(j(directory, entry.file), 'wt') as out:
            generate_schema_for(reg, out)

    

__all__ = 'output_js_schemas javascript_registry generate_schema_for'.split()
