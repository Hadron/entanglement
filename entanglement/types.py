# Copyright (C) 2017, 2020, 2022, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import base64, datetime, iso8601, uuid
from datetime import timezone

def binary_encoder(val):
    return str(base64.b64encode(val), 'utf-8')


def  binary_decoder( val):
    return base64.b64decode(val)


def datetime_encoder(dt):
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def datetime_decoder(value):
    return iso8601.parse_date(value)


type_map = {}

def register_type(typ, encoder, decoder):
    type_map[typ] = {'encoder': encoder,
                      'decoder': decoder}

    

def uuid_encoder(val):
    if val is not None: return str(val)

def uuid_decoder( val):
    if val: return uuid.UUID(val)

register_type(uuid.UUID, uuid_encoder, uuid_decoder)
register_type(datetime.datetime, datetime_encoder, datetime_decoder)

__all__ = [
    'register_type',
    'binary_encoder',
    'binary_decoder',
    'datetime_encoder',
    'datetime_decoder',
    'uuid_encoder',
    'uuid_decoder'
    ]
