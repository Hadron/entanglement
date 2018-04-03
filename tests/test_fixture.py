# Copyright (C) 2018, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, logging, pytest
from entanglement.sql import sql_sync_declarative_base
from sqlalchemy import Column, Integer
from .utils import settle_loop
Base = sql_sync_declarative_base()

class Foo(Base):
    __tablename__ = 'foo'
    id = Column(Integer, primary_key = True)
    x = Column(Integer, nullable = False)

@pytest.fixture
def registries():
        return [Base]

def test_layout(layout):
        assert layout.server.manager.loop
        t = Foo(x = -201)
        layout.client.session.add(t)
        layout.client.session.commit()
        settle_loop(layout.server.manager.loop)
        
        

#logging.basicConfig(level = 10)
#logging.getLogger('entanglement.protocol').setLevel(10)
