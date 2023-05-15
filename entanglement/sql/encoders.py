# Copyright (C) 2017, 2020, 2023 Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import base64, datetime, iso8601, uuid
from sqlalchemy import DateTime, DATETIME, Date, BLOB, BINARY, Float, Integer
from ..types import *
from ..util import GUID

register_type(Date, datetime_encoder, datetime_decoder)
register_type(DateTime, datetime_encoder, datetime_decoder)
register_type(DATETIME, datetime_encoder, datetime_decoder)
register_type(BLOB, binary_encoder, binary_decoder)
register_type(BINARY, binary_encoder, binary_decoder)
register_type(GUID, uuid_encoder, uuid_decoder)
register_type(Float, float, float)
register_type(Integer, int, int)
