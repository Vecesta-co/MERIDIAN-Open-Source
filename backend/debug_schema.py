import asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

engine = create_async_engine('sqlite+aiosqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)

async def check():
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = result.fetchall()
        print('Tables:', tables)
        
        result = await conn.execute(text("PRAGMA table_info(eval_definitions)"))
        cols = result.fetchall()
        print('eval_definitions columns:')
        for c in cols:
            print(f'  {c}')
        
        result = await conn.execute(text("PRAGMA table_info(missions)"))
        cols = result.fetchall()
        print('missions columns:')
        for c in cols:
            print(f'  {c}')

asyncio.run(check())