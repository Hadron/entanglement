# Copyright (C) 2018, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

"""Initial schema

Revision ID: 5370406b7505
Revises: 
Create Date: 2018-03-29 09:48:21.512383

"""
from alembic import op
import sqlalchemy as sa
import entanglement


# revision identifiers, used by Alembic.
revision = '5370406b7505'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.create_table('sync_destinations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('dest_hash', entanglement.util.SqlDestHash(length=60), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('host', sa.String(length=128), nullable=True),
    sa.Column('bw_per_sec', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dest_hash')
    )
    op.create_table('sync_serial',
    sa.Column('serial', sa.Integer(), nullable=False),
    sa.Column('timestamp', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('serial')
    )
    op.create_table('sync_owners',
    sa.Column('id', entanglement.util.GUID(), nullable=False),
    sa.Column('destination_id', sa.Integer(), nullable=True),
    sa.Column('type', sa.String(), nullable=False),
    sa.Column('incoming_serial', sa.Integer(), nullable=False),
    sa.Column('incoming_epoch', sa.DateTime(timezone=True), nullable=False),
    sa.Column('outgoing_epoch', sa.DateTime(timezone=True), nullable=False),
    sa.Column('sync_serial', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['destination_id'], ['sync_destinations.id'], ondelete='cascade'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sync_owners_destination_id'), 'sync_owners', ['destination_id'], unique=False)
    op.create_index(op.f('ix_sync_owners_sync_serial'), 'sync_owners', ['sync_serial'], unique=False)
    op.create_table('sync_deleted',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('sync_type', sa.String(length=128), nullable=False),
    sa.Column('primary_key', sa.TEXT(), nullable=False),
    sa.Column('sync_owner_id', entanglement.util.GUID(), nullable=True),
    sa.Column('sync_serial', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['sync_owner_id'], ['sync_owners.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('sync_deleted_serial_idx', 'sync_deleted', ['sync_owner_id', 'sync_serial'], unique=False)
    ### end Alembic commands ###


def downgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.drop_index('sync_deleted_serial_idx', table_name='sync_deleted')
    op.drop_table('sync_deleted')
    op.drop_index(op.f('ix_sync_owners_sync_serial'), table_name='sync_owners')
    op.drop_index(op.f('ix_sync_owners_destination_id'), table_name='sync_owners')
    op.drop_table('sync_owners')
    op.drop_table('sync_serial')
    op.drop_table('sync_destinations')
    ### end Alembic commands ###
