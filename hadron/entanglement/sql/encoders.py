# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import base64, datetime, iso8601, uuid
from sqlalchemy import DateTime, DATETIME, BLOB, BINARY

from datetime import timezone
from ..util import GUID


def binary_encoder(obj, propname):
    val = getattr(obj,propname, None)
    if val is None: return
    return str(base64.b64encode(val), 'utf-8')


def  binary_decoder(obj, propname, val):
    return base64.b64decode(val)


def datetime_encoder(obj, propname):
    dt = getattr(obj,propname,None)
    if dt is None: return
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()



def datetime_decoder(obj, propname, value):
    return iso8601.parse_date(value)


type_map = {}
def register_type(typ, encoder, decoder):
    type_map[typ] = {'encoder': encoder,
                      'decoder': decoder}

    

def uuid_encoder(obj, propname):
    val = getattr(obj, propname, None)
    if val is not None: return str(val)

def uuid_decoder(obj, propname, val):
    if val: return uuid.UUID(val)


register_type(DateTime, datetime_encoder, datetime_decoder)
register_type(DATETIME, datetime_encoder, datetime_decoder)
register_type(BLOB, binary_encoder, binary_decoder)
register_type(BINARY, binary_encoder, binary_decoder)
register_type(GUID, uuid_encoder, uuid_decoder)
