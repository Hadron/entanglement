# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import datetime, iso8601
from sqlalchemy import DateTime

from datetime import timezone

def datetime_encoder(propname):
    def encode(obj):
        dt = getattr(obj,propname,None)
        if dt is None: return
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat()
    return encode


def datetime_decoder(propname):
    def decode(obj, value):
        return iso8601.parse_date(value)
    return decode

type_map = {}
def register_type(typ, encoder, decoder):
    type_map[type] = {'encoder': encoder,
                      'decoder': decoder}


register_type(DateTime, datetime_encoder, datetime_decoder)
