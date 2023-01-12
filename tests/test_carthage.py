# Copyright (C) 2018, 2019, 2020, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, dataclasses, logging, pytest, uuid
from entanglement import *
from entanglement.memory import *
from .utils import settle_loop

from carthage.entanglement import *

@pytest.fixture(scope='module')
def registries():
    return [carthage_registry]


def test_carthage_startup(layout, ainjector, loop):
    server_registry = layout.server.registries[0]
    server_registry.instrument_injector(ainjector.injector)
    settle_loop(loop)
    
logging.getLogger('entanglement.protocol').setLevel(10)
