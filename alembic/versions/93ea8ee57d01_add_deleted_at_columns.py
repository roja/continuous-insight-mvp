"""add_deleted_at_columns

Revision ID: 93ea8ee57d01
Revises: add_deleted_at_columns
Create Date: 2024-10-25 16:51:55.367583

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '93ea8ee57d01'
down_revision: Union[str, None] = 'be2af536c0ca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None                                                                                                                                                                                                                                                      
                                                                                                                                                                                                                                                                            
                                                                                                                                                                                                                                                                             
def upgrade() -> None:                                                                                                                                                                                                                                                      
     # Create new tables with the deleted_at column                                                                                                                                                                                                                          
     with op.batch_alter_table('users') as batch_op:                                                                                                                                                                                                                         
         batch_op.add_column(sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))                                                                                                                                                                             
                                                                                                                                                                                                                                                                             
     with op.batch_alter_table('audits') as batch_op:                                                                                                                                                                                                                        
         batch_op.add_column(sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))    

     with op.batch_alter_table('companies') as batch_op:                                                                                                                                                                                                                         
         batch_op.add_column(sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))                                                                                                                                                                           
                                                                                                                                                                                                                                                                             
                                                                                                                                                                                                                                                                             
def downgrade() -> None:                                                                                                                                                                                                                                                    
     # Remove deleted_at columns                                                                                                                                                                                                                                             
     with op.batch_alter_table('users') as batch_op:                                                                                                                                                                                                                         
         batch_op.drop_column('deleted_at')                                                                                                                                                                                                                                  
                                                                                                                                                                                                                                                                             
     with op.batch_alter_table('audits') as batch_op:                                                                                                                                                                                                                        
         batch_op.drop_column('deleted_at') 

     with op.batch_alter_table('companies') as batch_op:                                                                                                                                                                                                                        
         batch_op.drop_column('deleted_at')